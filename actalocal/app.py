"""Ventana principal de ActaLocal."""

from __future__ import annotations

import logging
import os
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from . import APP_NAME, APP_VERSION, theme
from . import ffmpeg_utils as fu
from . import audio_capture as ac
from . import models, transcribe, minutes, llm
from .config import (AppConfig, load_config, save_config, work_dir)

logger = logging.getLogger(__name__)

LANGS = [("Espanol", "es"), ("Ingles", "en"), ("Frances", "fr"), ("Aleman", "de"),
         ("Italiano", "it"), ("Portugues", "pt"), ("Detectar", "auto")]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1000x720")
        self.minsize(900, 640)
        theme.apply(self)
        try:
            ico = Path(__file__).resolve().parent.parent / "build" / "icon.ico"
            if ico.is_file():
                self.iconbitmap(str(ico))
        except tk.TclError:
            pass

        self.cfg: AppConfig = load_config()
        self.ffmpeg = fu.find_ffmpeg()
        self._whisper_ok: bool | None = None   # cache de fu.has_whisper (calculo perezoso)
        self.mics = ac.list_microphones()

        self.cap: ac.AudioCapture | None = None
        self._state = "idle"        # idle | recording | processing | done
        self._rec_t0: float | None = None
        self._paused_accum = 0.0
        self._timer_id: str | None = None
        self._poll_id: str | None = None
        self._closing = False
        self.last_wavs: list[str] = []
        self.last_segments: list[dict] = []
        self.last_minutes: dict = {}
        self.last_outputs: dict[str, str] = {}
        self.last_refined = ""
        self.last_refined_changed = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self._first_run_check)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        theme.header(self, APP_NAME, "Reuniones a acta · transcripcion y resumen 100% en tu PC")
        self.status = theme.status_bar(self, "Listo.")

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # --- Columna izquierda: controles ---
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        self._build_controls(left)

        # --- Columna derecha: resultados ---
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        self._build_results(right)

    def _build_controls(self, parent: ttk.Frame) -> None:
        rec = ttk.LabelFrame(parent, text="🎙  Grabacion", padding=12)
        rec.pack(fill="x")

        self.btn_rec = ttk.Button(rec, text="●  Grabar reunion", style="Rec.TButton",
                                  command=self._toggle_record)
        self.btn_rec.pack(fill="x")
        row = ttk.Frame(rec); row.pack(fill="x", pady=(8, 0))
        self.btn_pause = ttk.Button(row, text="⏸ Pausa", command=self._toggle_pause,
                                    state="disabled")
        self.btn_pause.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.lbl_timer = ttk.Label(rec, text="00:00", style="Big.TLabel")
        self.lbl_timer.pack(pady=(10, 2))

        # niveles
        self.vu = tk.Canvas(rec, width=210, height=34, bg=theme.CARD, highlightthickness=0)
        self.vu.pack()
        self._draw_levels(0.0, 0.0)

        src = ttk.LabelFrame(parent, text="🔊  Fuentes de audio", padding=12)
        src.pack(fill="x", pady=(12, 0))
        self.var_sys = tk.BooleanVar(value=self.cfg.audio_system and ac.AVAILABLE)
        ttk.Checkbutton(src, text="Audio del sistema (otros participantes)",
                        variable=self.var_sys, command=self._on_setting).pack(anchor="w")
        self.var_mic = tk.BooleanVar(value=self.cfg.audio_mic and ac.AVAILABLE)
        ttk.Checkbutton(src, text="Microfono (tu voz)", variable=self.var_mic,
                        command=self._on_setting).pack(anchor="w", pady=(2, 4))
        self.var_micdev = tk.StringVar(value=self.cfg.audio_mic_device)
        mic_vals = self.mics or ["(sin microfonos)"]
        self.cmb_mic = ttk.Combobox(src, textvariable=self.var_micdev, values=mic_vals,
                                    state="readonly", width=26)
        if self.cfg.audio_mic_device in self.mics:
            self.cmb_mic.set(self.cfg.audio_mic_device)
        elif self.mics:
            self.cmb_mic.current(0)
        self.cmb_mic.pack(fill="x")
        self.var_denoise = tk.BooleanVar(value=self.cfg.denoise)
        ttk.Checkbutton(src, text="Reducir ruido de fondo", variable=self.var_denoise,
                        command=self._on_setting).pack(anchor="w", pady=(6, 0))
        if not ac.AVAILABLE:
            ttk.Label(src, text="⚠ Captura de audio no disponible en este equipo.",
                      style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        ia = ttk.LabelFrame(parent, text="🧠  Transcripcion e IA", padding=12)
        ia.pack(fill="x", pady=(12, 0))
        ttk.Label(ia, text="Idioma", style="CardMuted.TLabel").pack(anchor="w")
        self.var_lang = tk.StringVar()
        self.cmb_lang = ttk.Combobox(ia, state="readonly", width=26,
                                     values=[n for n, _ in LANGS])
        self.cmb_lang.current(next((i for i, (_, c) in enumerate(LANGS)
                                    if c == self.cfg.language), 0))
        self.cmb_lang.pack(fill="x", pady=(0, 6))
        ttk.Label(ia, text="Modelo Whisper (mas grande = mejor texto)",
                  style="CardMuted.TLabel").pack(anchor="w")
        self.var_model = tk.StringVar(value=self.cfg.whisper_model)
        self.cmb_model = ttk.Combobox(ia, textvariable=self.var_model, state="readonly",
                                      width=26, values=[models.label(k) for k in models.ORDER])
        self.cmb_model.set(models.label(self.cfg.whisper_model)
                           if self.cfg.whisper_model in models.MODELS else models.label("base"))
        self.cmb_model.pack(fill="x")
        rec = models.recommend_model(llm.system_ram_gb())
        ttk.Label(ia, text=f"Sugerido para tu PC: {rec}", style="CardMuted.TLabel").pack(
            anchor="w", pady=(2, 4))
        self.var_refine = tk.BooleanVar(value=self.cfg.refine_with_ollama)
        ttk.Checkbutton(ia, text="Pulir transcripcion con IA (Ollama)",
                        variable=self.var_refine, command=self._on_setting).pack(anchor="w")
        self.lbl_ia = ttk.Label(ia, text="", style="CardMuted.TLabel", wraplength=220,
                                justify="left")
        self.lbl_ia.pack(anchor="w", pady=(6, 2))
        ttk.Button(ia, text="Configurar IA (Ollama)…", command=self._ollama_dialog).pack(
            anchor="w", pady=(2, 0))
        self._refresh_ia_label()

        meta = ttk.LabelFrame(parent, text="📝  Datos de la reunion", padding=12)
        meta.pack(fill="x", pady=(12, 0))
        ttk.Label(meta, text="Titulo", style="CardMuted.TLabel").pack(anchor="w")
        self.var_title = tk.StringVar(value="Reunion")
        ttk.Entry(meta, textvariable=self.var_title).pack(fill="x", pady=(0, 6))
        ttk.Label(meta, text="Asistentes (opcional)", style="CardMuted.TLabel").pack(anchor="w")
        self.var_attendees = tk.StringVar()
        ttk.Entry(meta, textvariable=self.var_attendees).pack(fill="x")

    def _build_results(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent); top.pack(fill="x")
        ttk.Label(top, text="Resultado", style="H.TLabel").pack(side="left")
        self.pb = ttk.Progressbar(top, mode="determinate", length=220)
        self.pb.pack(side="right")

        self.nb = ttk.Notebook(parent)
        self.nb.pack(fill="both", expand=True, pady=(8, 8))
        self.txt_acta = self._make_text_tab("Acta")
        self.txt_trans = self._make_text_tab("Transcripcion")
        self._set_text(self.txt_acta,
                       "Aqui aparecera el acta cuando termines de grabar (o importes un audio).\n\n"
                       "1. Elige las fuentes de audio.\n2. Pulsa 'Grabar reunion'.\n"
                       "3. Al parar, ActaLocal transcribe y redacta el acta automaticamente.")

        bar = ttk.Frame(parent); bar.pack(fill="x")
        self.btn_import = ttk.Button(bar, text="Abrir un audio/video…", command=self._import_media)
        self.btn_import.pack(side="left")
        self.btn_html = ttk.Button(bar, text="Exportar HTML", command=lambda: self._export("html"),
                                   state="disabled")
        self.btn_html.pack(side="right", padx=(6, 0))
        self.btn_md = ttk.Button(bar, text="Markdown", command=lambda: self._export("md"),
                                 state="disabled")
        self.btn_md.pack(side="right", padx=(6, 0))
        self.btn_txt = ttk.Button(bar, text="TXT", command=lambda: self._export("txt"),
                                  state="disabled")
        self.btn_txt.pack(side="right", padx=(6, 0))
        self.btn_srt = ttk.Button(bar, text="SRT", command=lambda: self._export("srt"),
                                  state="disabled")
        self.btn_srt.pack(side="right", padx=(6, 0))
        self.btn_folder = ttk.Button(bar, text="Abrir carpeta", command=self._open_folder)
        self.btn_folder.pack(side="right", padx=(6, 0))

    def _make_text_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.nb, padding=2)
        self.nb.add(frame, text=title)
        txt = tk.Text(frame, wrap="word", font=(theme.FONT, 11), bg=theme.WHITE,
                      fg=theme.TEXT, relief="flat", padx=12, pady=10, spacing3=4)
        sb = ttk.Scrollbar(frame, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.configure(state="disabled")
        return txt

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    # -------------------------------------------------------------- niveles
    def _draw_levels(self, sys_lvl: float, mic_lvl: float) -> None:
        c = self.vu
        try:
            c.delete("all")
        except tk.TclError:
            return
        for i, (lab, lvl) in enumerate((("Sis", sys_lvl), ("Mic", mic_lvl))):
            y = 4 + i * 16
            c.create_text(2, y + 5, text=lab, anchor="w", fill=theme.MUTED,
                          font=(theme.FONT, 7))
            x0 = 26
            w = 180
            c.create_rectangle(x0, y, x0 + w, y + 10, outline=theme.BORDER, fill=theme.SURFACE)
            fill_w = int(w * max(0.0, min(1.0, lvl)))
            col = theme.SUCCESS if lvl < 0.85 else theme.DANGER
            if fill_w > 0:
                c.create_rectangle(x0, y, x0 + fill_w, y + 10, outline="", fill=col)

    def _poll_levels(self) -> None:
        if self._state != "recording" or not self.cap or self._closing:
            return
        self._draw_levels(self.cap.peak("system"), self.cap.peak("mic"))
        self._poll_id = self.after(80, self._poll_levels)

    def _cancel_timers(self) -> None:
        for attr in ("_timer_id", "_poll_id"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    self.after_cancel(tid)
                except (tk.TclError, ValueError):
                    pass
                setattr(self, attr, None)

    # -------------------------------------------------------------- grabar
    def _model_key(self) -> str:
        i = self.cmb_model.current()
        if 0 <= i < len(models.ORDER):
            return models.ORDER[i]
        return self.cfg.whisper_model or "base"

    def _on_setting(self, *_a) -> None:
        self.cfg.audio_system = bool(self.var_sys.get())
        self.cfg.audio_mic = bool(self.var_mic.get())
        if self.var_micdev.get() in self.mics:
            self.cfg.audio_mic_device = self.var_micdev.get()
        self.cfg.denoise = bool(self.var_denoise.get())
        self.cfg.refine_with_ollama = bool(self.var_refine.get())
        save_config(self.cfg)

    def _set_status(self, text: str) -> None:
        try:
            self.status.config(text=text)
        except tk.TclError:
            pass

    def _toggle_record(self) -> None:
        if self._state == "recording":
            self._stop_record()
            return
        if self._state == "processing":
            return
        if not ac.AVAILABLE:
            messagebox.showerror(APP_NAME, "La captura de audio no esta disponible en este equipo.")
            return
        if not (self.var_sys.get() or self.var_mic.get()):
            messagebox.showwarning(APP_NAME, "Activa al menos una fuente de audio (sistema o microfono).")
            return
        mic_name = self.var_micdev.get() if (self.var_mic.get() and self.mics) else None
        self.cap = ac.AudioCapture(self.var_sys.get(), mic_name, str(work_dir()))
        self.cap.start()
        self._state = "recording"
        self._rec_t0 = time.time()
        self._paused_accum = 0.0
        self.btn_rec.config(text="⏹  Detener y generar acta")
        self.btn_pause.config(state="normal", text="⏸ Pausa")
        self.btn_import.config(state="disabled")
        self._set_status("Grabando… (todo se queda en tu PC)")
        self._tick_timer()
        self._poll_levels()

    def _toggle_pause(self) -> None:
        if self._state != "recording" or not self.cap:
            return
        if self._timer_id:                       # evita timers solapados
            try:
                self.after_cancel(self._timer_id)
            except (tk.TclError, ValueError):
                pass
            self._timer_id = None
        if self.cap.paused:
            self.cap.resume()
            self._rec_t0 = time.time()
            self.btn_pause.config(text="⏸ Pausa")
            self._set_status("Grabando…")
            self._tick_timer()
        else:
            self.cap.pause()
            if self._rec_t0:
                self._paused_accum += time.time() - self._rec_t0
            self.btn_pause.config(text="▶ Reanudar")
            self._set_status("En pausa.")

    def _tick_timer(self) -> None:
        if self._state != "recording" or not self.cap or self.cap.paused:
            return
        elapsed = self._paused_accum + (time.time() - (self._rec_t0 or time.time()))
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        self.lbl_timer.config(text=(f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"))
        self._timer_id = self.after(500, self._tick_timer)

    def _stop_record(self) -> None:
        if not self.cap:
            return
        self._set_status("Deteniendo grabacion…")
        wavs = self.cap.stop()
        self._state = "idle"
        self._cancel_timers()
        self.btn_rec.config(text="●  Grabar reunion")
        self.btn_pause.config(state="disabled")
        self._draw_levels(0.0, 0.0)
        if not wavs:
            messagebox.showwarning(APP_NAME, "No se capturo audio. Revisa las fuentes seleccionadas.")
            self.btn_import.config(state="normal")
            return
        self.last_wavs = wavs
        self._start_processing(wavs, is_recording=True)

    # ----------------------------------------------------------- importar
    def _import_media(self) -> None:
        if self._state in ("recording", "processing"):
            return
        p = filedialog.askopenfilename(
            title="Elige un audio o video de la reunion",
            filetypes=[("Audio/Video", "*.wav *.mp3 *.m4a *.aac *.ogg *.flac *.mp4 *.mkv *.mov *.webm")])
        if p:
            self._start_processing([p], is_recording=False)

    # --------------------------------------------------------- procesar
    def _whisper_supported(self) -> bool:
        """True si el FFmpeg localizado trae el filtro whisper. Se calcula una sola
        vez (spawnea ffmpeg -filters, ~1 s) y se cachea."""
        if self._whisper_ok is None:
            self._whisper_ok = bool(self.ffmpeg) and fu.has_whisper(self.ffmpeg)
        return self._whisper_ok

    def _start_processing(self, inputs: list[str], *, is_recording: bool) -> None:
        if not self.ffmpeg:
            messagebox.showerror(APP_NAME, "No se encontro FFmpeg (necesario para transcribir).")
            return
        # Sin el filtro whisper, la transcripcion fallaria a mitad con un error
        # criptico de FFmpeg. Mejor detectarlo ya y explicar como resolverlo.
        if not self._whisper_supported():
            messagebox.showerror(
                APP_NAME,
                "El FFmpeg encontrado no incluye el filtro «whisper», necesario para "
                "transcribir.\n\nInstala el build COMPLETO (full) de FFmpeg (por ejemplo "
                "el de Gyan, que trae whisper) y vuelve a intentarlo. La version "
                "«essentials» no sirve.")
            return
        key = self._model_key()   # honra el modelo elegido por el usuario
        if not models.is_downloaded(key):
            if not messagebox.askyesno(
                    APP_NAME, f"Para transcribir hay que descargar una sola vez el modelo "
                    f"Whisper '{key}' ({models.MODELS[key][1]} MB).\n\nDescargar ahora?"):
                return
        lang = next((c for n, c in LANGS if n == self.cmb_lang.get()), "es")
        self.cfg.language = lang
        self.cfg.whisper_model = key
        save_config(self.cfg)
        # La deteccion de Ollama es una llamada HTTP: se hace en el worker, no aqui
        # (en el hilo de UI podia congelar la ventana un instante).
        refine_requested = bool(self.var_refine.get())
        # estado de pulido limpio para esta ejecucion
        self.last_refined = ""
        self.last_refined_changed = False
        title = self.var_title.get().strip() or "Reunion"
        attendees = self.var_attendees.get().strip()
        denoise = bool(self.var_denoise.get())

        self._state = "processing"
        self.btn_rec.config(state="disabled")
        self.btn_import.config(state="disabled")
        self._set_progress(0.02)
        self._set_status("Preparando…")

        t = threading.Thread(target=self._process_worker,
                             args=(inputs, key, lang, denoise, refine_requested,
                                   title, attendees, is_recording),
                             daemon=True)
        t.start()

    def _process_worker(self, inputs, key, lang, denoise, refine_requested,
                        title, attendees, is_recording=False) -> None:
        mixed = str(work_dir() / f".al_mixed_{os.getpid()}.wav")
        srt_path = str(work_dir() / f".al_{os.getpid()}.srt")
        try:
            if self._closing:
                return
            # Deteccion de Ollama en el hilo. Si el modelo configurado ya no existe,
            # esta ejecucion usa autodeteccion (model=None) y se avisa via UI.
            use_ollama = llm.available()
            model = self.cfg.ollama_model or None
            if use_ollama and model and model not in llm.list_models():
                stale, model = model, None

                def _warn_stale(stale=stale):
                    messagebox.showwarning(APP_NAME, f"El modelo Ollama '{stale}' ya no "
                                           "esta disponible. Se usara la deteccion automatica.")
                    self.cfg.ollama_model = ""
                    llm.set_model(None)
                    save_config(self.cfg)
                self._ui(_warn_stale)
            do_refine = refine_requested and use_ollama
            # 1) modelo
            self._ui(lambda: self._set_status("Comprobando el modelo de IA…"))
            if not models.is_downloaded(key):
                models.download(key, lambda p: self._ui(lambda: self._set_progress(0.02 + p * 0.13)))
            model_file = str(models.model_path(key))
            if self._closing:
                return

            # 2) preparar audio (mezcla) -> wav 16k mono
            self._ui(lambda: (self._set_status("Preparando el audio…"), self._set_progress(0.16)))
            transcribe.mix_to_wav(self.ffmpeg, list(inputs), mixed, denoise=denoise)
            if self._closing:
                return

            # 3) transcribir
            self._ui(lambda: self._set_status("Transcribiendo (Whisper, en local)…"))
            srt_text = transcribe.transcribe(
                self.ffmpeg, model_file, mixed, lang, srt_path,
                progress_cb=lambda p: self._ui(lambda: self._set_progress(0.18 + p * 0.7)))
            segments = transcribe.parse_srt(srt_text)
            text = transcribe.plain_text(segments)
            if not text:
                raise transcribe.TranscribeError("No se detecto voz en el audio.")

            # 3b) pulir transcripcion con IA (opcional)
            refined = text
            if do_refine and not self._closing:
                self._ui(lambda: (self._set_status("Puliendo la transcripcion con IA…"),
                                  self._set_progress(0.9)))
                refined = minutes.refine_transcript(text, model=model)

            # 4) acta (sobre el texto pulido si lo hay)
            self._ui(lambda: (self._set_status("Redactando el acta…"), self._set_progress(0.94)))
            mins = minutes.build_minutes(refined, use_ollama=use_ollama, model=model)

            refined_changed = refined.strip() != text.strip()
            self._ui(lambda: self._finish_processing(segments, srt_text, mins, title,
                                                     attendees, refined, refined_changed))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Procesado fallo")
            self._ui(lambda: self._fail_processing(str(exc)))
        finally:
            # PRIVACIDAD: no dejamos audio ni transcripcion en bruto en el disco.
            # El .srt final ya se ha leido a memoria (y se exporta aparte a peticion).
            leftovers = [mixed, srt_path]
            if is_recording:
                # Solo borramos las pistas grabadas por la app, nunca los ficheros
                # que el usuario importa desde su propia carpeta.
                leftovers += list(inputs)
            for f in leftovers:
                try:
                    Path(f).unlink(missing_ok=True)
                except OSError:
                    pass

    def _ui(self, fn) -> None:
        if self._closing:
            return
        try:
            self.after(0, fn)
        except (RuntimeError, tk.TclError) as exc:
            logger.debug("UI callback descartado (ventana cerrada): %s", exc)

    def _set_progress(self, frac: float) -> None:
        try:
            self.pb["value"] = max(0, min(100, frac * 100))
        except tk.TclError:
            pass

    def _finish_processing(self, segments, srt_text, mins, title, attendees,
                          refined_text="", refined_changed=False) -> None:
        self.last_segments = segments
        self.last_srt = srt_text
        self.last_minutes = mins
        self.last_refined = refined_text
        self.last_refined_changed = refined_changed
        self.meta = {"title": title, "attendees": attendees,
                     "date": datetime.now().strftime("%d/%m/%Y %H:%M")}
        self._render_results()
        self._set_progress(1.0)
        self._state = "done"
        self.btn_rec.config(state="normal")
        self.btn_import.config(state="normal")
        for b in (self.btn_html, self.btn_md, self.btn_txt, self.btn_srt):
            b.config(state="normal")
        self._set_status(f"Acta lista ({mins.get('fuente', '')}). Revisa y exporta.")
        # autoguardado del HTML
        try:
            self._export("html", silent=True)
        except Exception:  # noqa: BLE001
            pass

    def _fail_processing(self, msg: str) -> None:
        self._state = "idle"
        self.btn_rec.config(state="normal")
        self.btn_import.config(state="normal")
        self._set_progress(0.0)
        self._set_status("No se pudo generar el acta.")
        messagebox.showerror(APP_NAME, f"No se pudo procesar:\n\n{msg}")

    def _render_results(self) -> None:
        m = self.last_minutes
        lines = []
        if m.get("resumen"):
            lines += ["RESUMEN", "", m["resumen"], ""]
        if m.get("puntos"):
            lines += ["PUNTOS PRINCIPALES", ""] + [f"  • {p}" for p in m["puntos"]] + [""]
        if m.get("decisiones"):
            lines += ["DECISIONES", ""] + [f"  • {d}" for d in m["decisiones"]] + [""]
        if m.get("tareas"):
            lines += ["TAREAS / ACCIONES", ""] + [f"  ☐ {t}" for t in m["tareas"]] + [""]
        self._set_text(self.txt_acta, "\n".join(lines) or "(sin contenido)")
        # Transcripcion: si se pulio con IA, mostramos el texto limpio; si no, el
        # texto con marcas de tiempo (timestamps exactos del audio).
        if getattr(self, "last_refined_changed", False) and getattr(self, "last_refined", ""):
            self._set_text(self.txt_trans,
                           "Transcripcion pulida con IA:\n\n" + self.last_refined)
        else:
            self._set_text(self.txt_trans, transcribe.timestamped_text(self.last_segments))

    # ----------------------------------------------------------- exportar
    def _export(self, fmt: str, silent: bool = False) -> str | None:
        if not self.last_minutes and fmt != "srt":
            return None
        meta = getattr(self, "meta", {"title": "Reunion", "attendees": "",
                                      "date": datetime.now().strftime("%d/%m/%Y %H:%M")})
        safe = "".join(ch for ch in meta["title"] if ch.isalnum() or ch in " -_").strip() or "Reunion"
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        outdir = Path(self.cfg.recordings_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        # Coincide con lo que se muestra: si se pulio con IA, se exporta el texto pulido.
        if getattr(self, "last_refined_changed", False) and getattr(self, "last_refined", ""):
            trans = self.last_refined
        else:
            trans = transcribe.timestamped_text(self.last_segments)
        try:
            if fmt == "html":
                path = outdir / f"{safe}_{stamp}.html"
                path.write_text(minutes.to_html(self.last_minutes, title=meta["title"],
                                date_str=meta["date"], attendees=meta["attendees"],
                                transcript=trans), encoding="utf-8")
            elif fmt == "md":
                path = outdir / f"{safe}_{stamp}.md"
                path.write_text(minutes.to_markdown(self.last_minutes, title=meta["title"],
                                date_str=meta["date"], attendees=meta["attendees"],
                                transcript=trans), encoding="utf-8")
            elif fmt == "txt":
                path = outdir / f"{safe}_{stamp}.txt"
                path.write_text(minutes.to_txt(self.last_minutes, title=meta["title"],
                                date_str=meta["date"], attendees=meta["attendees"],
                                transcript=trans), encoding="utf-8")
            elif fmt == "srt":
                path = outdir / f"{safe}_{stamp}.srt"
                path.write_text(getattr(self, "last_srt", ""), encoding="utf-8")
            else:
                return None
        except OSError as exc:
            if not silent:
                messagebox.showerror(APP_NAME, f"No se pudo exportar:\n{exc}")
            return None
        self.last_outputs[fmt] = str(path)
        if not silent:
            self._set_status(f"Exportado: {path}")
            if fmt == "html" and messagebox.askyesno(APP_NAME, f"Guardado:\n{path}\n\nAbrir ahora?"):
                webbrowser.open(path.as_uri())
        return str(path)

    def _open_folder(self) -> None:
        try:
            os.startfile(self.cfg.recordings_dir)
        except OSError:
            pass

    # ----------------------------------------------------------- Ollama
    def _refresh_ia_label(self) -> None:
        if llm.available():
            mdl = self.cfg.ollama_model or llm.default_model() or "?"
            self.lbl_ia.config(text=f"✓ IA avanzada activa (Ollama: {mdl}). Actas mejor redactadas.")
        else:
            self.lbl_ia.config(text="Sin Ollama: el acta se genera con heuristicas locales "
                               "(funciona, pero menos pulida).")

    def _ollama_dialog(self) -> None:
        win = tk.Toplevel(self)
        theme.center_window(win)
        win.title("Configurar IA local (Ollama)")
        win.configure(bg=theme.BG)
        win.transient(self)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=18)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="IA local opcional con Ollama", style="H.TLabel").pack(anchor="w")
        ttk.Label(frm, text="Ollama es gratis y hace que ActaLocal redacte actas mucho mejores,\n"
                  "sin que nada salga de tu PC. Es totalmente opcional.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(2, 10))

        ram = llm.system_ram_gb()
        gpu = llm.has_gpu()
        rec_model, size, motivo = llm.recommend_model(ram, gpu)
        info = ttk.LabelFrame(frm, text="Tu equipo", padding=10)
        info.pack(fill="x")
        ttk.Label(info, text=f"RAM: {ram:.0f} GB  ·  GPU NVIDIA: {'si' if gpu else 'no detectada'}",
                  style="CardMuted.TLabel").pack(anchor="w")
        ttk.Label(info, text=f"Modelo recomendado: {rec_model} ({size})\n{motivo}",
                  style="CardMuted.TLabel", justify="left").pack(anchor="w", pady=(4, 0))

        if llm.available():
            mods = llm.list_models()
            ttk.Label(frm, text="Modelo a usar:", style="H.TLabel").pack(anchor="w", pady=(12, 2))
            var = tk.StringVar(value=self.cfg.ollama_model or (llm.default_model() or ""))
            cmb = ttk.Combobox(frm, textvariable=var, values=mods, state="readonly", width=34)
            cmb.pack(anchor="w")

            def save_model():
                self.cfg.ollama_model = var.get()
                save_config(self.cfg)
                llm.set_model(var.get() or None)
                self._refresh_ia_label()
                win.destroy()
            ttk.Button(frm, text="Guardar", style="Primary.TButton", command=save_model).pack(
                anchor="e", pady=(12, 0))
        else:
            guide = ttk.LabelFrame(frm, text="Como activarla (5 min, una vez)", padding=10)
            guide.pack(fill="x", pady=(12, 0))
            steps = (f"1. Descarga Ollama gratis: https://ollama.com\n"
                     f"2. Abre una terminal (tecla Windows, escribe 'cmd', Enter).\n"
                     f"3. Pega este comando y pulsa Enter (descarga el modelo recomendado):\n")
            ttk.Label(guide, text=steps, style="CardMuted.TLabel", justify="left").pack(anchor="w")
            cmd = f"ollama run {rec_model}"
            ent = ttk.Entry(guide, width=34)
            ent.insert(0, cmd)
            ent.configure(state="readonly")
            ent.pack(side="left", pady=(2, 0))

            def copy_cmd():
                self.clipboard_clear()
                self.clipboard_append(cmd)
                self._set_status("Comando copiado al portapapeles.")
            ttk.Button(guide, text="Copiar", command=copy_cmd).pack(side="left", padx=6, pady=(2, 0))
            ttk.Label(frm, text="Cuando termine, vuelve a abrir esta ventana: ActaLocal lo detecta solo.",
                      style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
            ttk.Button(frm, text="Abrir ollama.com", command=lambda: webbrowser.open("https://ollama.com")).pack(
                anchor="w", pady=(8, 0))
        win.grab_set()

    # ----------------------------------------------------------- varios
    def _first_run_check(self) -> None:
        if self._closing or self.cfg.seen_welcome:
            return
        self.cfg.seen_welcome = True
        save_config(self.cfg)
        messagebox.showinfo(
            APP_NAME,
            "Bienvenido a ActaLocal.\n\n"
            "Graba tus reuniones y obten el acta automaticamente: transcripcion + "
            "resumen, puntos clave, tareas y decisiones.\n\n"
            "Todo ocurre EN TU PC: ni el audio ni el texto salen de tu ordenador. "
            "Ideal para reuniones con datos confidenciales.\n\n"
            "Consejo: activa 'Audio del sistema' para captar a los demas y "
            "'Microfono' para tu voz.")

    def _on_close(self) -> None:
        if self._state in ("recording", "processing"):
            if not messagebox.askyesno(APP_NAME, "Hay una operacion en curso. Salir igualmente?"):
                return
        self._closing = True
        self._cancel_timers()
        if self.cap:
            try:
                self.cap.stop()
                self.cap.cleanup()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._on_setting()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def main() -> None:
    from .config import setup_logging
    setup_logging()
    App().mainloop()
