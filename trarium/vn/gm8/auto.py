"""Automatic GameMaker 8.0 route for the UI: pick player text, translate, build.

The hand-tuned route (translation JSON with per-string font/width) lives in
:mod:`build`; this module writes that JSON automatically:

* literals inside a ``draw_text(x, y, "...")`` call -> ``draw_text`` labels,
* other text literals -> typewriter pages, but only when the game has a
  typewriter draw event that :mod:`gml_patch` recognises (Undertale-style
  writer).  Otherwise they are left in English and counted as ``unsupported``.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .exe import GM80_SIG, GM80_SIG_POS, GM8Error, GM8Exe
from .gml_patch import draw_text_call, is_typewriter
from .strings import code_blocks, is_texty, scan_literals, short_hash

MAX_EXE_BYTES = 1 * 2**30


def is_gm8_exe(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return False
            f.seek(GM80_SIG_POS)
            return f.read(8) == GM80_SIG
    except OSError:
        return False


def find_gm8_exe(root: Path) -> Path | None:
    root = Path(root)
    for depth in range(3):
        pattern = "/".join(["*"] * (depth + 1))
        for p in sorted(root.glob(pattern)):
            if p.suffix.lower() == ".exe" and p.is_file() and not p.is_symlink() and is_gm8_exe(p):
                return p
    return None


def _default_font(g: GM8Exe) -> str | None:
    names = [f.name.decode("latin-1") for _i, f in g.section("fonts").items()]
    for want in ("fnt_maintext", "fnt_main"):
        if want in names:
            return want
    return names[0] if names else None


def collect(g: GM8Exe) -> tuple[list[dict], dict]:
    blocks = list(code_blocks(g))
    has_writer = any(is_typewriter(code) for _w, code, _s in blocks)
    font = _default_font(g)
    items, unsupported = [], 0
    for where, code, _setter in blocks:
        for n, (s, e, _q, t) in enumerate(scan_literals(code)):
            if not is_texty(t) or b" " not in t.strip():
                continue
            lid = "%s#%d@%s" % (where, n, short_hash(t))
            if draw_text_call(code, s, e):
                mode = "draw_text"
            elif has_writer:
                mode = "writer"
            else:
                unsupported += 1
                continue
            items.append({"id": lid, "text": t.decode("latin-1"), "mode": mode, "font": font})
    return items, {"typewriter": has_writer, "unsupported": unsupported, "font": font}


def inspect(path: Path) -> dict:
    if Path(path).stat().st_size > MAX_EXE_BYTES:
        raise GM8Error("exe too large")
    g = GM8Exe(Path(path).read_bytes())
    items, info = collect(g)
    return {"lines": len(items), **info}


def translate_exe(path: Path, translate, limit: int | None = None) -> dict:
    """Patch the exe in place. ``translate(list[str]) -> (list[str|None], why)``."""
    from .build import build
    path = Path(path)
    if path.stat().st_size > MAX_EXE_BYTES:
        raise GM8Error("exe too large")
    g = GM8Exe(path.read_bytes())
    items, info = collect(g)
    if not items or not info["font"]:
        raise GM8Error("هیچ متن قابل ترجمه‌ای (یا فونتی) در این بازی GameMaker 8 پیدا نشد.")
    if limit:
        items = items[:limit]
    trs, why = translate([i["text"] for i in items])
    entries = []
    for it, fa in zip(items, trs):
        if fa and '"' not in fa and "'" not in fa:
            entries.append({"id": it["id"], "fa": fa, "mode": it["mode"], "font": it["font"]})
    with tempfile.TemporaryDirectory(prefix="trarium_gm8_") as tmp:
        tr = Path(tmp) / "tr.json"
        tr.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        out = Path(tmp) / "out.exe"
        rep = build(str(path), str(tr), str(out), log=lambda *_: None)
        import os
        import shutil
        tmp_out = path.with_name(path.name + ".trarium-tmp")
        shutil.copyfile(out, tmp_out)
        os.replace(tmp_out, path)     # new inode: a hard-linked upload stays original
    return {"segments": len(items), "translated": rep["translated"], "kept_original": len(items) - rep["translated"],
            "rejected": why, "layout_problems": len(rep["layout_problems"]), "typewriter": info["typewriter"],
            "unsupported": info["unsupported"], "glyph_forms": rep["glyph_forms"],
            "samples": [{"en": i["text"], "fa": t} for i, t in list(zip(items, trs))[:12]]}
