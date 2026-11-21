"""Persian text preparation for GameMaker 8 byte strings.

GM8 strings are single bytes and its fonts are pre-rendered atlases, so Persian
is stored as *already shaped* presentation forms mapped onto byte slots
0x80..0xFF (see :mod:`font`).  The patched writer draws a Persian string from
right to left, one byte at a time, so every line is stored in **right-to-left
draw order**: logical order for Persian, with embedded LTR runs (Latin words,
numbers) reversed, and mirrored brackets.

Game control codes (``\\X`` colour/face codes, ``^n`` pauses, ``&`` line breaks,
``/`` ``%`` page ends) are kept byte-exact and travel with the character that
follows them.
"""
from __future__ import annotations

import re
import unicodedata

from arabic_reshaper.letters import FINAL, INITIAL, ISOLATED, LETTERS_ARABIC, MEDIAL


def connects_with_letter_after(ch: str) -> bool:
    f = LETTERS_ARABIC.get(ch)
    return bool(f and (f[INITIAL] or f[MEDIAL]))


def connects_with_letter_before(ch: str) -> bool:
    f = LETTERS_ARABIC.get(ch)
    return bool(f and (f[FINAL] or f[MEDIAL]))

ZWNJ = "\u200c"
# code tokens understood by the Undertale-style writer; generic enough for most GM8 typewriters
# \E \F \T take one argument char; every other \X code is two chars (as the GM8 writer reads them)
CODE_RE = re.compile(r"\\[EFT][0-9A-Za-z]|\\[A-Za-z]|\^[0-9]|&|/%%|/%|/\^|/|%%%|%%|%")
# ASCII punctuation inside Persian runs is drawn with the Persian font (proper size/spacing);
# it is keyed by its full-width twin so it gets its own byte slot.
PUNCT = {c: chr(ord(c) + 0xFEE0) for c in ".!:-=()[]{}<>,;'\"+"}
UNPUNCT = {v: k for k, v in PUNCT.items()}
LAM_ALEF = {"آ": ("\uFEF5", "\uFEF6"), "أ": ("\uFEF7", "\uFEF8"), "إ": ("\uFEF9", "\uFEFA"),
            "ا": ("\uFEFB", "\uFEFC")}
MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "<": ">", ">": "<", "{": "}", "}": "{",
          "«": "»", "»": "«"}
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


HARAKAT = re.compile("[\u064B-\u065F\u0670\u06D6-\u06ED]")


def is_digit(ch: str) -> bool:
    return "0" <= ch <= "9" or "\u06F0" <= ch <= "\u06F9" or "\u0660" <= ch <= "\u0669"


def is_arabic(ch: str) -> bool:
    o = ord(ch)
    if is_digit(ch):
        return False
    return 0x0600 <= o <= 0x06FF or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF


def is_ltr(ch: str) -> bool:
    """Latin letters and all digits form left-to-right runs (numbers read LTR in Persian)."""
    return (ch.isascii() and ch.isalnum()) or is_digit(ch)


def _form(ch: str, prev_joins: bool, next_joins: bool) -> str:
    forms = LETTERS_ARABIC.get(ch)
    if not forms:
        return ch
    want = (MEDIAL if prev_joins and next_joins else FINAL if prev_joins else
            INITIAL if next_joins else ISOLATED)
    f = forms[want]
    if not f:  # letter lacks that form (e.g. alef has no medial) -> fall back
        f = forms[FINAL] if prev_joins and forms[FINAL] else forms[ISOLATED]
    return f


def shape_chars(chars: list[str]) -> list[str]:
    """Contextual shaping on a list of logical chars (one entry per char, codes removed).
    Returns a list of the same length; lam-alef ligatures put the ligature on the lam
    slot and an empty string on the alef slot. ZWNJ breaks joining and becomes ''."""
    n = len(chars)
    out = list(chars)

    def letter(i):
        return 0 <= i < n and chars[i] in LETTERS_ARABIC

    i = 0
    while i < n:
        c = chars[i]
        if c == ZWNJ:
            out[i] = ""
            i += 1
            continue
        if not letter(i):
            i += 1
            continue
        prev_j = letter(i - 1) and connects_with_letter_after(chars[i - 1])
        if c == "ل" and i + 1 < n and chars[i + 1] in LAM_ALEF:
            iso, fin = LAM_ALEF[chars[i + 1]]
            out[i] = fin if prev_j else iso
            out[i + 1] = ""
            i += 2
            continue
        next_j = letter(i + 1) and connects_with_letter_after(c) and connects_with_letter_before(chars[i + 1])
        out[i] = _form(c, prev_j, next_j)
        i += 1
    return out


def tokenize(line: str):
    """Split a line into [(codes_before, char)], trailing_codes."""
    toks, pending, pos = [], "", 0
    for m in CODE_RE.finditer(line):
        for ch in line[pos:m.start()]:
            toks.append((pending, ch))
            pending = ""
        pending += m.group(0)
        pos = m.end()
    for ch in line[pos:]:
        toks.append((pending, ch))
        pending = ""
    return toks, pending


def rtl_draw_order(line: str, persian_digits: bool = False) -> str:
    """One display line (no '&') -> shaped string in right-to-left draw order."""
    toks, tail = tokenize(line)
    chars = [c for _, c in toks]
    if persian_digits:
        chars = [c.translate(PERSIAN_DIGITS) for c in chars]
    shaped = shape_chars(chars)
    # bidi: classify, resolve neutrals (between two LTR -> LTR, else RTL)
    kinds = ["L" if is_ltr(c) else "R" if is_arabic(c) else "N" for c in chars]
    res = list(kinds)
    for i, k in enumerate(kinds):
        if k != "N":
            continue
        a = next((kinds[j] for j in range(i - 1, -1, -1) if kinds[j] != "N"), "R")
        b = next((kinds[j] for j in range(i + 1, len(kinds)) if kinds[j] != "N"), "R")
        res[i] = "L" if a == b == "L" else "R"
    units = [(toks[i][0], shaped[i], res[i]) for i in range(len(toks))]
    out, i = [], 0
    while i < len(units):
        if units[i][2] == "L":
            j = i
            while j < len(units) and units[j][2] == "L":
                j += 1
            run = units[i:j]
            # codes stay in front of the run so colours/faces switch before it is drawn
            lead = "".join(u[0] for u in run)
            out.append(lead + "".join(u[1] for u in reversed(run)))
            i = j
        else:
            code, ch, _ = units[i]
            ch = MIRROR.get(ch, ch)
            out.append(code + PUNCT.get(ch, ch))
            i += 1
    return "".join(out) + tail


def prepare(text: str, line_sep: str = "&", persian_digits: bool = False) -> str:
    """Whole game string -> RTL draw-order string (lines kept, codes kept)."""
    text = HARAKAT.sub("", unicodedata.normalize("NFC", text))
    return line_sep.join(rtl_draw_order(l, persian_digits) for l in text.split(line_sep))


def needed_glyphs(prepared: str) -> set:
    return {c for c in prepared if ord(c) > 0x7F}


class GlyphMap:
    """Assigns non-ASCII glyphs to free byte slots 0x80..0xFF."""

    def __init__(self, first: int = 0x80, last: int = 0xFF):
        self.slots = list(range(first, last + 1))
        self.map: dict[str, int] = {}

    def add(self, chars) -> None:
        for c in sorted(chars):
            if c not in self.map:
                if len(self.map) >= len(self.slots):
                    raise ValueError("too many distinct Persian glyph forms (%d > %d)" %
                                     (len(self.map) + 1, len(self.slots)))
                self.map[c] = self.slots[len(self.map)]

    def encode(self, s: str) -> bytes:
        out = bytearray()
        for c in s:
            if ord(c) < 0x80:
                out.append(ord(c))
            elif c in self.map:
                out.append(self.map[c])
            else:
                raise KeyError("glyph %r (U+%04X) has no slot" % (c, ord(c)))
        return bytes(out)
