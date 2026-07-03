"""Generate the app icon (PNG sizes + multi-resolution .ico) with Pillow.

The committed icon files in ``src/drainage_extractor/gui/resources`` are the
output of this script; re-run it after design changes:

    pip install pillow
    python scripts/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

RES_DIR = Path(__file__).resolve().parent.parent / "src" / "drainage_extractor" / "gui" / "resources"

BG = (18, 26, 34, 255)          # dark navy tile
EDGE = (34, 134, 184, 255)      # accent border
TRIB = (142, 212, 255, 255)     # light tributaries
MID = (56, 165, 238, 255)       # mid channel
MAIN = (26, 111, 192, 255)      # trunk


def _river(draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]], width: int, colour) -> None:
    draw.line(pts, fill=colour, width=width, joint="curve")
    r = width / 2
    for x, y in (pts[0], pts[-1]):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)


def build(size: int = 256) -> Image.Image:
    """Draw the branching-river tile at 256 px, supersampled 4×."""
    ss = 4
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def sc(*pts: tuple[float, float]) -> list[tuple[float, float]]:
        return [(x * s / 256, y * s / 256) for x, y in pts]

    d.rounded_rectangle(sc((10, 10), (246, 246)), radius=58 * s / 256, fill=BG,
                        outline=EDGE, width=int(7 * s / 256))

    w = int(13 * s / 256)
    _river(d, sc((58, 42), (84, 88), (104, 126)), w, TRIB)          # west tributary
    _river(d, sc((150, 36), (128, 82), (106, 124)), w, TRIB)        # north tributary
    _river(d, sc((105, 125), (118, 166), (148, 194)), int(w * 1.5), MID)   # mid reach
    _river(d, sc((206, 88), (188, 140), (152, 192)), w, TRIB)       # east tributary
    _river(d, sc((149, 193), (178, 226)), int(w * 2.0), MAIN)       # trunk to outlet

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    RES_DIR.mkdir(parents=True, exist_ok=True)
    base = build(256)
    base.save(RES_DIR / "icon_256.png")
    base.resize((48, 48), Image.LANCZOS).save(RES_DIR / "icon_48.png")
    base.save(
        RES_DIR / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Icons written to {RES_DIR}")


if __name__ == "__main__":
    main()
