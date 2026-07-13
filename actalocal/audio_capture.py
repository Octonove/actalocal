"""Captura de audio del sistema (loopback WASAPI) y/o microfono a WAV.

Usa `soundcard` (loopback WASAPI nativo de Windows): funciona en cualquier
Windows 10/11 sin "Stereo Mix". Cada pista va a su propio WAV; despues se
mezclan. Best-effort: cualquier fallo se registra y no rompe la app.

RESPALDO (importante): algunos micros USB ('USB PnP Audio Device') reportan un
formato que soundcard no puede abrir (assert de WAVEFORMATEXTENSIBLE) y quedaban
SIN grabar. Cuando pasa, se graba ese micro con FFmpeg/DirectShow, que abre
casi cualquier dispositivo. El mismo bucle calcula el nivel para el VU.

REGLA COM: soundcard inicializa COM (MTA) en el primer hilo que lo importa; si
eso ocurre en el hilo de la UI, congela los dialogos nativos de Tk. Por eso se
carga perezosamente (_load) y SOLO desde hilos de trabajo, y cada hilo que
graba/enumera inicializa COM por su cuenta.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

SAMPLERATE = 48000
BLOCK = 2048

AVAILABLE = (importlib.util.find_spec("soundcard") is not None
             and importlib.util.find_spec("numpy") is not None)

_libs: tuple | None = None
_libs_failed = False
_libs_lock = threading.Lock()


def _load():
    """Importa numpy+soundcard (una vez). SOLO desde hilos de trabajo."""
    global _libs, _libs_failed
    if _libs is not None:
        return _libs
    if _libs_failed:
        return None
    with _libs_lock:
        if _libs is None and not _libs_failed:
            try:
                import numpy as np
                import soundcard as sc
                _libs = (np, sc)
            except Exception as exc:  # noqa: BLE001
                _libs_failed = True
                logger.warning("Captura de audio no disponible: %s", exc)
    return _libs


def _com_init() -> bool:
    """Inicializa COM (MTA) en el hilo actual (WASAPI lo exige). True si hay que
    des-inicializar al terminar."""
    try:
        import ctypes
        RPC_E_CHANGED_MODE = 0x80010106
        hr = ctypes.windll.ole32.CoInitializeEx(None, 0x0) & 0xFFFFFFFF
        return hr != RPC_E_CHANGED_MODE
    except (AttributeError, OSError):
        return False


def _com_uninit() -> None:
    try:
        import ctypes
        ctypes.windll.ole32.CoUninitialize()
    except (AttributeError, OSError):
        pass


def list_microphones() -> list[str]:
    """Nombres de microfonos. SOLO desde hilos de trabajo (carga soundcard)."""
    libs = _load()
    if libs is None:
        return []
    _, sc = libs
    co = _com_init()
    try:
        return [m.name for m in sc.all_microphones(include_loopback=False)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudieron listar microfonos: %s", exc)
        return []
    finally:
        if co:
            _com_uninit()


def find_microphone(sc, name: str, on_fallback=None):
    """Micro por nombre exacto -> subcadena -> predeterminado (avisando)."""
    mics = sc.all_microphones(include_loopback=False)
    m = next((x for x in mics if x.name == name), None)
    if m is None and name:
        m = next((x for x in mics if name in x.name or x.name in name), None)
    if m is None:
        logger.warning("Microfono '%s' no encontrado; predeterminado.", name)
        m = sc.default_microphone()
        if on_fallback:
            on_fallback(m)
    return m


def open_recorder(device, prefer_channels: int | None = None,
                  samplerates: tuple = (SAMPLERATE, 44100), blocksize: int = BLOCK):
    """Abre device.recorder() probando samplerate/canales (micros USB mono
    rechazan la config exacta). Devuelve (recorder_abierto, sr, canales)."""
    prefer = int(prefer_channels or getattr(device, "channels", 2) or 2)
    prefer = max(1, min(2, prefer))
    last_exc: Exception | None = None
    for sr in samplerates:
        for ch in dict.fromkeys((prefer, 1, 2)):
            try:
                rec = device.recorder(samplerate=sr, channels=ch, blocksize=blocksize)
                rec.__enter__()
                return rec, sr, ch
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
    raise last_exc if last_exc else RuntimeError("No se pudo abrir el dispositivo.")


class _Track:
    def __init__(self, wav_path: str, kind: str):
        self.wav_path = wav_path
        self.kind = kind
        self.thread: threading.Thread | None = None
        self.ok = False
        self.peak = 0.0   # nivel reciente (0..1) para el VU


class AudioCapture:
    """Captura system/mic a WAV(s) en hilos; soporta pausa/reanudar y VU."""

    def __init__(self, system: bool, mic_name: str | None, work_dir: str,
                 ffmpeg: str = ""):
        self.system = bool(system) and AVAILABLE
        self.mic_name = mic_name          # el micro tiene respaldo por ffmpeg
        self.work_dir = work_dir
        self.ffmpeg = ffmpeg
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._tracks: list[_Track] = []
        self.problems: list[str] = []

    @property
    def enabled(self) -> bool:
        return self.system or bool(self.mic_name and (AVAILABLE or self.ffmpeg))

    def start(self) -> None:
        if not self.enabled:
            return
        if self.system:
            self._tracks.append(_Track(str(Path(self.work_dir) / ".al_sys.wav"), "system"))
        if self.mic_name:
            self._tracks.append(_Track(str(Path(self.work_dir) / ".al_mic.wav"), "mic"))
        for t in self._tracks:
            t.thread = threading.Thread(target=self._run, args=(t,), daemon=True)
            t.thread.start()

    def _open_device(self, sc, kind: str):
        if kind == "system":
            spk = sc.default_speaker()
            return sc.get_microphone(id=str(spk.name), include_loopback=True)
        return find_microphone(
            sc, self.mic_name or "",
            on_fallback=lambda m: self.problems.append(
                f"El microfono «{self.mic_name}» no aparecio; se grabo con "
                f"«{m.name}» (el predeterminado)."))

    def _run(self, track: _Track) -> None:
        libs = _load()
        if libs is None:
            if track.kind == "mic" and self.ffmpeg:
                self._intentar_dshow(track, None)
            return
        np, sc = libs
        co = _com_init()
        rec = None
        try:
            device = self._open_device(sc, track.kind)
            rec, sr, ch = open_recorder(device)
            wf = wave.open(track.wav_path, "wb")
            wf.setnchannels(ch)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            try:
                while not self._stop.is_set():
                    data = rec.record(numframes=BLOCK)
                    if self._paused.is_set():
                        track.peak = 0.0
                        continue
                    try:
                        track.peak = float(np.abs(data).max())
                    except (ValueError, TypeError):
                        track.peak = 0.0
                    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
                    wf.writeframes(pcm)
            finally:
                wf.close()
            track.ok = Path(track.wav_path).is_file() and Path(track.wav_path).stat().st_size > 1024
        except Exception as exc:  # noqa: BLE001
            logger.warning("Captura de audio (%s) fallo: %s", track.kind, exc)
            track.ok = False
            track.peak = 0.0
            if track.kind == "mic" and self.ffmpeg and not self._stop.is_set():
                self._intentar_dshow(track, exc)   # respaldo FFmpeg/DirectShow
            else:
                self._reportar_fallo(track, exc)
        finally:
            if rec is not None:
                try:
                    rec.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass
            if co:
                _com_uninit()

    def _reportar_fallo(self, track: _Track, exc: Exception | None) -> None:
        nombre = "el audio del sistema" if track.kind == "system" else \
            f"el microfono «{self.mic_name}»"
        detalle = (str(exc).strip() or type(exc).__name__) if exc else "sin detalle"
        self.problems.append(f"No se pudo grabar {nombre}: {detalle}")

    def _intentar_dshow(self, track: _Track, exc_original: Exception | None) -> None:
        try:
            if self._grabar_dshow(track):
                track.ok = True
                logger.info("Micro «%s» grabado via FFmpeg/DirectShow.", self.mic_name)
                return
        except Exception as exc2:  # noqa: BLE001
            logger.warning("Fallback dshow fallo: %s", exc2)
        self._reportar_fallo(track, exc_original)

    def _grabar_dshow(self, track: _Track) -> bool:
        """Graba el micro por FFmpeg -f dshow leyendo PCM crudo: escribe el WAV y
        calcula el nivel (VU) igual que la via nativa."""
        import struct as _struct
        from octonove_core import dshow
        dev = dshow.match_device(self.mic_name or "", dshow.list_audio_devices(self.ffmpeg))
        if not dev:
            logger.warning("dshow: sin dispositivo para «%s»", self.mic_name)
            return False
        ch = 2
        bytes_bloque = BLOCK * ch * 2
        proc = dshow.open_pcm(self.ffmpeg, dev, SAMPLERATE, ch)
        wf = None
        try:
            wf = wave.open(track.wav_path, "wb")
            wf.setnchannels(ch)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLERATE)
            while not self._stop.is_set():
                chunk = proc.stdout.read(bytes_bloque)
                if not chunk:
                    if proc.poll() is not None:
                        break        # ffmpeg termino (dispositivo caido)
                    continue
                if self._paused.is_set():
                    track.peak = 0.0         # se drena pero no se graba
                    continue
                n = len(chunk) - (len(chunk) % 2)
                if n:
                    maxv = max(abs(v) for v in _struct.unpack(f"<{n // 2}h", chunk[:n])) \
                        if n <= 8192 else _peak_np(chunk[:n])
                    track.peak = maxv / 32768.0
                wf.writeframes(chunk)
        finally:
            track.peak = 0.0
            if wf is not None:
                try:
                    wf.close()
                except Exception:  # noqa: BLE001
                    pass
            dshow.stop_pcm(proc)
        return Path(track.wav_path).is_file() and Path(track.wav_path).stat().st_size > 1024

    def peak(self, kind: str) -> float:
        for t in self._tracks:
            if t.kind == kind:
                return t.peak
        return 0.0

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def stop(self) -> list[str]:
        self._stop.set()
        for t in self._tracks:
            if t.thread:
                t.thread.join(timeout=6)
        return [t.wav_path for t in self._tracks if t.ok]

    def cleanup(self) -> None:
        for t in self._tracks:
            try:
                Path(t.wav_path).unlink(missing_ok=True)
            except OSError:
                pass


def _peak_np(pcm: bytes) -> float:
    """Pico de un bloque PCM s16le grande (usa numpy si esta cargado)."""
    libs = _load()
    if libs is None:
        return 0.0
    np, _ = libs
    return float(np.abs(np.frombuffer(pcm, dtype="<i2")).max())
