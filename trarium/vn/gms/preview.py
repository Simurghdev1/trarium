"""Offline preview: draw a stored string with a patched font the way a GMS runner's
``draw_text`` does (left to right, glyph ``shift`` as advance, ``&``/``#`` breaks).
For QA only; it is not the game's renderer."""
from __future__ import annotations

import struct

from PIL import Image

from trarium.vn.gms import text as tx
from trarium.vn.gms.font import parse_fonts, read_texture
from trarium.vn.gms.iff import DataWin


def render(dw: DataWin, s: str, font_index: int = 0, scale: int = 2) -> Image.Image:
    f = parse_fonts(dw)[font_index]
    tp = struct.unpack_from("<10H", dw.d, f.tpag)
    tex = read_texture(dw, dw.u16(f.tpag + 20))
    lines = tx.LINE_SEP_RE.split(tx.END_CODES.sub("", s))
    lh = max(g[3] for g in f.glyphs.values())
    width = 8 + max(sum(f.glyphs.get(ord(c), (0, 0, 0, 0, 6))[4] for c in tx.CODE_RE.sub("", l)) for l in lines)
    img = Image.new("RGBA", (max(16, width), 8 + lh * len(lines)), (0, 0, 0, 255))
    for n, line in enumerate(lines):
        x, y = 4, 4 + n * lh
        for c in tx.CODE_RE.sub("", line):
            g = f.glyphs.get(ord(c))
            if not g:
                x += 6
                continue
            gx, gy, gw, gh, sh, off = g[:6]
            if gw and gh:
                crop = tex.crop((tp[0] + gx, tp[1] + gy, tp[0] + gx + gw, tp[1] + gy + gh))
                img.alpha_composite(crop, (x + off, y))
            x += sh
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)
