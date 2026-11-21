"""Which GameMaker Studio strings are player text, and how Persian is stored.

GMS runners draw strings left to right and look glyphs up by Unicode code point,
so Persian is stored *pre-shaped* (Arabic presentation forms) in **visual
left-to-right order**, one display line at a time.  Control codes are kept
byte-exact:

* Undertale/Deltarune writer codes: ``\\E1`` ``\\F0`` ``\\TS`` (one argument),
  ``\\R`` ``\\W`` ... (colour), ``^1`` (pause), ``&`` and ``#`` (line break),
  ``/`` ``%`` ``%%`` ``/%`` ``/^`` (end of page/message), ``\\[C]``-style codes,
* generic placeholders: ``%s`` ``%d`` ``{0}`` ``~`` ``\\n``.

Colour codes are re-emitted wherever the colour changes in draw order, so a
coloured word stays coloured after the line is reversed.  Other inline codes
travel with the character that follows them; codes at the very start / end of a
line stay there.
"""
from __future__ import annotations

import re
import unicodedata

from trarium.vn.gm8.fa_text import HARAKAT, MIRROR, is_arabic, is_ltr, shape_chars

CODE_RE = re.compile(r"\\\[[^\]\n]{1,12}\]|\\[EFTMSZ][0-9A-Za-z\-]|\\[A-Za-z*]|\^[0-9]|/%%|/%|/\^|/\*|%%%|%%|"
                     r"%[0-9]*[sdfi]|\{[0-9]+\}|[&#/%~]|\\n|\n")
PROTECT = CODE_RE
RESET = "\\W"
COLOR_RE = re.compile(r"\\[RGWYXBOLPpC]\Z")
LINE_SEP_RE = re.compile(r"&|#|\\n|\n")
END_CODES = re.compile(r"(?:/%%|/%|/\^|/\*|%%%|%%|[/%])+\Z")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_FILE = re.compile(r"[\w\-./\\]+\.[A-Za-z0-9]{1,5}\Z")
_CODEY = re.compile(r"gl_|uniform |varying |void main|attribute |#define|precision |function\s*\(|=>|\breturn\b.*;")
_URL = re.compile(r"(https?|ftp|file)://|www\.", re.I)
_WORD = re.compile(r"[A-Za-z]{2,}")
_FA = re.compile(r"[\u0600-\u06FF]")


def plain(s: str) -> str:
    return CODE_RE.sub(" ", s)


def is_player_text(s: str, identifier: bool = False) -> bool:
    """Heuristic: dialogue / UI text rather than code identifiers, file names,
    sprite names, shader sources or format codes.  Single words are skipped on
    purpose: they are often ini keys or asset lookups and translating them can
    break saves or code (they are reported as skipped)."""
    if identifier or not s or len(s) > 4000 or "\0" in s:
        return False
    t = s.strip()
    if not t or _FA.search(t):
        return False
    if _IDENT.match(t) or _FILE.match(t) or _URL.search(t) or _CODEY.search(t):
        return False
    if t.count(";") >= 2 or t.count("{") >= 2 or t.startswith(("gml_", "obj_", "spr_", "scr_", "snd_", "rm_", "fnt_")):
        return False
    words = _WORD.findall(plain(t))
    if not words:
        return False
    return len(words) >= 2


def is_single_word(s: str) -> bool:
    t = plain(s).strip()
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z'\-]{1,24}[.!?:]?", t))


# ----------------------------------------------------------------- persian
def _tokens(line: str, code_re=CODE_RE):
    """[(code_or_None, char_or_None)] in logical order."""
    out, pos = [], 0
    for m in code_re.finditer(line):
        out += [(None, c) for c in line[pos:m.start()]]
        out.append((m.group(0), None))
        pos = m.end()
    out += [(None, c) for c in line[pos:]]
    return out


def visual_line(line: str, code_re=CODE_RE, color_re=COLOR_RE, reset: str = RESET) -> str:
    """One display line (no line separators) -> shaped, visual LTR draw order.

    ``code_re``/``color_re``/``reset`` let other engines (RPG Maker ``\\C[n]``) reuse it."""
    toks = _tokens(line, code_re)
    # leading and trailing code runs stay put
    i = 0
    while i < len(toks) and toks[i][1] is None:
        i += 1
    j = len(toks)
    while j > i and toks[j - 1][1] is None:
        j -= 1
    lead = "".join(c for c, _ in toks[:i])
    tail = "".join(c for c, _ in toks[j:])
    body = toks[i:j]
    if not body:
        return line
    # per char: attached codes (non-colour) and current colour state
    chars, attached, colour = [], [], []
    pend, state = "", ""
    for code, ch in body:
        if code is not None:
            if color_re.match(code):
                state = "" if code == reset else code
            else:
                pend += code
            continue
        chars.append(ch); attached.append(pend); colour.append(state)
        pend = ""
    tail = pend + tail
    shaped = shape_chars(chars)
    kinds = ["L" if is_ltr(c) else "R" if is_arabic(c) else "N" for c in chars]
    res = list(kinds)
    for k, kind in enumerate(kinds):
        if kind == "N":
            a = next((kinds[x] for x in range(k - 1, -1, -1) if kinds[x] != "N"), "R")
            b = next((kinds[x] for x in range(k + 1, len(kinds)) if kinds[x] != "N"), "R")
            res[k] = "L" if a == b == "L" else "R"
    # runs in logical order, then reverse run order (RTL paragraph); L runs keep inner order
    runs, k = [], 0
    while k < len(chars):
        m = k
        while m < len(chars) and res[m] == res[k]:
            m += 1
        idx = list(range(k, m))
        runs.append(idx if res[k] == "L" else idx[::-1])
        k = m
    order = [x for run in reversed(runs) for x in run]
    out = [lead]
    cur = None
    for x in order:
        if not shaped[x]:
            continue
        col = colour[x]
        if col != cur:
            if col:
                out.append(col)
            elif cur:
                out.append(reset)
            cur = col
        ch = shaped[x]
        if res[x] == "R":
            ch = MIRROR.get(ch, ch)
        out.append(attached[x] + ch)
    end_state = colour[-1] if colour else ""
    if end_state != (cur or ""):
        out.append(end_state or reset)
    return "".join(out) + tail


def wrap(text: str, budget: int) -> list[str]:
    """Greedy word wrap by visible characters (codes do not count)."""
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if cur and len(plain(cand).rstrip()) > budget:
            lines.append(cur); cur = w
        else:
            cur = cand
    lines.append(cur)
    return lines


def line_budget(source: str) -> int | None:
    """Longest visible line of the English string, or None for single-line UI text."""
    if not LINE_SEP_RE.search(source):
        return None
    lines = LINE_SEP_RE.split(END_CODES.sub("", source))
    return max(10, max(len(plain(l).strip()) for l in lines) + 2)


def prepare(fa: str, source: str) -> str:
    """Translated string (logical order, codes kept) -> stored string for the runner."""
    fa = HARAKAT.sub("", unicodedata.normalize("NFC", fa))
    budget = line_budget(source)
    m = LINE_SEP_RE.search(source)
    sep = m.group(0) if m else None
    end = END_CODES.search(fa)
    tail = end.group(0) if end else ""
    body = fa[:len(fa) - len(tail)] if tail else fa
    parts = LINE_SEP_RE.split(body)
    seps = LINE_SEP_RE.findall(body)
    lines: list[str] = []
    joins: list[str] = []
    for n, part in enumerate(parts):
        wrapped = wrap(part, budget) if budget else [part]
        for w_i, w in enumerate(wrapped):
            lines.append(w)
            if w_i < len(wrapped) - 1:
                joins.append(sep or "&")
        if n < len(seps):
            joins.append(seps[n])
    out = []
    for n, l in enumerate(lines):
        out.append(visual_line(l))
        if n < len(joins):
            out.append(joins[n])
    return "".join(out) + tail


def glyphs_needed(stored: list[str]) -> set[str]:
    return {c for s in stored for c in plain(s) if ord(c) > 0x7F and not c.isspace()}
