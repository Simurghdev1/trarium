"""GameMaker Studio 1/2: find the data file, pick player text, translate, patch."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from trarium.vn.gms import text as tx
from trarium.vn.gms.font import FONT_TTF, add_glyphs
from trarium.vn.gms.iff import DataWin, GMSError, looks_like_data_win

DATA_NAMES = ("data.win", "game.unx", "game.ios", "game.droid")
MAX_DATA_BYTES = 4 * 2**30


@dataclass
class Seg:
    index: int
    text: str


def find_data_file(root: Path) -> Path | None:
    """data.win / game.unx / game.ios / game.droid anywhere under root (shallowest first)."""
    root = Path(root)
    hits = []
    for p in root.rglob("*"):
        if p.is_symlink() or not p.is_file():
            continue
        if p.name.lower() in DATA_NAMES or p.suffix.lower() in (".win", ".unx", ".droid"):
            try:
                with open(p, "rb") as f:
                    if looks_like_data_win(f.read(12)):
                        hits.append(p)
            except OSError:
                pass
    hits.sort(key=lambda p: (len(p.relative_to(root).parts), p.name.lower() not in DATA_NAMES))
    return hits[0] if hits else None


def _replace_file(path: Path, data) -> None:
    """Write next to the file and swap it in (a new inode: hard links to the upload stay untouched)."""
    tmp = path.with_name(path.name + ".trarium-tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    import os
    os.replace(tmp, path)


def extract(dw: DataWin) -> tuple[list[Seg], dict]:
    strings = dw.strings()
    ident = dw.direct_string_refs()
    segs, single = [], 0
    for i, s in enumerate(strings):
        if i in ident:
            continue
        if tx.is_player_text(s):
            segs.append(Seg(i, s))
        elif tx.is_single_word(s):
            single += 1
    return segs, {"strings": len(strings), "identifiers": len(ident), "skipped_single_words": single}


def inspect(path: Path) -> dict:
    p = Path(path)
    if p.stat().st_size > MAX_DATA_BYTES:
        raise GMSError("data file too large")
    dw = DataWin(p.read_bytes())
    segs, stats = extract(dw)
    from trarium.vn.gms.font import parse_fonts
    return {"lines": len(segs), "fonts": len(parse_fonts(dw)), "version": ".".join(map(str, dw.gen8_version())),
            "bytecode": dw.bytecode_version(), **stats}


def apply(dw: DataWin, segs: list[Seg], translations: list[str | None], ttf: str = FONT_TTF) -> dict:
    stored = []
    written = 0
    for seg, fa in zip(segs, translations):
        if not fa:
            continue
        s = tx.prepare(fa, seg.text)
        dw.set_string(seg.index, s)
        stored.append(s)
        written += 1
    glyphs = tx.glyphs_needed(stored)
    font_rep = add_glyphs(dw, glyphs, ttf, log=lambda *_: None) if glyphs else {"patched": [], "skipped": []}
    return {"segments": len(segs), "written": written, "kept_original": len(segs) - written,
            "glyph_forms": len(glyphs), "fonts": font_rep}


def translate_file(path: Path, translate, limit: int | None = None) -> dict:
    """Patch ``path`` in place. ``translate(list[str]) -> (list[str|None], why)``."""
    started = time.time()
    path = Path(path)
    if path.stat().st_size > MAX_DATA_BYTES:
        raise GMSError("data file too large")
    dw = DataWin(path.read_bytes())
    segs, stats = extract(dw)
    if not segs:
        raise GMSError("هیچ متن قابل ترجمه‌ای در این فایل GameMaker پیدا نشد.")
    if limit:
        segs = segs[:limit]
    trs, why = translate([s.text for s in segs])
    rep = apply(dw, segs, trs)
    _replace_file(path, dw.d)
    rep.update(stats)
    rep["rejected"] = why
    rep["samples"] = [{"en": s.text, "fa": t} for s, t in list(zip(segs, trs))[:12]]
    rep["seconds"] = round(time.time() - started, 1)
    return rep
