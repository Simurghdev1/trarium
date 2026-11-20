"""اجرای کامل: باز کردن بازی، تشخیص موتور، ترجمه، فونت، و ساخت پچ و نسخهٔ کامل."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from trarium.structured import translate_segments
from trarium.vn import renpy as rp
from trarium.vn import rpgmaker as rm
from trarium.vn import tyrano as ty
from trarium.vn.detect import detect_engine, find_root

FONT = Path(__file__).resolve().parents[1] / "fonts" / "Sahel.ttf"
FA = re.compile(r"[\u0600-\u06FF]")


def _protected_translate(pipeline, texts: list[str], protect_rx: re.Pattern) -> tuple[list[str | None], dict]:
    """کدها را قفل می‌کند، ترجمه می‌کند و هر ترجمه‌ای که کد گم کرده یا فارسی ندارد را رد می‌کند."""
    prepared, maps = [], []
    for t in texts:
        mapping: dict[str, str] = {}

        def repl(m, mapping=mapping):
            tok = f"⟦{len(mapping)}⟧"
            mapping[tok] = m.group(0)
            return tok

        prepared.append(protect_rx.sub(repl, t))
        maps.append(mapping)
    raw = translate_segments(pipeline, prepared)
    out: list[str | None] = []
    why = {"no_persian": 0, "lost_code": 0}
    for src, tr, mapping in zip(prepared, raw, maps):
        if not tr or tr.strip() == src.strip() or not FA.search(tr):
            out.append(None); why["no_persian"] += 1; continue
        restored = tr
        ok = True
        for tok, orig in mapping.items():
            if tok not in restored:
                ok = False; break
            restored = restored.replace(tok, orig, 1)
        if not ok or "⟦" in restored:
            out.append(None); why["lost_code"] += 1; continue
        out.append(restored.strip())
    return out, why


def _snapshot(root: Path) -> dict[str, str]:
    import hashlib
    return {str(p.relative_to(root)): hashlib.md5(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def translate_game(source: Path, outdir: Path, pipeline, limit: int | None = None) -> dict:
    """source: پوشه یا zip. خروجی در outdir: <name>_fa_patch.zip، <name>_fa_full.zip، vn_report.json."""
    started = time.time()
    source, outdir = Path(source), Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="trarium_vn_"))
    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as z:
            z.extractall(work)
        name = source.stem
    else:
        shutil.copytree(source, work / source.name)
        name = source.name
    root = find_root(work)
    engine = detect_engine(root)
    report: dict = {"game": name, "engine": engine}
    before = _snapshot(root)

    if engine == "renpy":
        game = root / "game"
        if not rp.script_files(game):
            for rpa in game.glob("*.rpa"):
                report.setdefault("unpacked", []).extend(str(p.relative_to(root)) for p in rp.unpack_rpa(rpa, game))
        segs = rp.extract(game)
        if not segs:
            raise RuntimeError("هیچ دیالوگی پیدا نشد؛ احتمالاً بازی فقط .rpyc دارد (نیاز به unrpyc).")
        if limit:
            segs = segs[:limit]
        trs, why = _protected_translate(pipeline, [s.text.replace("\n", " ") for s in segs], rp.PROTECT)
        trs = [t.replace("\n", " ") if t else t for t in trs]
        stats = rp.apply(segs, trs)
        rp.install_font(game, FONT)
        report.update({"segments": stats["segments"], "translated": stats["written"],
                       "kept_original": stats["kept_original"], "rejected": why,
                       "by_kind": {k: sum(1 for s in segs if s.kind == k) for k in ("say", "menu", "ui", "name")}})
        report["samples"] = [{"file": str(s.file.relative_to(root)), "line": s.line + 1, "en": s.text, "fa": t}
                             for s, t in list(zip(segs, trs))[:12]]
    elif engine == "rpgmaker":
        docs, col = rm.extract(root)
        idx = list(range(len(col.texts)))[: limit or None]
        trs, why = _protected_translate(pipeline, [col.texts[i] for i in idx], rm.PROTECT)
        changed = set()
        done = 0
        for i, tr in zip(idx, trs):
            if tr is not None:
                col.setters[i](tr); changed.add(col.owners[i]); done += 1
        rm.save(docs, changed)
        rm.install_font(root, FONT)
        report.update({"segments": len(idx), "translated": done, "kept_original": len(idx) - done, "rejected": why,
                       "note": "متن‌ها فارسی شدند؛ برای چسبیدن حروف و راست‌به‌چپ در پنجرهٔ پیام هنوز پلاگین RTL لازم است."})
        report["samples"] = [{"en": col.texts[i], "fa": t} for i, t in list(zip(idx, trs))[:12]]
    elif engine == "tyrano":
        troot = ty.find_root(root)
        segs, docs = ty.extract(troot)
        if not segs:
            raise RuntimeError("هیچ متنی در data/scenario پیدا نشد.")
        if limit:
            segs = segs[:limit]
        trs, why = _protected_translate(pipeline, [s.text for s in segs], ty.PROTECT)
        stats = ty.apply(segs, trs, docs)
        ty.install_font(troot, FONT)
        report.update({"segments": stats["segments"], "translated": stats["written"],
                       "kept_original": stats["kept_original"], "rejected": why,
                       "by_kind": {k: sum(1 for s in segs if s.kind == k) for k in ("text", "name", "jname", "attr")},
                       "note": "مجوز TyranoScript بازتوزیع موتور را منع می‌کند؛ خروجی فقط پچ است."})
        report["samples"] = [{"file": str(s.file.relative_to(root)), "line": s.line + 1, "src": s.text, "fa": t}
                             for s, t in list(zip(segs, trs))[:12]]
    else:
        raise RuntimeError(f"موتور این بازی پشتیبانی نمی‌شود ({engine}). فعلاً Ren'Py، RPG Maker MV/MZ و TyranoScript.")

    after = _snapshot(root)
    changed_files = sorted(k for k, v in after.items() if before.get(k) != v)
    patch = outdir / f"{name}_fa_patch.zip"
    with zipfile.ZipFile(patch, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in changed_files:
            z.write(root / rel, rel)
        z.writestr("README_FA.txt", "محتوای این زیپ را در پوشهٔ اصلی بازی کپی کنید و جایگزین کنید. ساخته‌شده با تراریوم.\n")
    full = outdir / f"{name}_fa_full.zip"
    if engine == "tyrano":      # بدون بازتوزیع موتور
        full = None
    else:
        with zipfile.ZipFile(full, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(root))
    report.update({"changed_files": changed_files, "patch_zip": patch.name, "full_zip": full.name if full else None,
                   "seconds": round(time.time() - started, 1), "game_root": str(root)})
    status = getattr(getattr(pipeline, "translator", None), "status", None)
    if callable(status):
        report["providers"] = status()
    (outdir / "vn_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
