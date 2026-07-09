"""Generacion del ACTA a partir de la transcripcion.

Dos caminos:
  - Con Ollama (opcional): redaccion real en JSON (resumen, puntos, tareas,
    decisiones).
  - Sin Ollama: heuristicas locales (extractivo por frecuencia + deteccion de
    cues de tarea/decision). Peor que un LLM, pero 100% local y util.

Las funciones del nucleo son puras (entran textos, salen dicts/strings) para
poder testearlas sin audio ni red.
"""

from __future__ import annotations

import html
import json
import logging
import re

from . import llm

logger = logging.getLogger(__name__)

# --- Heuristica -----------------------------------------------------------
_STOP = set("""
a al algo algunas algunos ante antes como con contra cual cuando de del desde donde
dos el ella ellas ellos en entre era erais eran eras eres es esa esas ese eso esos
esta estaba estado estamos estan estar estas este esto estos fue fueron ha haber habia
hacer hace hacia hasta hay la las le les lo los mas me mi mis mucho muy nada ni no nos
nosotras nosotros o os otra otras otro otros para pero poco por porque que se sea sean
segun ser si sin sobre solo somos son su sus tan te tiene tienen toda todas todo todos
tu tus un una uno unos vosotras vosotros y ya yo bueno vale entonces pues osea o sea
""".split())

# Nota: son PREFIJOS/stems, por eso NO llevan \b de cierre (asi "decidi" casa con
# "decidimos" y "aprob" con "aprobar"). Cubre espanol e ingles (los dos idiomas
# mas habituales) para que la heuristica sirva aunque Ollama no este.
_TASK_CUES = re.compile(
    r"\b(hay que|tenemos que|tengo que|tienes que|voy a|vamos a|deberi|habr[ií]a que|"
    r"habr[aá] que|encarg|prepar|envi|mand|revis|pendiente|quedamos en|te encargas|"
    r"se encarga|asignamos|para el |antes del |fecha l[ií]mite|deadline|pl[aá]zo|"
    r"we need to|we have to|i will|we will|we'?ll|let'?s|action item|follow up|"
    r"to[- ]do|assign|by (?:monday|tuesday|wednesday|thursday|friday|next week)|due )",
    re.IGNORECASE)

_DECISION_CUES = re.compile(
    r"\b(decidi|acord|aprob|hemos decidido|se decide|optamos por|elegimos|"
    r"confirmamos|descartamos|se rechaza|se acuerda|"
    r"we decided|we agreed|approv|we'?ll go with|let'?s go with|rejected|"
    r"we chose|agreed to)",
    re.IGNORECASE)


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    # corta en . ? ! manteniendo frases razonables
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = []
    for p in parts:
        p = p.strip()
        # subdivide frases muy largas por comas/“y” solo si exceden mucho
        if len(p) > 320:
            out.extend(s.strip() for s in re.split(r";|,\s+(?=[A-ZÁÉÍÓÚÑ])", p) if s.strip())
        elif p:
            out.append(p)
    return out


def _word_freq(sentences: list[str]) -> dict:
    freq: dict[str, int] = {}
    for s in sentences:
        for w in re.findall(r"[a-záéíóúñü]+", s.lower()):
            if len(w) > 3 and w not in _STOP:
                freq[w] = freq.get(w, 0) + 1
    return freq


def _score(sentence: str, freq: dict) -> float:
    words = [w for w in re.findall(r"[a-záéíóúñü]+", sentence.lower())
             if len(w) > 3 and w not in _STOP]
    if not words:
        return 0.0
    return sum(freq.get(w, 0) for w in words) / (len(words) ** 0.6)


def heuristic_minutes(text: str, max_points: int = 7) -> dict:
    sents = split_sentences(text)
    if not sents:
        return {"resumen": "", "puntos": [], "tareas": [], "decisiones": []}
    freq = _word_freq(sents)
    scored = sorted(((_score(s, freq), i, s) for i, s in enumerate(sents)),
                    key=lambda t: -t[0])
    top = sorted(scored[:max_points], key=lambda t: t[1])   # vuelve al orden original
    puntos = [s for _, _, s in top]
    tareas = _dedup([s for s in sents if _TASK_CUES.search(s)])[:12]
    decisiones = _dedup([s for s in sents if _DECISION_CUES.search(s)])[:12]
    resumen = " ".join(puntos[:3])
    return {"resumen": resumen, "puntos": puntos, "tareas": tareas,
            "decisiones": decisiones}


def _dedup(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for it in items:
        k = re.sub(r"\W+", "", it.lower())[:80]
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


# --- Ollama ---------------------------------------------------------------
_SYS = ("Eres un asistente que redacta actas de reunion claras y fieles. "
        "Respondes SIEMPRE en espanol y SOLO con un objeto JSON valido, sin texto "
        "adicional ni markdown.")

_PROMPT = """A partir de esta transcripcion de una reunion, redacta el acta.

Devuelve EXACTAMENTE este JSON (sin nada mas):
{{
  "resumen": "2-4 frases con lo esencial de la reunion",
  "puntos": ["punto clave 1", "punto clave 2", "..."],
  "tareas": ["tarea con responsable y plazo si se menciona", "..."],
  "decisiones": ["decision tomada 1", "..."]
}}

Si alguna lista no aplica, dejala vacia []. No inventes datos que no esten en la
transcripcion.

TRANSCRIPCION:
\"\"\"
{text}
\"\"\""""


# Unificado en el core; se conserva el nombre privado (los tests lo usan).
from octonove_core.jsonutil import extract_json as _extract_json  # noqa: E402


def _clip(text: str, max_chars: int = 12000) -> str:
    """Recorta transcripciones enormes para no reventar el contexto del modelo."""
    if len(text) <= max_chars:
        return text
    head = text[: max_chars * 2 // 3]
    tail = text[-max_chars // 3:]
    return head + "\n[...]\n" + tail


_REFINE_SYS = ("Eres un corrector de transcripciones de voz. Corrige la puntuacion, las "
               "mayusculas y los errores evidentes de reconocimiento de voz, SIN cambiar el "
               "significado, SIN resumir y SIN anadir contenido. Responde solo con el texto "
               "corregido, en el mismo idioma.")


def refine_transcript(text: str, model: str | None = None, chunk_chars: int = 3000) -> str:
    """Pule la transcripcion con Ollama (puntuacion/mayusculas/errores obvios).
    Trocea en bloques para no exceder el contexto. Si no hay Ollama o algo falla,
    devuelve el texto original (nunca rompe el flujo)."""
    text = (text or "").strip()
    if not text or not llm.available():
        return text
    sents = split_sentences(text)
    chunks: list[str] = []
    buf = ""
    for s in sents:
        if len(buf) + len(s) + 1 > chunk_chars and buf:
            chunks.append(buf.strip())
            buf = ""
        buf += " " + s
    if buf.strip():
        chunks.append(buf.strip())
    out = []
    fails = 0
    for c in chunks:
        r = llm.generate(f"Corrige esta transcripcion:\n\n{c}", system=_REFINE_SYS,
                         model=model, temperature=0.1)
        if not r:
            fails += 1
        out.append(r.strip() if r else c)
    if fails:
        logger.warning("Pulido parcial: %d/%d bloques fallaron (se uso el texto original "
                       "en esos).", fails, len(chunks))
    refined = " ".join(out).strip()
    return refined or text


def ollama_minutes(text: str, model: str | None = None) -> dict | None:
    raw = llm.generate(_PROMPT.format(text=_clip(text)), system=_SYS, model=model)
    data = _extract_json(raw or "")
    if not isinstance(data, dict):
        return None

    def _strlist(v):
        if not isinstance(v, list):
            return []
        return [x.strip() for x in v if isinstance(x, str) and x.strip()]

    resumen = data.get("resumen", "")
    out = {
        "resumen": resumen.strip() if isinstance(resumen, str) else "",
        "puntos": _strlist(data.get("puntos")),
        "tareas": _strlist(data.get("tareas")),
        "decisiones": _strlist(data.get("decisiones")),
    }
    if not (out["resumen"] or out["puntos"]):
        return None
    return out


def build_minutes(text: str, *, use_ollama: bool = True,
                  model: str | None = None) -> dict:
    """Genera el acta. Intenta Ollama si use_ollama; cae a heuristica."""
    text = (text or "").strip()
    if not text:
        return {"resumen": "", "puntos": [], "tareas": [], "decisiones": [],
                "fuente": "vacio"}
    if use_ollama and llm.available():
        data = ollama_minutes(text, model)
        if data:
            data["fuente"] = "Ollama (" + (model or llm.default_model() or "?") + ")"
            return data
    data = heuristic_minutes(text)
    data["fuente"] = "heuristica local"
    return data


# --- Exportadores ---------------------------------------------------------
def to_markdown(minutes: dict, *, title: str, date_str: str,
                attendees: str = "", transcript: str = "") -> str:
    lines = [f"# {title}", "", f"**Fecha:** {date_str}  "]
    if attendees.strip():
        lines.append(f"**Asistentes:** {attendees.strip()}  ")
    lines.append(f"**Generado por:** {minutes.get('fuente', 'ActaLocal')}")
    lines.append("")
    if minutes.get("resumen"):
        lines += ["## Resumen", "", minutes["resumen"], ""]
    if minutes.get("puntos"):
        lines += ["## Puntos clave", ""] + [f"- {p}" for p in minutes["puntos"]] + [""]
    if minutes.get("decisiones"):
        lines += ["## Decisiones", ""] + [f"- {d}" for d in minutes["decisiones"]] + [""]
    if minutes.get("tareas"):
        lines += ["## Tareas / acciones", ""] + [f"- [ ] {t}" for t in minutes["tareas"]] + [""]
    if transcript.strip():
        lines += ["## Transcripcion completa", "", transcript.strip(), ""]
    return "\n".join(lines)


def to_txt(minutes: dict, *, title: str, date_str: str,
           attendees: str = "", transcript: str = "") -> str:
    def section(name, items, bullet="- "):
        if not items:
            return []
        return [name.upper(), ""] + [f"{bullet}{x}" for x in items] + [""]
    out = [title, "=" * len(title), f"Fecha: {date_str}"]
    if attendees.strip():
        out.append(f"Asistentes: {attendees.strip()}")
    out.append(f"Generado por: {minutes.get('fuente', 'ActaLocal')}")
    out.append("")
    if minutes.get("resumen"):
        out += ["RESUMEN", "", minutes["resumen"], ""]
    out += section("Puntos clave", minutes.get("puntos", []))
    out += section("Decisiones", minutes.get("decisiones", []))
    out += section("Tareas / acciones", minutes.get("tareas", []), bullet="[ ] ")
    if transcript.strip():
        out += ["TRANSCRIPCION COMPLETA", "", transcript.strip()]
    return "\n".join(out)


_HTML_TPL = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
 body{{font-family:'Segoe UI',system-ui,sans-serif;color:#1E293B;max-width:820px;
      margin:32px auto;padding:0 20px;line-height:1.55}}
 h1{{color:#1E3A5F;border-bottom:3px solid #CE6E61;padding-bottom:8px}}
 h2{{color:#1E3A5F;margin-top:28px}}
 .meta{{color:#64748B;font-size:14px;margin:4px 0}}
 ul{{padding-left:22px}} li{{margin:5px 0}}
 .task{{list-style:none}} .task::before{{content:'\\2610  ';color:#CE6E61}}
 .src{{background:#EEF3F8;border-radius:8px;padding:8px 12px;font-size:13px;
       color:#64748B;display:inline-block;margin-top:8px}}
 details{{margin-top:24px}} summary{{cursor:pointer;color:#1E3A5F;font-weight:600}}
 pre{{white-space:pre-wrap;background:#FAFCFF;border:1px solid #E2E8F0;border-radius:8px;
      padding:12px;font-family:inherit;font-size:14px}}
</style></head><body>
<h1>{title}</h1>
<div class="meta"><b>Fecha:</b> {date}</div>
{attendees}
<div class="src">Generado por: {source} &middot; 100% en local con ActaLocal</div>
{resumen}{puntos}{decisiones}{tareas}{transcript}
</body></html>"""


def to_html(minutes: dict, *, title: str, date_str: str,
            attendees: str = "", transcript: str = "") -> str:
    def esc(s):
        return html.escape(str(s))

    def ul(items, cls=""):
        if not items:
            return ""
        li = "".join(f'<li class="{cls}">{esc(x)}</li>' for x in items)
        return f"<ul>{li}</ul>"

    att = f'<div class="meta"><b>Asistentes:</b> {esc(attendees)}</div>' if attendees.strip() else ""
    resumen = f"<h2>Resumen</h2><p>{esc(minutes['resumen'])}</p>" if minutes.get("resumen") else ""
    puntos = f"<h2>Puntos clave</h2>{ul(minutes.get('puntos', []))}" if minutes.get("puntos") else ""
    decis = f"<h2>Decisiones</h2>{ul(minutes.get('decisiones', []))}" if minutes.get("decisiones") else ""
    tareas = f"<h2>Tareas / acciones</h2>{ul(minutes.get('tareas', []), 'task')}" if minutes.get("tareas") else ""
    trans = (f"<details><summary>Transcripcion completa</summary><pre>{esc(transcript)}</pre></details>"
             if transcript.strip() else "")
    return _HTML_TPL.format(title=esc(title), date=esc(date_str), attendees=att,
                            source=esc(minutes.get("fuente", "ActaLocal")),
                            resumen=resumen, puntos=puntos, decisiones=decis,
                            tareas=tareas, transcript=trans)
