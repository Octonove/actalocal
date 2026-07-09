"""Capa de IA local OPCIONAL via Ollama: shim del nucleo compartido
(octonove_core.llm) con los defaults de generacion de ActaLocal."""

from __future__ import annotations

from octonove_core.llm import (  # noqa: F401
    OLLAMA_URL,
    _cache,
    _get,
    _resolve_ollama_url,
    available,
    default_model,
    has_gpu,
    list_models,
    recommend_model,
    reset_cache,
    set_model,
    system_ram_gb,
)
from octonove_core.llm import generate as _generate


def generate(prompt: str, *, system: str | None = None, model: str | None = None,
             timeout: float = 180.0, temperature: float = 0.2) -> str | None:
    """Defaults propios de esta app: actas largas (timeout amplio) y temperatura
    baja. minutes.py llama generate() SIN estos kwargs y confia en ellos."""
    return _generate(prompt, system=system, model=model, timeout=timeout,
                     temperature=temperature)
