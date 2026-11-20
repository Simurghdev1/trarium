"""Small, pattern-checked GML patches that teach a GM8 typewriter to draw Persian.

Only *typewriter* draw events that follow the common GM8 pattern are touched
(per-letter ``draw_text(myx ..., myletter)`` then ``myx = (myx + spacing)``).  The
patch is opt-in per string: a page containing the marker ``\\P`` is drawn right to
left; every other page renders exactly as before.  Nothing is changed when the
patterns are not found (the caller reports it).
"""
from __future__ import annotations

import re

MARK = b"\\P"
HELPER_NAME = b"trarium_fa_draw"

NEWLINE_CALL = re.compile(rb"script_execute\(\s*SCR_NEWLINE\s*\)")
INLINE_NEWLINE = (b"{myx = writingx; if (__fa) myx = __fax; myy = (myy + vspacing); "
                  b"lineno = (lineno + 1)}")
HEAD = re.compile(rb"\A(\s*myx\s*=\s*writingx\s*\r?\n\s*myy\s*=\s*writingy\s*\r?\n)")
HEAD_ADD = (b"__fa = (string_pos(\"\\P\", originalstring) > 0)\r\n"
            b"__fax = writingxend\r\n"
            b"if (global.typer == 11) __fax = writingx + 200\r\n"
            b"if (writingxend - writingx == 200) __fax = writingx + %d\r\n"
            b"if (__fa) myx = __fax\r\n")
WRAP = re.compile(rb"if\s+myx\s*>\s*writingxend\s+then")
WRAP_NEW = b"if ((!__fa && myx > writingxend) || (__fa && myx < writingx - 4)) then"
COLOR = re.compile(rb"(draw_set_color\(mycolor\)[^\r\n]*\r?\n)")
COLOR_ADD = (b"__big = 0\r\n"
             b"__gs = 0.5\r\n"
             b"if (__fa) {\r\n"
             b"  draw_set_font(myfont)\r\n"
             b"  if (string_length(myletter) > 0) { if (ord(myletter) >= 128) __big = 1 }\r\n"
             b"  __gs = 0.5\r\n"
             b"%s"
             b"  if (__big) myx -= string_width(myletter) * __gs\r\n"
             b"  else { if (myletter == \" \") myx -= spacing / 2 else myx -= spacing }\r\n"
             b"}\r\n")
DRAW = re.compile(rb"else\s+draw_text\(myx \+ \(random\(shake\)-\(shake/2\)\), myy \+ \(random\(shake\)-\(shake/2\)\), myletter\)")
DRAW_NEW = (b"else { if (__big) draw_text_transformed(myx + (random(shake)-(shake/2)), "
            b"myy + (random(shake)-(shake/2)), myletter, __gs, __gs, 0) "
            b"else draw_text(myx + (random(shake)-(shake/2)), myy + (random(shake)-(shake/2)), myletter) }")
ADV = re.compile(rb"\r?\nmyx\s*=\s*\(myx\s*\+\s*spacing\)")
ADV_NEW = b"\r\nif (!__fa) myx = (myx + spacing)"


def is_typewriter(code: bytes) -> bool:
    return bool(HEAD.search(code) and DRAW.search(code) and ADV.search(code))


def patch_typewriter(code: bytes, bubble_width: int = 185, fonts_1x=()) -> bytes:
    """Return the patched draw-event code (raises ValueError if a pattern is missing).
    ``fonts_1x``: font assets whose Persian glyphs are stored at 1x (drawn unscaled,
    for rooms that are not view-scaled); all others are 2x glyphs drawn at 0.5."""
    one = b"".join(b"  if (myfont == %s) __gs = 1\r\n" % f for f in fonts_1x)
    steps = [(HEAD, lambda m: m.group(1) + HEAD_ADD % bubble_width, 1),
             (WRAP, lambda m: WRAP_NEW, 1),
             (COLOR, lambda m: m.group(1) + COLOR_ADD % one, 1),
             (DRAW, lambda m: DRAW_NEW, 1),
             (ADV, lambda m: ADV_NEW, 1)]
    out = NEWLINE_CALL.sub(INLINE_NEWLINE, code)
    for rx, rep, count in steps:
        out, n = rx.subn(rep, out, count=count)
        if n != count:
            raise ValueError("typewriter pattern not found: %r" % rx.pattern[:40])
    return out


HELPER_SRC = (
    b"// Trarium: draw a Persian string (stored in right-to-left draw order).\r\n"
    b"// argument0 = right edge x, argument1 = y, argument2 = string. Returns width.\r\n"
    b"var __i, __c, __x;\r\n"
    b"__x = argument0\r\n"
    b"for (__i = 1; __i <= string_length(argument2); __i += 1) {\r\n"
    b"  __c = string_char_at(argument2, __i)\r\n"
    b"  if (ord(__c) >= 128) { __x -= string_width(__c) / 2; draw_text_transformed(__x, argument1, __c, 0.5, 0.5, 0) }\r\n"
    b"  else { if (__c == \" \") __x -= string_width(__c) / 2 else __x -= string_width(__c); draw_text(__x, argument1, __c) }\r\n"
    b"}\r\n"
    b"return argument0 - __x\r\n")


def draw_text_call(code: bytes, lit_start: int, lit_end: int):
    """If the literal at code[lit_start:lit_end] is the 3rd argument of a plain
    ``draw_text(x, y, "lit")`` call, return (call_start, call_end, x_expr, y_expr)."""
    head = code[max(0, lit_start - 120):lit_start]
    m = re.search(rb"draw_text\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*,\s*$", head)
    if not m:
        return None
    tail = re.match(rb"\s*\)", code[lit_end:])
    if not tail:
        return None
    start = lit_start - len(head) + m.start()
    return start, lit_end + tail.end(), m.group(1), m.group(2)
