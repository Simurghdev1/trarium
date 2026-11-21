"""Add Persian presentation-form glyphs to GameMaker Studio bitmap fonts.

A GMS font (FONT chunk) is a glyph table pointing into a texture-page item
(TPAG), which is a rectangle on a texture page (TXTR, a PNG).  For every font
that misses some of the needed glyphs we

1. render the glyphs with Pillow from the bundled Sahel font, sized to the
   font's own cell height and baseline,
2. enlarge the font's texture page downwards (original pixels untouched, size
   rounded up to a power of two) and draw the new glyphs below the old image,
3. append a rebuilt font struct (all old glyphs byte-for-byte + the new ones,
   sorted by code point) and repoint the FONT list entry to it,
4. grow the font's TPAG rectangle so it covers the new glyphs, and repoint the
   TXTR entry's data pointer to the new PNG (fixing its size/width/height
   fields on GMS 2022+ layouts).

Only PNG texture pages are handled; QOI/BZip2 pages (GMS 2022.1+) are reported
as skipped.
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from trarium.vn.gm8.font import GlyphStyle, render_glyph
from trarium.vn.gms.iff import DataWin, GMSError

FONT_TTF = str(Path(__file__).resolve().parents[2] / "fonts" / "Sahel.ttf")
PNG_SIG = b"\x89PNG\r\n\x1a\n"
HEADER = 40          # name, display, em, bold, italic, range start/charset/aa, range end, tpag, scale x/y


@dataclass
class FontInfo:
    index: int
    ptr: int
    name: str
    header_len: int
    tpag: int
    glyph_ptrs: list[int]
    kerning: bool
    glyphs: dict[int, tuple] = field(default_factory=dict)   # char -> (x, y, w, h, shift, offset, raw bytes)


def _glyph_size(dw: DataWin, p: int, kerning: bool) -> int:
    return 16 + 4 * dw.u16(p + 14) if kerning else 14


def parse_fonts(dw: DataWin) -> list[FontInfo]:
    c = dw.chunk("FONT")
    if not c:
        return []
    out = []
    for i, p in enumerate(dw.pointer_list(c.start)):
        if not (c.start <= p < len(dw.d) - HEADER):
            continue
        hdr = None
        for k in range(0, 7):
            q = p + HEADER + 4 * k
            n = dw.u32(q)
            if n > 70000:
                continue
            if n == 0:
                continue
            first = dw.u32(q + 4)
            if first == q + 4 + 4 * n:
                hdr = k
                break
        if hdr is None:
            continue
        q = p + HEADER + 4 * hdr
        ptrs = dw.pointer_list(q)
        kerning = len(ptrs) > 1 and ptrs[1] - ptrs[0] != 14
        if len(ptrs) == 1:
            kerning = dw.bytecode_version() >= 17 or dw.gen8_version()[0] >= 2
        try:
            name = dw.string_at(dw.u32(p) - 4)
        except GMSError:
            name = f"font{i}"
        f = FontInfo(i, p, name, HEADER + 4 * hdr, dw.u32(p + 28), ptrs, kerning)
        for g in ptrs:
            ch, x, y, w, h = struct.unpack_from("<5H", dw.d, g)
            sh, off = struct.unpack_from("<2h", dw.d, g + 10)
            f.glyphs[ch] = (x, y, w, h, sh, off, bytes(dw.d[g:g + _glyph_size(dw, g, kerning)]))
        out.append(f)
    return out


@dataclass
class Texture:
    entry: int          # absolute offset of the TXTR entry
    field: int          # word index of the data pointer inside the entry
    entry_words: int
    image: Image.Image
    changed: bool = False


def _blob_len_png(d: bytearray, p: int) -> int:
    q = p + 8
    while q + 8 <= len(d):
        n = struct.unpack_from(">I", d, q)[0]
        typ = bytes(d[q + 4:q + 8])
        q += 12 + n
        if typ == b"IEND":
            return q - p
    raise GMSError("PNG without IEND")


def _textures(dw: DataWin) -> list[tuple[int, int, int] | None]:
    """Per texture: (entry offset, data-pointer word index, entry words) or None (not PNG)."""
    c = dw.chunk("TXTR")
    if not c:
        return []
    out = []
    for e in dw.pointer_list(c.start):
        found = None
        for k in (1, 2, 3, 6, 4, 5):
            v = dw.u32(e + 4 * k)
            if c.start <= v < len(dw.d) - 8 and bytes(dw.d[v:v + 8]) == PNG_SIG:
                found = (e, k, k + 1)
                break
        out.append(found)
    return out


def _load(dw: DataWin, info) -> Texture:
    e, k, words = info
    p = dw.u32(e + 4 * k)
    n = _blob_len_png(dw.d, p)
    img = Image.open(io.BytesIO(bytes(dw.d[p:p + n]))).convert("RGBA")
    return Texture(e, k, words, img)


def _pot(n: int) -> int:
    v = 1
    while v < n:
        v *= 2
    return v


def _style(f: FontInfo, tex: Image.Image, tp: tuple, ttf: str, aa: bool) -> GlyphStyle:
    ref = f.glyphs.get(ord("A")) or f.glyphs.get(ord("a")) or next(iter(f.glyphs.values()))
    x, y, w, h = ref[:4]
    sx, sy = tp[0], tp[1]
    base, ink_top = h, None
    alpha = tex.getchannel("A")
    rows = []
    if sx + x + w <= tex.width and sy + y + h <= tex.height:
        rows = [r for r in range(h) if any(alpha.getpixel((sx + x + c, sy + y + r)) > 40 for c in range(w))]
    if rows:
        base, ink_top = rows[-1] + 1, rows[0]
    cap = (base - ink_top) if ink_top is not None else max(4, int(h * 0.7))
    size = max(8, int(round(cap * 1.55)))
    cell = max(h, base + max(2, size // 3))
    return GlyphStyle(ttf, size, base, cell, threshold=0 if aa else 128)


def add_glyphs(dw: DataWin, chars: set[str], ttf: str = FONT_TTF, log=print) -> dict:
    """Add every char in ``chars`` (presentation forms etc.) to every font that lacks it."""
    fonts = parse_fonts(dw)
    texinfo = _textures(dw)
    report = {"fonts": len(fonts), "patched": [], "skipped": []}
    if not fonts or not chars:
        return report
    textures: dict[int, Texture] = {}
    bottoms: dict[int, int] = {}
    for f in fonts:
        missing = sorted(c for c in chars if ord(c) not in f.glyphs and ord(c) <= 0xFFFF)
        if not missing:
            continue
        tp = struct.unpack_from("<10H", dw.d, f.tpag)
        page = dw.u16(f.tpag + 20)
        if page >= len(texinfo) or texinfo[page] is None:
            report["skipped"].append({"font": f.name, "why": "texture page is not PNG (QOI/BZ2) or missing"})
            continue
        if page not in textures:
            textures[page] = _load(dw, texinfo[page])
            bottoms[page] = textures[page].image.height
        tex = textures[page]
        aa = dw.d[f.ptr + 23] != 0
        st = _style(f, tex.image, tp, ttf, aa)
        rendered = [(c, *render_glyph(c, st)) for c in missing]
        sx, sy = tp[0], tp[1]
        shelf_w = max(tex.image.width - sx, 256)
        x = y = row_h = 0
        places = []
        for c, img, adv in rendered:
            if x and x + img.width + 1 > shelf_w:
                x, y, row_h = 0, y + row_h + 1, 0
            places.append((c, img, adv, x, y))
            x += img.width + 1
            row_h = max(row_h, img.height)
        top = bottoms[page] + 1
        need_w, need_h = _pot(max(tex.image.width, sx + shelf_w)), _pot(top + y + row_h + 1)
        if need_w > 16384 or need_h > 16384:
            report["skipped"].append({"font": f.name, "why": "texture would exceed 16384 px"})
            continue
        if (need_w, need_h) != tex.image.size:
            big = Image.new("RGBA", (need_w, need_h), (0, 0, 0, 0))
            big.paste(tex.image, (0, 0))
            tex.image = big
        new_glyphs = dict(f.glyphs)
        max_x, max_y = tp[2], tp[3]
        for c, img, adv, gx, gy in places:
            ax, ay = sx + gx, top + gy
            rgba = Image.new("RGBA", img.size, (255, 255, 255, 0))
            rgba.putalpha(img)
            tex.image.paste(rgba, (ax, ay))
            rx, ry = ax - sx, ay - sy
            raw = struct.pack("<5H2h", ord(c), rx, ry, img.width, img.height, adv, 0)
            if f.kerning:
                raw += b"\0\0"
            new_glyphs[ord(c)] = (rx, ry, img.width, img.height, adv, 0, raw)
            max_x, max_y = max(max_x, rx + img.width), max(max_y, ry + img.height)
        bottoms[page] = top + y + row_h + 1
        tex.changed = True
        # rebuilt font struct
        hdr = bytearray(dw.d[f.ptr:f.ptr + f.header_len])
        struct.pack_into("<I", hdr, 24, max(struct.unpack_from("<I", hdr, 24)[0], max(new_glyphs)))
        at = dw.next_offset()
        order = sorted(new_glyphs)
        n = len(order)
        gpos = at + len(hdr) + 4 + 4 * n
        ptrs, body = [], bytearray()
        for ch in order:
            raw = new_glyphs[ch][6]
            pad = (-(gpos + len(body))) % 2
            body += b"\0" * pad
            ptrs.append(gpos + len(body))
            body += raw
        blob = bytes(hdr) + struct.pack("<I", n) + struct.pack("<%dI" % n, *ptrs) + bytes(body)
        got = dw.append(blob)
        assert got == at
        dw.put_u32(dw.chunk("FONT").start + 4 + 4 * f.index, at)
        # TPAG rectangle covers the new glyphs
        for off, v in ((4, max_x), (6, max_y), (12, max_x), (14, max_y), (16, max_x), (18, max_y)):
            dw.put_u16(f.tpag + off, v)
        report["patched"].append({"font": f.name, "added": len(missing), "px": st.size})
        log(f"font {f.name}: +{len(missing)} glyphs")
    for page, tex in textures.items():
        if not tex.changed:
            continue
        buf = io.BytesIO()
        tex.image.save(buf, "PNG", optimize=False)
        png = buf.getvalue()
        at = dw.append(png, align=128)
        dw.put_u32(tex.entry + 4 * tex.field, at)
        if tex.field >= 3:                       # 2022.3+: TextureBlockSize before the pointer
            dw.put_u32(tex.entry + 8, len(png))
        if tex.field >= 6:                       # 2022.9+: width, height
            dw.put_u32(tex.entry + 12, tex.image.width)
            dw.put_u32(tex.entry + 16, tex.image.height)
        report.setdefault("textures", []).append({"page": page, "size": list(tex.image.size)})
    return report


def read_texture(dw: DataWin, page: int) -> Image.Image:
    info = _textures(dw)[page]
    if info is None:
        raise GMSError("texture is not PNG")
    return _load(dw, info).image
