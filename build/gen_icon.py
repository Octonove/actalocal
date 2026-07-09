"""Genera build/icon.ico para ActaLocal (tarjeta navy + punto de grabacion +
lineas de 'acta')."""

from pathlib import Path
from PIL import Image, ImageDraw

NAVY = (30, 58, 95, 255)
NAVY2 = (21, 48, 77, 255)
TERRA = (206, 110, 97, 255)
WHITE = (255, 255, 255, 255)


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=NAVY)
    d.rounded_rectangle([0, int(size * 0.5), size - 1, size - 1], radius=r, fill=NAVY2)
    # punto de grabacion arriba-izquierda
    cx, cy = int(size * 0.30), int(size * 0.30)
    rad = int(size * 0.12)
    d.ellipse([cx - rad - 2, cy - rad - 2, cx + rad + 2, cy + rad + 2], fill=WHITE)
    d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=TERRA)
    # lineas de 'acta'
    x0 = int(size * 0.20)
    x1 = int(size * 0.80)
    lw = max(2, int(size * 0.045))
    for i, yy in enumerate((0.55, 0.68, 0.81)):
        y = int(size * yy)
        x_end = x1 if i < 2 else int(size * 0.6)
        d.line([(x0, y), (x_end, y)], fill=WHITE, width=lw)
    return img


def main() -> None:
    out = Path(__file__).resolve().parent / "icon.ico"
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [make(s) for s in sizes]
    imgs[-1].save(out, format="ICO", sizes=[(s, s) for s in sizes])
    make(256).save(out.with_name("icon_preview.png"))
    print("icono ->", out)


if __name__ == "__main__":
    main()
