"""Find and replace GML string literals inside a GM8 game.

Every literal gets a stable id ``<where>#<n>@<sha1-8>``:
  where  = ``script/<name>`` | ``object/<name>/<event>.<sub>.<action>`` | ``timeline/<name>/<moment>.<action>``
  n      = index of the literal inside that code block
  sha1-8 = first 8 hex of sha1(source bytes) so a translation never lands on changed text.
GM8 has no escape sequences: a literal runs from its quote to the next identical quote.
Comments are skipped so quotes inside them are not mistaken for literals.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .exe import GM8Exe


@dataclass
class Literal:
    id: str
    text: bytes        # without quotes
    start: int         # offset of the opening quote in the code
    end: int           # offset after the closing quote
    quote: bytes


def scan_literals(code: bytes):
    """Yield (start, end, quote, text) for each string literal in GML source."""
    i, n = 0, len(code)
    while i < n:
        c = code[i:i + 1]
        if c == b"/" and code[i + 1:i + 2] == b"/":
            j = code.find(b"\n", i)
            i = n if j < 0 else j
            continue
        if c == b"/" and code[i + 1:i + 2] == b"*":
            j = code.find(b"*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in (b'"', b"'"):
            j = code.find(c, i + 1)
            if j < 0:
                break
            yield i, j + 1, c, code[i + 1:j]
            i = j + 1
            continue
        i += 1


def short_hash(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()[:8]


def code_blocks(g: GM8Exe):
    """Yield (where, getter, setter) for every GML code block in the game."""
    scripts = g.section("scripts")
    for i, s in scripts.items():
        def setter(new, i=i, s=s):
            s.source = new
            scripts.put(i, s)
        yield "script/" + s.name.decode("latin-1"), s.source, setter
    for kind in ("objects", "timelines"):
        sec = g.section(kind)
        for i, a in sec.items():
            for key, act in a.actions():
                if act.kind != 7:            # 7 == "execute code"
                    continue
                where = "%s/%s/%s" % (kind[:-1], a.name.decode("latin-1"), ".".join(map(str, key)))

                def setter(new, i=i, a=a, act=act, sec=sec):
                    act.args[0] = new
                    sec.put(i, a)
                yield where, act.args[0], setter


def extract(g: GM8Exe):
    """Return {id: Literal} for every literal in every code block."""
    out = {}
    for where, code, _ in code_blocks(g):
        for n, (s, e, q, t) in enumerate(scan_literals(code)):
            lid = "%s#%d@%s" % (where, n, short_hash(t))
            out[lid] = Literal(lid, t, s, e, q)
    return out


TEXTY = re.compile(rb"[A-Za-z]{2,}")


def is_texty(t: bytes) -> bool:
    """Heuristic: player-visible text rather than an ini key / file name / control-only string."""
    plain = re.sub(rb"\\[A-Za-z][0-9A-Za-z]?|\^[0-9]|[&/%]", b" ", t)
    return bool(TEXTY.search(plain)) and not re.fullmatch(rb"[\w.]+\.(ogg|ini|wav|txt)", t)
