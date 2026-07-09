"""Tests de logica pura de ActaLocal (sin audio ni red)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actalocal import transcribe, minutes, models  # noqa: E402


def test_models_medium_and_recommend():
    assert "medium" in models.ORDER
    assert models.recommend_model(32) == "medium"
    assert models.recommend_model(10) == "small"
    assert models.recommend_model(2) == "base"


def test_refine_transcript_no_ollama(monkeypatch):
    # Forzamos 'sin Ollama': debe devolver el texto original sin romper.
    monkeypatch.setattr(minutes.llm, "available", lambda *a, **k: False)
    txt = "hola que tal esto es una prueba"
    assert minutes.refine_transcript(txt, model=None) == txt


SRT = """1
00:00:00,000 --> 00:00:03,000
Hola a todos, empezamos la reunion.

2
00:00:03,000 --> 00:00:07,500
Tenemos que enviar la propuesta antes del viernes.

3
00:00:07,500 --> 00:00:11,000
Decidimos aprobar el presupuesto de marketing.
"""


def test_parse_srt_basic():
    segs = transcribe.parse_srt(SRT)
    assert len(segs) == 3
    assert segs[0]["start"] == 0.0
    assert abs(segs[1]["end"] - 7.5) < 0.001
    assert "propuesta" in segs[1]["text"]


def test_plain_and_timestamped():
    segs = transcribe.parse_srt(SRT)
    plain = transcribe.plain_text(segs)
    assert "reunion" in plain and "presupuesto" in plain
    ts = transcribe.timestamped_text(segs)
    assert ts.startswith("[00:00]")
    assert "[00:07]" in ts


def test_parse_srt_empty():
    assert transcribe.parse_srt("") == []
    assert transcribe.plain_text([]) == ""


def test_split_sentences():
    s = minutes.split_sentences("Hola. Esto es una prueba! Y otra frase?")
    assert len(s) == 3


def test_heuristic_detects_tasks_and_decisions():
    text = ("Hola a todos. Tenemos que enviar la propuesta antes del viernes. "
            "Decidimos aprobar el presupuesto de marketing. "
            "Juan se encarga de preparar el informe. "
            "El producto va muy bien y los clientes estan contentos.")
    m = minutes.heuristic_minutes(text)
    assert any("propuesta" in t or "informe" in t for t in m["tareas"])
    assert any("aprobar" in d.lower() or "presupuesto" in d.lower() for d in m["decisiones"])
    assert m["puntos"]


def test_heuristic_detects_english_cues():
    text = ("Hello everyone. We need to send the proposal before Friday. "
            "We decided to approve the marketing budget. "
            "John will prepare the report by Monday.")
    m = minutes.heuristic_minutes(text)
    assert m["tareas"], "deberia detectar tareas en ingles"
    assert m["decisiones"], "deberia detectar decisiones en ingles"


def test_heuristic_empty():
    m = minutes.heuristic_minutes("")
    assert m == {"resumen": "", "puntos": [], "tareas": [], "decisiones": []}


def test_build_minutes_fallback_without_ollama():
    # Sin Ollama corriendo, build_minutes debe caer a heuristica y no fallar.
    text = "Tenemos que revisar el contrato. Acordamos firmar la semana que viene."
    m = minutes.build_minutes(text, use_ollama=False)
    assert m["fuente"] == "heuristica local"
    assert m["tareas"] or m["decisiones"]


def test_extract_json():
    raw = 'Aqui tienes: {"resumen": "ok", "puntos": ["a"], "tareas": [], "decisiones": []} fin'
    data = minutes._extract_json(raw)
    assert data["resumen"] == "ok"
    assert data["puntos"] == ["a"]


def test_extract_json_invalid():
    assert minutes._extract_json("sin json aqui") is None
    assert minutes._extract_json("") is None


def test_exporters_roundtrip():
    m = {"resumen": "Resumen de prueba", "puntos": ["punto uno", "punto dos"],
         "tareas": ["hacer X"], "decisiones": ["decidir Y"], "fuente": "heuristica local"}
    md = minutes.to_markdown(m, title="Demo", date_str="01/01/2026", attendees="Ana, Luis")
    assert "# Demo" in md and "## Tareas" in md and "- [ ] hacer X" in md
    txt = minutes.to_txt(m, title="Demo", date_str="01/01/2026")
    assert "RESUMEN" in txt and "hacer X" in txt
    htm = minutes.to_html(m, title="Demo", date_str="01/01/2026", attendees="Ana")
    assert "<h1>Demo</h1>" in htm and "punto uno" in htm and "Ana" in htm


def test_html_escapes():
    m = {"resumen": "<script>alert(1)</script>", "puntos": [], "tareas": [],
         "decisiones": [], "fuente": "x"}
    htm = minutes.to_html(m, title="A & B", date_str="hoy")
    assert "<script>" not in htm
    assert "&lt;script&gt;" in htm
    assert "A &amp; B" in htm


def test_clip_long_text():
    big = "palabra " * 5000
    clipped = minutes._clip(big, max_chars=1000)
    assert len(clipped) < len(big)
    assert "[...]" in clipped
