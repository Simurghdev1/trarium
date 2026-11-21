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


def _file_md5(p: Path) -> str:
    import hashlib
    h = hashlib.md5()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(4 * 2**20), b""):
            h.update(block)
    return h.hexdigest()


def _snapshot(root: Path) -> dict[str, str]:
    """هش فایل‌ها به‌صورت جریانی (فایل‌های چندگیگی کامل در حافظه خوانده نمی‌شوند)."""
    return {str(p.relative_to(root)): _file_md5(p) for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def translate_game(source: Path, outdir: Path, pipeline, limit: int | None = None, in_place: bool = False) -> dict:
    """source: پوشه یا zip. خروجی در outdir: <name>_fa_patch.zip، <name>_fa_full.zip، vn_report.json.

    in_place=True: پوشهٔ موقتِ خودِ فراخواننده مستقیم ویرایش می‌شود (بدون کپی دوم؛ برای بازی‌های چندگیگی).
    """
    started = time.time()
    source, outdir = Path(source), Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    work = None if (in_place and source.is_dir()) else Path(tempfile.mkdtemp(prefix="trarium_vn_"))
    if work is None:
        name = source.name
        work = source
    elif source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as z:
            z.extractall(work)
        name = source.stem
    else:
        shutil.copytree(source, work / source.name)
        name = source.name
    root = find_root(work)
    engine = detect_engine(root)
    if engine == "unknown":
        engine = "generic"
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
    elif engine == "rgss":
        from trarium.vn.rgss import engine as rg
        items, docs = rg.extract(root)
        if not items:
            raise RuntimeError("هیچ متنی در پوشهٔ Data پیدا نشد.")
        if limit:
            items = items[:limit]
        trs, why = _protected_translate(pipeline, [i.text for i in items], rg.PROTECT)
        stats = rg.apply(items, trs, docs)
        report.update({"segments": stats["segments"], "translated": stats["written"],
                       "kept_original": stats["kept_original"], "rejected": why,
                       "by_kind": {k: sum(1 for i in items if i.kind == k) for k in ("say", "choice", "db")},
                       "samples": [{"en": i.text, "fa": t} for i, t in list(zip(items, trs))[:12]],
                       "note": "متن‌ها شکل‌دهی و راست‌به‌چپ ذخیره شدند. فونت پیش‌فرض بازی باید حروف فارسی داشته "
                               "باشد (در Game.ini یا اسکریپت Font.default_name را روی Tahoma/Arial بگذارید)."})
    elif engine == "generic":
        from trarium.vn import generic as gn
        docs, binaries = gn.scan(root)
        units = gn.candidates(docs)
        if not units:
            msg = "هیچ موتور شناخته‌شده‌ای پیدا نشد و هیچ فایل متنی قابل ترجمه‌ای هم نبود."
            if binaries:
                msg += (f" {len(binaries)} فایل باینری رشته‌ی متنی داشتند (مثلاً {binaries[0]['file']}) ولی "
                        "ساختارشان ناشناخته است و تغییرشان بازی را خراب می‌کند، پس دست نخوردند.")
            raise RuntimeError(msg)
        decision = gn.ai_classify(getattr(pipeline, "translator", None), units)
        sel = gn.select(units, decision)
        if limit:
            sel = sel[:limit]
        if not sel:
            raise RuntimeError("متن‌هایی پیدا شد، اما هیچ‌کدام متن دیده‌شده توسط بازیکن تشخیص داده نشد.")
        trs, why = _protected_translate(pipeline, [u.text for u in sel], gn.PROTECT)
        stats = gn.apply(sel, trs, docs)
        report.update({"segments": len(sel), "translated": stats["written"], "kept_original": len(sel) - stats["written"],
                       "rejected": why, "candidates": len(units), "ai_classified": decision is not None,
                       "binary_untouched": binaries[:50], "encoding_notes": stats["encoding_notes"],
                       "samples": [{"file": str(u.file.relative_to(root)), "src": u.text, "fa": t}
                                   for u, t in list(zip(sel, trs))[:12]],
                       "note": "موتور بازی شناخته نشد؛ مسیر عمومی: فایل‌های متنی پیدا شدند و "
                               + ("هوش مصنوعی متن‌های دیده‌شده را انتخاب کرد. " if decision is not None else
                                  "انتخاب متن با قاعدهٔ محتاطانه انجام شد (پاسخ دسته‌بندی هوش مصنوعی در دسترس نبود). ")
                               + "متن فارسی بدون شکل‌دهی ذخیره شده؛ اگر بازی حروف فارسی را جدا یا برعکس نشان داد، "
                                 "موتور آن از راست‌به‌چپ پشتیبانی نمی‌کند."
                               + (f" {len(binaries)} فایل باینری دست نخورد." if binaries else "")})
    elif engine == "rgss-archived":
        raise RuntimeError("این بازی RPG Maker XP/VX/VX Ace داده‌هایش را در آرشیو رمزشدهٔ Game.rgssad/rgss2a/rgss3a "
                           "دارد؛ پوشهٔ Data باز لازم است (فعلاً باز کردن این آرشیو پشتیبانی نمی‌شود).")
    elif engine == "gamemaker":
        from trarium.vn.gms import text as gtx
        from trarium.vn.gms.build import find_data_file, translate_file
        data = find_data_file(root)
        rep = translate_file(data, lambda texts: _protected_translate(pipeline, texts, gtx.PROTECT), limit)
        fonts = rep.get("fonts") or {}
        report.update({"segments": rep["segments"], "translated": rep["written"], "kept_original": rep["kept_original"],
                       "rejected": rep["rejected"], "data_file": str(data.relative_to(root)),
                       "skipped_single_words": rep.get("skipped_single_words", 0),
                       "glyph_forms": rep.get("glyph_forms", 0),
                       "fonts_patched": len(fonts.get("patched", [])), "fonts_skipped": fonts.get("skipped", []),
                       "samples": rep["samples"],
                       "note": "فایل داده (data.win/game.droid/...) فارسی شد و حروف فارسی به فونت‌های بازی اضافه شدند. "
                               "کلمه‌های تکی (مثل کلید تنظیمات) برای جلوگیری از خراب شدن بازی ترجمه نشدند. "
                               "بازی‌هایی که متن را حرف‌به‌حرف با فاصلهٔ ثابت می‌نویسند (مثل Undertale/Deltarune) "
                               "ممکن است حروف را جدا از هم نشان دهند."})
    elif engine == "gm8":
        from trarium.vn.gm8 import fa_text as g8tx
        from trarium.vn.gm8.auto import find_gm8_exe, translate_exe
        exe = find_gm8_exe(root)
        rep = translate_exe(exe, lambda texts: _protected_translate(pipeline, texts, g8tx.CODE_RE), limit)
        report.update({k: rep[k] for k in ("segments", "translated", "kept_original", "rejected", "samples",
                                            "layout_problems", "unsupported", "typewriter")})
        report["data_file"] = str(exe.relative_to(root))
        report["note"] = ("فایل exe بازی GameMaker 8 با متن فارسی ساخته شد (آزمایشی). "
                          + ("" if rep["typewriter"] else "نویسندهٔ دیالوگ این بازی شناخته نشد؛ فقط برچسب‌های draw_text ترجمه شدند. "))
    else:
        raise RuntimeError(f"موتور این بازی پشتیبانی نمی‌شود ({engine}). فعلاً Ren'Py، RPG Maker، TyranoScript و GameMaker.")

    after = _snapshot(root)
    changed_files = sorted(k for k, v in after.items() if before.get(k) != v)
    patch = outdir / f"{name}_fa_patch.zip"
    with zipfile.ZipFile(patch, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in changed_files:
            z.write(root / rel, rel)
        z.writestr("README_FA.txt", "محتوای این زیپ را در پوشهٔ اصلی بازی کپی کنید و جایگزین کنید. ساخته‌شده با تراریوم.\n")
    full = outdir / f"{name}_fa_full.zip"
    if engine in ("tyrano", "gamemaker", "gm8", "rgss", "generic"):   # بدون بازتوزیع موتور/کل بازی؛ پچ کافی است
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
