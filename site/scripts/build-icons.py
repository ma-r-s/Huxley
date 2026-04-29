#!/usr/bin/env -S uv run --with pillow --with fonttools --with coloraide --with requests --quiet python
"""
Generate Huxley's full icon set from the wordmark letterform.

Input:  Instrument Serif Italic (downloaded fresh) + the page's coral palette.
Output: site/public/{favicon.ico,favicon.svg,apple-touch-icon.png,icon-*.png,
        og-image.png,manifest.webmanifest}.

Re-run this anytime the brand colors or the wordmark choice changes — it's the
single source of truth for every raster icon, and the only thing it depends on
is the font + the OKLCH values mirrored from src/styles/index.css.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

import requests
from coloraide import Color
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
PUBLIC = SCRIPT_DIR.parent / "public"
FONT_CACHE = SCRIPT_DIR / ".cache" / "InstrumentSerif-Italic.ttf"
FONT_URL = "https://raw.githubusercontent.com/Instrument/instrument-serif/main/fonts/ttf/InstrumentSerif-Italic.ttf"

# ── Palette mirrored from src/styles/index.css ────────────────────────────
# OKLCH values are the source of truth in CSS. We convert them once here so
# every raster output matches what the page renders. If you change the CSS
# vars, change these and rerun.
CORAL_OKLCH = "oklch(0.62 0.19 23)"
PAPER_OKLCH = "oklch(0.96 0.015 60)"


def oklch_to_rgb(value: str) -> tuple[int, int, int]:
    c = Color(value).convert("srgb").clip()
    r, g, b = (max(0.0, min(1.0, v)) for v in c.coords())
    return round(r * 255), round(g * 255), round(b * 255)


CORAL = oklch_to_rgb(CORAL_OKLCH)
PAPER = oklch_to_rgb(PAPER_OKLCH)


# ── Font ─────────────────────────────────────────────────────────────────
def load_font_bytes() -> bytes:
    if FONT_CACHE.exists():
        return FONT_CACHE.read_bytes()
    print(f"  fetching font: {FONT_URL}")
    FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(FONT_URL, timeout=30)
    r.raise_for_status()
    FONT_CACHE.write_bytes(r.content)
    return r.content


# ── Letter rendering on a square ──────────────────────────────────────────
def render_letter(
    size: int,
    letter: str,
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
    *,
    font_bytes: bytes,
    letter_height_pct: float = 0.62,
    rounding: float = 0.18,
) -> Image.Image:
    """Square canvas, optically centered letter, optional rounded corners.

    letter_height_pct: cap-to-baseline target as a fraction of canvas. Tuned
    by eye on a 512px render — Instrument Serif's italic 'h' has long
    ascenders that visually shrink unless we lean larger than you'd think.

    rounding: 0 = square, 0.18 = subtle iOS-feel corner radius. macOS/iOS
    apply their own mask, so this only matters where we render flat.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if rounding > 0:
        draw.rounded_rectangle(
            (0, 0, size - 1, size - 1),
            radius=int(size * rounding),
            fill=bg + (255,),
        )
    else:
        draw.rectangle((0, 0, size - 1, size - 1), fill=bg + (255,))

    # Pillow sizes fonts by point ≈ EM; the actual glyph height is smaller.
    # Iterate to hit the target visible-glyph height precisely.
    target_h = size * letter_height_pct
    pt = int(target_h * 1.4)
    for _ in range(10):
        font = ImageFont.truetype(io.BytesIO(font_bytes), pt)
        bbox = font.getbbox(letter)
        h = bbox[3] - bbox[1]
        if abs(h - target_h) < 1:
            break
        pt = max(8, int(pt * (target_h / max(h, 1))))
    font = ImageFont.truetype(io.BytesIO(font_bytes), pt)
    bbox = font.getbbox(letter)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    # The 'h' has a tall ascender + descender baseline geometry. Center on
    # the *visible bbox*, not the font's metric box, so it sits optically
    # centered regardless of how Instrument Serif's vertical metrics report.
    x = (size - w) / 2 - bbox[0]
    y = (size - h) / 2 - bbox[1]
    # Lift italic letterforms a hair — italic angles read low-right when
    # mathematically centered.
    y -= size * 0.01
    draw.text((x, y), letter, font=font, fill=fg + (255,))
    return img


# ── ICO writer (Pillow's is fine but we want explicit multi-res control) ─
def write_ico(path: Path, images: list[Image.Image]) -> None:
    images[0].save(path, format="ICO", sizes=[(im.width, im.height) for im in images])


# ── SVG with embedded glyph path ──────────────────────────────────────────
def build_svg(font_bytes: bytes, *, out_path: Path) -> None:
    """Generate an SVG with the 'h' as a vector path (font-independent).

    Browsers render favicon.svg at draw time without waiting for webfonts,
    so we must not rely on @font-face. Extract the glyph outline and embed
    it as a <path>. fontTools gives us upem coordinates, y-down in glyph
    space; SVG is y-down on screen but glyphs are y-up in font space, so
    we flip via a transform.
    """
    tt = TTFont(io.BytesIO(font_bytes))
    cmap = tt.getBestCmap()
    glyph_name = cmap[ord("h")]  # getBestCmap returns codepoint → glyph name
    glyph_set = tt.getGlyphSet()
    pen = SVGPathPen(glyph_set)
    glyph_set[glyph_name].draw(pen)
    path_d = pen.getCommands()

    upem = tt["head"].unitsPerEm
    # Glyph metrics — bbox of the actual outline, not the advance width.
    glyph = tt["glyf"][glyph_name] if "glyf" in tt else None
    if glyph and glyph.numberOfContours:
        gx_min, gy_min, gx_max, gy_max = glyph.xMin, glyph.yMin, glyph.xMax, glyph.yMax
    else:
        # CFF fallback (Instrument Serif ships TTF, but be safe).
        gx_min, gy_min, gx_max, gy_max = 0, 0, upem, upem

    gw = gx_max - gx_min
    gh = gy_max - gy_min
    canvas = 64  # logical SVG units; renders crisply at any pixel size.
    target_h = canvas * 0.62
    scale = target_h / gh
    scaled_w = gw * scale
    tx = (canvas - scaled_w) / 2 - gx_min * scale
    ty = (canvas + target_h) / 2 + gy_min * scale - canvas * 0.01

    coral_hex = "#{:02x}{:02x}{:02x}".format(*CORAL)
    paper_hex = "#{:02x}{:02x}{:02x}".format(*PAPER)
    radius = canvas * 0.18
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas} {canvas}">'
        f'<rect width="{canvas}" height="{canvas}" rx="{radius:.3f}" fill="{coral_hex}"/>'
        f'<g transform="translate({tx:.3f} {ty:.3f}) scale({scale:.5f} -{scale:.5f})">'
        f'<path d="{path_d}" fill="{paper_hex}"/>'
        f"</g>"
        f"</svg>"
    )
    out_path.write_text(svg)


# ── OG card (1200×630) ───────────────────────────────────────────────────
def render_og_card(font_bytes: bytes) -> Image.Image:
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), CORAL)
    draw = ImageDraw.Draw(img)

    # Subtle radial atmosphere — same shape as page's section atmosphere.
    # Light upper-left, dark lower-right. Cheap fake using a softened
    # alpha gradient blitted twice.
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for r in range(800, 0, -20):
        a = max(0, int(40 * (1 - r / 800)))
        odraw.ellipse((-100 - r, -200 - r, 600 + r, 400 + r), fill=(255, 255, 255, a // 4))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Wordmark — italic serif "huxley" centered-left, large.
    wordmark = ImageFont.truetype(io.BytesIO(font_bytes), 220)
    wm_text = "huxley"
    wm_bbox = wordmark.getbbox(wm_text)
    wm_w = wm_bbox[2] - wm_bbox[0]
    wm_h = wm_bbox[3] - wm_bbox[1]
    wm_x = (w - wm_w) / 2 - wm_bbox[0]
    wm_y = (h - wm_h) / 2 - wm_bbox[1] - 60
    draw.text((wm_x, wm_y), wm_text, font=wordmark, fill=PAPER)

    # Tagline — smaller italic, faint.
    tagline = ImageFont.truetype(io.BytesIO(font_bytes), 44)
    tag_text = "A voice you can actually own."
    tag_bbox = tagline.getbbox(tag_text)
    tag_w = tag_bbox[2] - tag_bbox[0]
    tag_x = (w - tag_w) / 2 - tag_bbox[0]
    tag_y = wm_y + wm_h + 80
    # Faint paper — paper@70% over coral. Pillow has no opacity for text on
    # RGB; we approximate by mixing toward the bg.
    faint = tuple(int(PAPER[i] * 0.78 + CORAL[i] * 0.22) for i in range(3))
    draw.text((tag_x, tag_y), tag_text, font=tagline, fill=faint)

    return img


# ── Manifest ──────────────────────────────────────────────────────────────
def write_manifest(path: Path) -> None:
    coral_hex = "#{:02x}{:02x}{:02x}".format(*CORAL)
    manifest = {
        "name": "Huxley",
        "short_name": "Huxley",
        "description": "Voice agent framework. Bring a persona and skills.",
        "start_url": "/",
        "display": "standalone",
        "background_color": coral_hex,
        "theme_color": coral_hex,
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {
                "src": "/icon-512-maskable.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
            {"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"},
        ],
    }
    import json

    path.write_text(json.dumps(manifest, indent=2) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────
def main() -> None:
    PUBLIC.mkdir(exist_ok=True)
    font_bytes = load_font_bytes()
    print(f"  coral {CORAL} · paper {PAPER}")

    # Standard letter mark — rounded corners, paper "h" on coral.
    common = {"font_bytes": font_bytes, "fg": PAPER, "bg": CORAL}

    sizes_for_ico = [16, 32, 48]
    ico_imgs = [render_letter(s, "h", **common, rounding=0.0) for s in sizes_for_ico]
    write_ico(PUBLIC / "favicon.ico", ico_imgs)
    print(f"  → favicon.ico ({'/'.join(map(str, sizes_for_ico))})")

    for size, name in [
        (180, "apple-touch-icon.png"),
        (192, "icon-192.png"),
        (512, "icon-512.png"),
    ]:
        img = render_letter(size, "h", **common, rounding=0.18)
        img.save(PUBLIC / name, format="PNG", optimize=True)
        print(f"  → {name}")

    # Maskable: bigger safe zone — letter scaled down so it stays inside
    # the circular crop Android applies (40% radius from center).
    maskable = render_letter(512, "h", **common, rounding=0.0, letter_height_pct=0.42)
    maskable.save(PUBLIC / "icon-512-maskable.png", format="PNG", optimize=True)
    print("  → icon-512-maskable.png")

    build_svg(font_bytes, out_path=PUBLIC / "favicon.svg")
    print("  → favicon.svg")

    og = render_og_card(font_bytes)
    og.save(PUBLIC / "og-image.png", format="PNG", optimize=True)
    print("  → og-image.png (1200x630)")

    write_manifest(PUBLIC / "manifest.webmanifest")
    print("  → manifest.webmanifest")


if __name__ == "__main__":
    main()
