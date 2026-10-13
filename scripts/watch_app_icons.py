"""Draw the phone app's home-screen icons (huxley-watch, T1.46): a calm
walkie-talkie on a solid square, written once and committed.

    uv run --with pillow python scripts/watch_app_icons.py

Writes server/watch/src/huxley_watch/static/icon-{180,192,512}.png. The
180 px one is the apple-touch-icon: iOS rounds its corners itself and
fills transparency with black, so it is drawn full bleed. The 192 and 512
px ones (the manifest's) are rounded squares.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "server/watch/src/huxley_watch/static"
BACK = (47, 93, 80)  # deep calm green
INK = (244, 243, 239)  # the page's background
SCALE = 4  # drawn large, scaled down: smooth edges


def draw(size: int, rounded: bool) -> Image.Image:
    big = size * SCALE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(big * 0.22), fill=BACK)
    else:
        d.rectangle((0, 0, big, big), fill=BACK)

    def box(x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        return (round(x0 * big), round(y0 * big), round(x1 * big), round(y1 * big))

    w = max(1, round(big * 0.035))  # stroke
    # the antenna, then the body over its foot
    d.rounded_rectangle(box(0.385, 0.17, 0.435, 0.36), radius=round(big * 0.025), fill=INK)
    d.rounded_rectangle(box(0.33, 0.31, 0.67, 0.83), radius=round(big * 0.07), fill=INK)
    # the speaker grille, cut out of the body
    for y in (0.43, 0.505, 0.58):
        d.rounded_rectangle(box(0.405, y, 0.595, y + 0.035), radius=w // 2, fill=BACK)
    # the talk button
    d.ellipse(box(0.465, 0.67, 0.535, 0.74), fill=BACK)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size, rounded in ((180, False), (192, True), (512, True)):
        icon = draw(size, rounded)
        if not rounded:
            icon = icon.convert("RGB")  # no alpha: iOS would fill it with black
        icon.save(OUT / f"icon-{size}.png", optimize=True)
        print(f"wrote {OUT / f'icon-{size}.png'}")


if __name__ == "__main__":
    main()
