"""Synthetic GameMaker Studio data file for tests and UI demos (no game data).

Builds a minimal but structurally valid FORM: GEN8, SPRT (one name pointer), FONT
(one bitmap font, GMS1 or GMS2 glyph layout), TPAG, STRG, TXTR (one PNG page)
and an empty AUDO as the last chunk.
"""
from __future__ import annotations

import io
import struct

from PIL import Image, ImageDraw

DEFAULT_STRINGS = [
    "Demo",
    "spr_player",
    "fnt_main",
    "Main Font",
    "* Howdy!&* I'm FLOWEY.&* FLOWEY the FLOWER!/",
    "* You feel like you're \\Rgoing\\W to have a bad time./%%",
    "Press Z to start",
    "General",
    "settings.ini",
    "obj_writer",
    "Continue",
]


class _Out:
    def __init__(self):
        self.b = bytearray()
        self.fix: list[tuple[int, str]] = []
        self.labels: dict[str, int] = {}

    def here(self, label: str) -> None:
        self.labels[label] = len(self.b)

    def u32(self, v): self.b += struct.pack("<I", v)
    def u16(self, v): self.b += struct.pack("<H", v)
    def u8(self, v): self.b += struct.pack("<B", v)
    def f32(self, v): self.b += struct.pack("<f", v)

    def ptr(self, label: str) -> None:
        self.fix.append((len(self.b), label)); self.u32(0)

    def align(self, n=4):
        self.b += b"\0" * ((-len(self.b)) % n)

    def done(self) -> bytes:
        for at, label in self.fix:
            struct.pack_into("<I", self.b, at, self.labels[label])
        return bytes(self.b)


def _atlas(chars: str) -> tuple[bytes, list]:
    img = Image.new("RGBA", (512, 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    glyphs = []
    for n, ch in enumerate(chars):
        x = n * 8
        d.text((x, 2), ch, fill=(255, 255, 255, 255))
        glyphs.append((ord(ch), x, 0, 8, 16, 8, 0))
    buf = io.BytesIO(); img.save(buf, "PNG")
    return buf.getvalue(), glyphs


def build(strings: list[str] | None = None, gms2: bool = False) -> bytes:
    strings = list(strings or DEFAULT_STRINGS)
    png, glyphs = _atlas(" ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh!.*")
    o = _Out()
    o.b += b"FORM"; o.u32(0)

    def chunk(name: bytes, body):
        o.b += name
        size_at = len(o.b); o.u32(0)
        start = len(o.b)
        body()
        o.align(4)
        struct.pack_into("<I", o.b, size_at, len(o.b) - start)

    def gen8():
        o.u8(0); o.u8(17 if gms2 else 16); o.u16(0)
        o.ptr("s1"); o.ptr("s0"); o.u32(100); o.u32(10000000); o.u32(1234)
        o.b += b"\0" * 16
        o.ptr("s0")
        for v in ((2, 0, 0, 0) if gms2 else (1, 0, 0, 9999)):
            o.u32(v)
        o.b += b"\0" * 8

    def sprt():
        o.u32(1); o.ptr("sprite0"); o.here("sprite0"); o.ptr("s1"); o.u32(16); o.u32(16)

    def font():
        o.u32(1); o.ptr("font0"); o.here("font0")
        o.ptr("s2"); o.ptr("s3"); o.u32(12); o.u32(0); o.u32(0)
        o.u16(32); o.u8(0); o.u8(1); o.u32(127); o.ptr("tpag0"); o.f32(1.0); o.f32(1.0)
        if gms2:
            o.u32(0)                       # ascender offset (bytecode 17+)
        o.u32(len(glyphs))
        for n in range(len(glyphs)):
            o.ptr(f"g{n}")
        for n, g in enumerate(glyphs):
            o.here(f"g{n}")
            o.b += struct.pack("<5H2h", *g)
            if gms2:
                o.u16(0)
        o.align(4)

    def tpag():
        o.u32(1); o.ptr("tpag0"); o.here("tpag0")
        o.b += struct.pack("<11H", 0, 0, 512, 16, 0, 0, 512, 16, 512, 16, 0)

    def strg():
        o.u32(len(strings))
        for n in range(len(strings)):
            o.ptr(f"e{n}")
        for n, s in enumerate(strings):
            o.align(4)
            o.here(f"e{n}")
            raw = s.encode("utf-8")
            o.u32(len(raw))
            o.labels[f"s{n}"] = len(o.b)
            o.b += raw + b"\0"

    def txtr():
        o.u32(1); o.ptr("tex0"); o.here("tex0"); o.u32(1)
        if gms2:
            o.u32(0)
        o.ptr("png0"); o.align(128); o.here("png0"); o.b += png

    def audo():
        o.u32(0)

    for name, fn in ((b"GEN8", gen8), (b"SPRT", sprt), (b"FONT", font), (b"TPAG", tpag),
                     (b"STRG", strg), (b"TXTR", txtr), (b"AUDO", audo)):
        chunk(name, fn)
    out = bytearray(o.done())
    struct.pack_into("<I", out, 4, len(out) - 8)
    return bytes(out)
