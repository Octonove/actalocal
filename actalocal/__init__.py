"""ActaLocal — graba reuniones y genera el acta con IA, 100% en tu PC.

Captura el audio del sistema (loopback WASAPI) y/o el microfono, transcribe con
Whisper (offline) y redacta un acta (resumen, puntos clave, tareas y decisiones)
con Ollama si esta disponible, o con heuristicas locales si no. Nada sale de tu
ordenador: la opcion privada para reuniones con NDA, sanidad, legal o RRHH.
"""

from __future__ import annotations

APP_NAME = "ActaLocal"
APP_VERSION = "1.0.1"   # fuente unica de version: build-installer.ps1 la inyecta al .iss
