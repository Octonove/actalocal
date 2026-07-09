"""Transcripcion local con el filtro whisper de FFmpeg.

Flujo: mezcla las pistas WAV (sistema + micro) a un unico audio 16 kHz mono ->
filtro whisper -> SRT -> segmentos con marcas de tiempo. Todo offline.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from . import ffmpeg_utils as fu

logger = logging.getLogger(__name__)


class TranscribeError(Exception):
    pass


def mix_to_wav(ffmpeg_path: str, wavs: list[str], out_wav: str,
               denoise: bool = True) -> str:
    """Mezcla 1..N WAV a un unico WAV mono 16 kHz (lo que espera whisper)."""
    if not wavs:
        raise TranscribeError("No hay pistas de audio que mezclar.")
    cmd = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error"]
    for w in wavs:
        cmd += ["-i", w]
    af = "highpass=f=80,afftdn=nr=12," if denoise else ""
    if len(wavs) == 1:
        graph = f"[0:a]{af}aresample=16000,aformat=channel_layouts=mono[a]"
    else:
        labels = "".join(f"[{i}:a]" for i in range(len(wavs)))
        graph = (f"{labels}amix=inputs={len(wavs)}:duration=longest:normalize=0,"
                 f"{af}aresample=16000,aformat=channel_layouts=mono[a]")
    cmd += ["-filter_complex", graph, "-map", "[a]", "-c:a", "pcm_s16le", out_wav]
    proc = subprocess.run(cmd, capture_output=True, timeout=1800, **fu.subprocess_kwargs())
    if proc.returncode != 0 or not Path(out_wav).is_file():
        raise TranscribeError(fu._decode(proc.stderr)[-400:] or "No se pudo mezclar el audio.")
    return out_wav


def _safe_lang(language: str) -> str:
    lang = (language or "auto").strip().lower()
    return lang if (lang == "auto" or re.match(r"^[a-z]{2,3}$", lang)) else "auto"


def transcribe(ffmpeg_path: str, model_file: str, audio_file: str,
               language: str, out_srt: str, progress_cb=None) -> str:
    """Transcribe audio_file con el filtro whisper. Devuelve el texto SRT.

    cwd = carpeta del modelo + nombres relativos (el parser de filtergraph no
    admite rutas absolutas de Windows con ':')."""
    model_dir = str(Path(model_file).parent)
    model_rel = Path(model_file).name
    tmp_name = f".al_tx_{os.getpid()}.srt"
    tmp_path = Path(model_dir) / tmp_name
    try:
        tmp_path.unlink(missing_ok=True)
    except OSError:
        pass

    total = fu.get_duration(ffmpeg_path, audio_file)
    lang = _safe_lang(language)
    filt = (f"aresample=16000,whisper=model={model_rel}:language={lang}"
            f":use_gpu=false:destination={tmp_name}:format=srt")
    cmd = [ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", "-i", os.path.abspath(audio_file),
           "-af", filt, "-f", "null", "-"]
    # stderr a un fichero temporal (no a DEVNULL): si falla, asi tenemos el motivo
    # real de FFmpeg. No se puede leer en el bucle (lee stdout) sin riesgo de deadlock.
    err_path = Path(model_dir) / f".al_tx_{os.getpid()}.log"
    err_f = None
    try:
        err_f = open(err_path, "w+", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(cmd, cwd=model_dir, stdout=subprocess.PIPE,
                                    stderr=err_f, text=True, **fu.subprocess_kwargs())
        except OSError as exc:
            raise TranscribeError(f"No se pudo iniciar FFmpeg: {exc}") from exc
        if proc.stdout is not None:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms=") and total > 0 and progress_cb:
                    try:
                        sec = int(line.split("=", 1)[1]) / 1_000_000
                        progress_cb(min(0.99, sec / total))
                    except ValueError:
                        pass
        proc.wait()
        if proc.returncode != 0 or not tmp_path.is_file():
            tail = ""
            try:
                err_f.flush()
                err_f.seek(0)
                tail = err_f.read()[-500:].strip()
            except OSError:
                pass
            logger.warning("Whisper fallo (rc=%s): %s", proc.returncode, tail)
            raise TranscribeError("La transcripcion fallo." + (f"\n\n{tail}" if tail else ""))
        Path(out_srt).parent.mkdir(parents=True, exist_ok=True)
        try:
            Path(out_srt).unlink(missing_ok=True)
            os.replace(str(tmp_path), out_srt)
        except OSError as exc:
            raise TranscribeError(f"No se pudo guardar la transcripcion: {exc}") from exc
        if progress_cb:
            progress_cb(1.0)
        return Path(out_srt).read_text(encoding="utf-8", errors="replace")
    finally:
        if err_f is not None:
            try:
                err_f.close()
            except OSError:
                pass
        for p in (tmp_path, err_path):
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _to_sec(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(srt_text: str) -> list[dict]:
    """SRT -> [{start, end, text}] (segundos)."""
    segs: list[dict] = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        m = None
        text_lines = []
        for ln in lines:
            mm = _TS.search(ln)
            if mm and m is None:
                m = mm
            elif not ln.strip().isdigit():
                text_lines.append(ln.strip())
        if not m:
            continue
        start = _to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
        text = " ".join(text_lines).strip()
        if text:
            segs.append({"start": start, "end": end, "text": text})
    return segs


def plain_text(segments: list[dict]) -> str:
    return " ".join(s["text"] for s in segments).strip()


def timestamped_text(segments: list[dict]) -> str:
    out = []
    for s in segments:
        mm = int(s["start"] // 60)
        ss = int(s["start"] % 60)
        out.append(f"[{mm:02d}:{ss:02d}] {s['text']}")
    return "\n".join(out)
