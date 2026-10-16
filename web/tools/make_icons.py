#!/usr/bin/env python3
"""Draw the Captain's Deck icons.

The mark is the board itself: five columns on a dark deck, with the captain's
call column lit. Run from the project root:

    python3 tools/make_icons.py
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

OUT = pathlib.Path(__file__).resolve().parent.parent / "static" / "icons"

BG = (11, 18, 32)
COLUMN = (56, 189, 248)
CALL = (192, 132, 252)
LANDED = (71, 85, 105)

# heights as a fraction of the board height, in column order
HEIGHTS = (0.62, 0.86, 0.50, 0.72, 0.34)


def draw(size: int, pad: float) -> Image.Image:
    image = Image.new("RGB", (size, size), BG)
    draw_ctx = ImageDraw.Draw(image)
    inner = size * (1 - 2 * pad)
    left = size * pad
    top = size * pad
    gap = inner * 0.035
    width = (inner - gap * (len(HEIGHTS) - 1)) / len(HEIGHTS)
    for index, fraction in enumerate(HEIGHTS):
        height = inner * fraction
        x0 = left + index * (width + gap)
        y0 = top + (inner - height)
        color = CALL if index == 2 else (LANDED if index == 4 else COLUMN)
        draw_ctx.rounded_rectangle(
            [x0, y0, x0 + width, y0 + height],
            radius=width * 0.45,
            fill=color,
        )
    return image


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    targets = [
        ("icon-192.png", 192, 0.16),
        ("icon-512.png", 512, 0.16),
        ("icon-maskable-512.png", 512, 0.26),
        ("apple-touch-icon.png", 180, 0.16),
    ]
    for name, size, pad in targets:
        image = draw(size, pad)
        image.save(OUT / name)
        print(f"wrote {OUT / name} ({size}x{size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
