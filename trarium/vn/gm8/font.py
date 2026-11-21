"""Add pre-rendered Persian glyphs to a GameMaker 8 font atlas.

GM8 fonts cover char codes ``range_start..range_end`` with a 256-entry table
(x, y, w, h, shift, offset) into an 8-bit alpha atlas.  We keep every original
glyph and append the Persian presentation forms (mapped to bytes 0x80..0xFF by
:class:`fa_text.GlyphMap`) at **2x resolution**: the patched writer draws them with
``draw_text_transformed(..., 0.5, 0.5, 0)`` so on the game's 2x-scaled window they
land 1:1 on screen pixels – crisp and readable, while the original pixel font is
left untouched for English/ASCII.
"""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from .exe import Font
from .fa_text import UNPUNCT


@dataclass
class GlyphStyle:
    ttf: str
    size: int            # px at 2x
    baseline: int        # baseline y inside the 2x cell (= 2 * original baseline)
    cell_h: int          # 2x cell height
    threshold: int = 110  # alpha cut for crisp, non-antialiased pixels (0 = keep AA)
    y_shift: int = 0


def original_baseline(font: Font, probe: str = "AHx") -> int:
    """Lowest inked row (+1) of a few flat-bottomed capitals = the font's baseline (1x)."""
    rows = []
    for ch in probe:
        x, y, w, h, _s, _o = font.glyph(ord(ch))
        ink = [r for r in range(h) if any(font.pixels[(y + r) * font.map_w + x + k] for k in range(w))]
        if ink:
            rows.append(ink[-1] + 1)
    return max(rows) if rows else font.glyph(65)[3]


def joins_right(ch: str) -> bool:
    """Presentation form that connects to the glyph on its right (final / medial)."""
    import unicodedata
    n = unicodedata.name(ch, "")
    return " FINAL FORM" in n or " MEDIAL FORM" in n


def render_glyph(ch: str, st: GlyphStyle):
    f = ImageFont.truetype(st.ttf, st.size, layout_engine=ImageFont.Layout.BASIC)
    adv = max(1, int(round(f.getlength(UNPUNCT.get(ch, ch)))))
    pad = st.size
    canvas = Image.new("L", (adv + 2 * pad, st.cell_h), 0)
    ImageDraw.Draw(canvas).text((pad, st.baseline + st.y_shift), UNPUNCT.get(ch, ch), font=f, fill=255,
                                anchor="ls")
    img = canvas.crop((pad, 0, pad + adv, st.cell_h))
    if joins_right(ch):
        # one extra column so the connecting stroke overlaps its right neighbour
        # (drawn first) instead of leaving a hairline gap from rounding
        wide = Image.new("L", (adv + 1, st.cell_h), 0)
        wide.paste(img, (0, 0))
        wide.paste(img.crop((adv - 1, 0, adv, st.cell_h)), (adv, 0))
        img = wide
    if st.threshold:
        img = img.point(lambda a: 255 if a >= st.threshold else 0)
    return img, adv


def add_glyphs(font: Font, glyphs: dict, st: GlyphStyle) -> dict:
    """glyphs: {char: byte_slot}.  Mutates ``font``; returns {slot: (w, h, adv)}."""
    rendered = {slot: render_glyph(ch, st) for ch, slot in glyphs.items()}
    old = Image.frombytes("L", (font.map_w, font.map_h), font.pixels)
    width = max(font.map_w, 512)
    # shelf-pack the new glyphs under the original atlas
    x = y = 0
    row_h = 0
    places = {}
    for slot, (img, _adv) in sorted(rendered.items()):
        if x + img.width + 1 > width:
            x, y = 0, y + row_h + 1
            row_h = 0
        places[slot] = (x, y)
        x += img.width + 1
        row_h = max(row_h, img.height)
    top = font.map_h + 1
    atlas = Image.new("L", (width, top + y + row_h + 1), 0)
    atlas.paste(old, (0, 0))
    info = {}
    for slot, (img, adv) in rendered.items():
        px, py = places[slot]
        atlas.paste(img, (px, top + py))
        font.dmap[slot * 6:slot * 6 + 6] = [px, top + py, img.width, img.height, adv, 0]
        info[slot] = (img.width, img.height, adv)
    font.map_w, font.map_h = atlas.size
    font.pixels = atlas.tobytes()
    font.range_end = max(font.range_end, max(glyphs.values()))
    return info
