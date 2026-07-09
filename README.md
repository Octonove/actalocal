# ActaLocal

Aplicación de escritorio para **Windows** que convierte tus reuniones en **actas completas** — transcripción, resumen, puntos clave, decisiones y tareas — **100% en tu PC**: ni el audio ni el texto salen de tu ordenador.

## Funciones

- **Grabación de reuniones**: audio del sistema (los demás participantes) + micrófono (tu voz), con pausa/reanudar y medidores de nivel.
- **Importar audio/vídeo** existente (wav, mp3, m4a, mp4, mkv…).
- **Transcripción local** con el filtro *whisper* de FFmpeg (modelos tiny/base/small/medium, descarga única).
- **Acta automática**: resumen, puntos principales, decisiones y tareas. Con [Ollama](https://ollama.com) (opcional y gratuito) el acta se redacta con IA local; sin él, heurísticas locales.
- **Privacidad**: el audio en bruto y los temporales se **borran automáticamente** tras procesar.
- **Exportación**: HTML, Markdown, TXT y SRT.

## Stack

Python 3 + Tkinter (ttk) · FFmpeg (build *full* de Gyan, con filtro whisper) · `soundcard` (loopback WASAPI) · Ollama opcional.

Depende del paquete compartido de la suite [`octonove-core`](https://github.com/Octonove/octonove-core) (tema, capa Ollama, config, utilidades FFmpeg): debe estar en el `sys.path` del entorno (vía `.pth` o copia junto al proyecto).

## Compilar

```powershell
# Ejecutable (PyInstaller onedir)
.\build\build.ps1

# Instalador (Inno Setup)
.\build\build-installer.ps1
```

## Tests

```powershell
python -m pytest tests/ -q
```

## Licencia

[MIT](LICENSE) — © 2026 Octonove.
