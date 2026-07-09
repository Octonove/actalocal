"""Configuracion y rutas de datos de ActaLocal (shim del nucleo compartido
octonove_core.config)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from octonove_core.config import default_documents_dir as _default_documents_dir
from octonove_core.config import get_data_dir as _get_data_dir
from octonove_core.config import load_config as _load_config
from octonove_core.config import models_dir as _models_dir
from octonove_core.config import save_config as _save_config
from octonove_core.config import setup_logging as _setup_logging
from octonove_core.config import work_dir as _work_dir

from . import APP_NAME

logger = logging.getLogger(__name__)


def get_data_dir():
    return _get_data_dir(APP_NAME)


def default_recordings_dir() -> Path:
    return _default_documents_dir(APP_NAME)


def work_dir() -> Path:
    """Carpeta temporal de trabajo (WAVs intermedios, audio mezclado)."""
    return _work_dir(APP_NAME)


def models_dir() -> Path:
    """Modelos Whisper. Reutiliza los de TranscriptorIA o CapturaStudio si de
    verdad contienen modelos (asi no se descargan dos veces)."""
    appdata = os.environ.get("APPDATA", "")
    return _models_dir(APP_NAME, [Path(appdata) / "TranscriptorIA" / "models",
                                  Path(appdata) / "CapturaStudio" / "models"])


CONFIG_PATH = get_data_dir() / "config.json"
LOG_PATH = get_data_dir() / "actalocal.log"


@dataclass
class AppConfig:
    recordings_dir: str = field(default_factory=lambda: str(default_recordings_dir()))
    # Audio
    audio_system: bool = True
    audio_mic: bool = True
    audio_mic_device: str = ""
    denoise: bool = True
    # IA
    whisper_model: str = "base"      # tiny | base | small | medium
    language: str = "es"             # codigo ISO o "auto"
    ollama_model: str = ""           # modelo Ollama preferido ("" = auto)
    refine_with_ollama: bool = True  # pulir la transcripcion con Ollama si esta disponible
    # General
    seen_welcome: bool = False

    def ensure_dirs(self) -> None:
        try:
            Path(self.recordings_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("No se pudo crear %s: %s", self.recordings_dir, exc)


def load_config() -> AppConfig:
    return _load_config(CONFIG_PATH, AppConfig)


def save_config(cfg: AppConfig) -> None:
    _save_config(cfg, CONFIG_PATH)


def setup_logging() -> None:
    _setup_logging(LOG_PATH)
