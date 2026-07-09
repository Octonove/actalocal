"""Localizacion de FFmpeg y utilidades de proceso: shim del nucleo compartido
(octonove_core). ActaLocal usa FFmpeg para mezclar las pistas WAV a un audio de
16 kHz mono y para transcribir con el filtro whisper integrado."""

from __future__ import annotations

from octonove_core.ffmpeg import (  # noqa: F401
    ffprobe_from,
    get_duration,
    has_whisper,
)
from octonove_core.ffmpeg import find_ffmpeg as _core_find_ffmpeg
from octonove_core.procutil import (  # noqa: F401
    CREATE_NO_WINDOW,
    _decode,
    subprocess_kwargs,
)


def find_ffmpeg(override: str = "") -> str | None:
    # package_file=__file__: en desarrollo busca ffmpeg.exe junto a ESTA app.
    return _core_find_ffmpeg(override, package_file=__file__)
