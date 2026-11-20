"""Tests for the experimental GameMaker 8 module (no game files needed).
Set GM8_TEST_EXE=/path/to/game.exe to also run the real-exe round-trip test."""
import os
import random
import struct
import zlib

import pytest

from trarium.vn.gm8 import exe as E
from trarium.vn.gm8.fa_text import GlyphMap, needed_glyphs, prepare, shape_chars
from trarium.vn.gm8.gml_patch import draw_text_call, is_typewriter, patch_typewriter
from trarium.vn.gm8.strings import scan_literals


def test_gm80_codec_roundtrip():
    rnd = random.Random(7)
    swap = list(range(256))
    rnd.shuffle(swap)
    swap = bytes(swap)
    plain = bytes(rnd.randrange(256) for _ in range(5000))
    buf = bytearray(plain)
    E.gm80_encrypt(buf, 0, len(buf), swap)
    assert bytes(buf) != plain
    E.gm80_decrypt(buf, 0, len(buf), swap)
    assert bytes(buf) == plain


def test_literals_skip_comments():
    code = b'a = "x" // "not"\r\nb = \'y\' /* "no" */ c = "z"'
    assert [t for *_, t in scan_literals(code)] == [b"x", b"y", b"z"]


def test_shaping_and_lam_alef():
    out = shape_chars(list("سلام"))
    assert out[0] == "\uFEB3" and out[1] == "\uFEFC" and out[2] == "" and out[3] == "\uFEE1"


def test_rtl_draw_order_keeps_codes_and_ltr_runs():
    s = prepare(r"\E2* من \Yفلاوی\W هستم LV^1!/%")
    assert s.startswith(r"\E2* ")
    assert "VL" in s                      # LTR run stored reversed for right-to-left drawing
    assert s.endswith("/%")
    assert r"\Y" in s and r"\W" in s and "^1" in s
    assert "&" not in s


def test_glyphmap_encode():
    p = prepare("سلام، دنیا!")
    gm = GlyphMap()
    gm.add(needed_glyphs(p))
    b = gm.encode(p)
    assert all(c >= 0x80 or c < 0x80 for c in b) and len(b) == len(p)
    assert max(b) <= 0xFF


WRITER = (b"myx = writingx\r\nmyy = writingy\r\nfor (n=1; n < stringpos+1; n=n+1)\r\n{\r\n"
          b"if string_char_at(originalstring,n)=\"&\" then\r\n    {\r\n    script_execute(SCR_NEWLINE)\r\n"
          b"    n = (n + 1)\r\n    }\r\nif myx > writingxend then\r\n    {\r\n    script_execute(SCR_NEWLINE)\r\n    }\r\n"
          b"myletter = string_char_at(originalstring, n)\r\ndraw_set_font(myfont) //f\r\n"
          b"draw_set_color(mycolor) //c\r\nif shake>38 then\r\n{\r\n}\r\n"
          b"else draw_text(myx + (random(shake)-(shake/2)), myy + (random(shake)-(shake/2)), myletter)\r\n"
          b"myx = (myx + spacing)\r\n}\r\n")


def test_typewriter_patch():
    assert is_typewriter(WRITER)
    out = patch_typewriter(WRITER)
    assert b"draw_text_transformed" in out and b"__fa" in out
    assert b"SCR_NEWLINE" not in out
    assert b"if (!__fa) myx = (myx + spacing)" in out


def test_draw_text_call():
    code = b'draw_text(90,30,"Hello")\r\n'
    s = code.index(b'"')
    e = code.index(b'"', s + 1) + 1
    cs, ce, x, y = draw_text_call(code, s, e)
    assert (x, y) == (b"90", b"30") and code[cs:ce] == b'draw_text(90,30,"Hello")'


def test_font_glyphs_added():
    from trarium.vn.gm8.build import FONT_TTF
    from trarium.vn.gm8.font import GlyphStyle, add_glyphs
    if not os.path.exists(FONT_TTF):
        pytest.skip("bundled font missing")
    dmap = [0] * 1536
    dmap[65 * 6:65 * 6 + 6] = [0, 0, 8, 16, 8, 0]
    f = E.Font(b"f", 800, b"x", 12, 0, 0, 32, 127, dmap, 16, 16, bytes(256))
    gm = GlyphMap()
    gm.add(needed_glyphs(prepare("سلام")))
    add_glyphs(f, gm.map, GlyphStyle(FONT_TTF, 30, 26, 44))
    assert f.range_end >= max(gm.map.values())
    assert len(f.pixels) == f.map_w * f.map_h
    for slot in gm.map.values():
        assert f.dmap[slot * 6 + 2] > 0 and f.dmap[slot * 6 + 4] > 0
    # asset re-serialises and parses back identically
    blob = f.write()
    g = E.Font.read(E.Reader(blob))
    assert g.dmap == f.dmap and g.pixels == f.pixels


@pytest.mark.skipif(not os.environ.get("GM8_TEST_EXE"), reason="needs a GM8.0 exe")
def test_real_exe_roundtrip():
    data = open(os.environ["GM8_TEST_EXE"], "rb").read()
    assert E.GM8Exe(data).build() == data
