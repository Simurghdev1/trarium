"""Offline preview of the patched RTL writer (for QA without Windows).

Mimics the writer: draws a prepared byte string right-to-left; bytes >= 0x80 use
the 2x Persian glyphs at half scale, ASCII uses the original 1x glyph and the
writer's fixed spacing.  Output is rendered at 2x (the game's window scale).
"""
from __future__ import annotations

import re

from PIL import Image

from .exe import Font

CODE = re.compile(rb"\\[EFT][0-9A-Za-z]|\\[A-Za-z]|\^[0-9]|/%%|/%|/|%%%|%%|%")


def atlas(font: Font) -> Image.Image:
    return Image.frombytes("L", (font.map_w, font.map_h), font.pixels)


def draw_rtl(canvas: Image.Image, font: Font, s: bytes, right_x: float, y: float, spacing: int,
             vspacing: int, color=(255, 255, 255), left_x: float | None = None, scale: float = 0.5):
    """Draw like the patched writer onto a 2x canvas; returns min x reached (1x units)."""
    at = atlas(font)
    s = CODE.sub(b"", s)
    min_x = right_x
    for li, line in enumerate(s.split(b"&")):
        x = right_x
        for b in line:
            gx, gy, gw, gh, adv, off = font.dmap[b * 6:b * 6 + 6]
            if b >= 0x80:
                x -= adv * scale
                g = at.crop((gx, gy, gx + gw, gy + gh))
                if scale == 1.0:
                    g = g.resize((gw * 2, gh * 2), Image.NEAREST)
                X, Y = round(x * 2), round((y + li * vspacing) * 2)
            else:
                x -= spacing / 2 if b == 0x20 else spacing
                g = at.crop((gx, gy, gx + gw, gy + gh)).resize((gw * 2, gh * 2), Image.NEAREST)
                X, Y = round((x + off) * 2), round((y + li * vspacing) * 2)
            if gw and gh:
                canvas.paste(Image.new("RGB", g.size, color), (X, Y), g)
            min_x = min(min_x, x)
    return min_x
