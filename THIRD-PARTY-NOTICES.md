# Avisos de terceros (Third-Party Notices)

ActaLocal empaqueta y/o utiliza los siguientes componentes de terceros:

## FFmpeg — GNU GPL v3
ActaLocal incluye **FFmpeg** (https://ffmpeg.org) como programa independiente
que la aplicación invoca para la captura de audio y la transcripción (build
"full" de Gyan.dev, que incorpora el filtro `whisper`). FFmpeg se distribuye
bajo la **GNU General Public License v3**.

- Código fuente de FFmpeg: https://ffmpeg.org/download.html
- Build empaquetada (Gyan.dev, full): https://www.gyan.dev/ffmpeg/builds/
- Texto de la licencia GPL: https://www.gnu.org/licenses/gpl-3.0.html

De acuerdo con la GPL, el código fuente correspondiente de FFmpeg está
disponible en los enlaces anteriores.

## Modelos Whisper (GGML de whisper.cpp) — MIT
ActaLocal descarga en tiempo de ejecución (bajo demanda del usuario) los
modelos **Whisper** en formato GGML publicados por el proyecto **whisper.cpp**,
que se distribuyen bajo licencia **MIT**. Los modelos no se incluyen en el
instalador.

- Proyecto whisper.cpp: https://github.com/ggerganov/whisper.cpp
- Modelos descargados: https://huggingface.co/ggerganov/whisper.cpp

## Otras dependencias
- **soundcard** (captura loopback WASAPI) — licencia BSD-3-Clause — https://github.com/bastibe/SoundCard
- **cffi** (requerida por soundcard) — licencia MIT — https://cffi.readthedocs.io
- **NumPy** — licencia BSD-3-Clause — https://numpy.org
- **Pillow** (PIL) — licencia HPND/MIT-like — https://python-pillow.org

El resto del código de ActaLocal se distribuye bajo licencia MIT (ver `LICENSE`).
