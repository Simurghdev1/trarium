"""Real test stages for the Trarium full audit.

Usage: ..venv/bin/python qa/full_audit/stages.py <stage>
Stages: repair, undine6, manga, epub, site_witchcult, site_vcv, youtube,
        library, formats, manga_quality
"""
from __future__ import annotations

import json
import os
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
from common import FILES, MODEL, OUT, PROVIDER, SHORT_PROMPT, log_event, out_dir, result  # noqa: E402


# --------------------------------------------------------------------- repair
def stage_repair() -> None:
    from trarium.repair import analyze_book, analyze_pdf, repair_pdf

    folder = out_dir("repair")
    source = FILES / "Undine_Original_First_3_Pages.pdf"
    before = analyze_book(source)
    payload = {"source": str(source), "pages": before.pages, "text_pages": before.text_pages,
               "scanned_pages": before.scanned_pages, "score_before": before.quality_score,
               "issues": [i.to_dict() for i in before.issues],
               "summary_before": before.summary_fa()}
    try:
        pdf_report = analyze_pdf(source)
        payload["pdf_issues"] = [i.to_dict() for i in pdf_report.issues]
        payload["duplicates"] = pdf_report.duplicates
        payload["blank_pages"] = pdf_report.blank_pages
        payload["corrupt_pages"] = pdf_report.corrupt_pages
        payload["detected_order"] = pdf_report.detected_order
        output = folder / "Undine_Original_First_3_Pages_repaired.pdf"
        repaired = repair_pdf(source, output, pdf_report, drop_duplicates=True,
                              drop_blank=False, fix_order=True)
        payload.update({"output": str(output), "pages_before": repaired.pages_before,
                        "pages_after": repaired.pages_after, "removed": repaired.removed,
                        "reordered": repaired.reordered, "log": repaired.log,
                        "source_unchanged": repaired.source_unchanged,
                        "output_bytes": output.stat().st_size if output.exists() else 0})
        after = analyze_book(output)
        payload["score_after"] = after.quality_score
        payload["status"] = "passed" if output.exists() and output.stat().st_size > 0 else "failed"
    except Exception as exc:  # honest failure capture
        payload.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    result("repair", **payload)


# ---------------------------------------------------------------- pdf layout
def _page_count(path: Path) -> int:
    import pymupdf

    with pymupdf.open(str(path)) as document:
        return len(document)


def _pdf(path: Path, stage: str, enable_ocr: bool, ocr_method: str, vision_model: str = "") -> None:
    from trarium.pdf_optimized import translate_pdf_optimized

    folder = out_dir(stage)
    output = folder / (path.stem + "_fa.pdf")
    started = time.time()
    stats = None
    try:
        _, stats = translate_pdf_optimized(
            str(path), common.translator(), "fa", str(output),
            api_key=os.environ.get("OPENROUTER_API_KEY"), vision_model=vision_model or None,
            enable_ocr=enable_ocr, ocr_method=ocr_method, translation_workers=4,
            translation_batch_chars=12000, short_prompt=SHORT_PROMPT, return_stats=True,
            use_checkpoint=True, use_cache=True,
        )
        # A 16-page image-only comic that writes text on one page is a failure,
        # not a pass: judge coverage, not just the existence of the file.
        ok = bool(output.exists())
        if stats and getattr(stats, "pages_with_text", 0):
            ok = ok and stats.pages_with_text >= max(1, int(0.6 * _page_count(path)))
        result(stage, status="passed" if ok else "failed", output=str(output),
               input=str(path), pages_with_text=getattr(stats, "pages_with_text", 0),
               pages_total=_page_count(path),
               bytes=output.stat().st_size if output.exists() else 0,
               seconds=round(time.time() - started, 1), stats=stats.__dict__ if stats else {},
               provider=PROVIDER, model=MODEL, short_prompt=SHORT_PROMPT)
    except Exception as exc:
        result(stage, status="failed", input=str(path), seconds=round(time.time() - started, 1),
               error=f"{type(exc).__name__}: {exc}")


def stage_undine6() -> None:
    _pdf(FILES / "Undine_Trarium_Test_6_Pages.pdf", "undine6", False, "auto")


def stage_manga() -> None:
    _pdf(FILES / "Josep Moore - Mangas ENG.pdf", "manga", True, "gemini")


# ---------------------------------------------------------------------- epub
def stage_epub() -> None:
    from trarium.extractors import get_extractor
    from trarium.output import save_as_epub
    from trarium.processors.pipeline import TranslationPipeline

    folder = out_dir("epub")
    source = FILES / "_OceanofPDF.com_Omniscient_readers_viewpoint_novel_2_-_singNsong.epub"
    started = time.time()
    try:
        book = get_extractor(str(source), use_ocr=False).extract(str(source))
        log_event("epub", step="extracted", chapters=len(book.chapters),
                  chars=book.total_chars)
        pipe = TranslationPipeline(common.translator(), "fa", chunk_size=12000,
                                   max_workers=6, short_prompt=SHORT_PROMPT,
                                   use_checkpoint=True, translation_memory=True)
        translated = pipe.run(book)
        output = folder / "omniscient-vol2-fa.epub"
        save_as_epub(translated, str(output))
        result("epub", status="passed" if output.exists() else "failed", output=str(output),
               bytes=output.stat().st_size if output.exists() else 0,
               seconds=round(time.time() - started, 1), chapters=len(book.chapters),
               source_chars=book.total_chars, translated_chars=translated.translated_chars,
               chunks=translated.total_chunks, translated_chunks=translated.translated_chunks,
               failed_chunks=translated.failed_chunks, provider=PROVIDER, model=MODEL,
               short_prompt=SHORT_PROMPT)
    except Exception as exc:
        result("epub", status="failed", seconds=round(time.time() - started, 1),
               error=f"{type(exc).__name__}: {exc}")


# -------------------------------------------------------------------- website
def _site(url: str, stage: str, max_pages: int) -> None:
    from trarium.output import save_as_pdf
    from trarium.websites import translate_site

    folder = out_dir(stage)
    started = time.time()
    try:
        site = translate_site(url, common.throttled(), "fa", max_pages=max_pages,
                              delay=0.3, page_cache_dir=folder / "page-cache")
        html_path = site.save(folder, "html")
        txt_path = site.save(folder, "txt")
        epub_path = site.save(folder, "epub")
        pdf_path = folder / "site_fa.pdf"
        save_as_pdf(site.to_result(), str(pdf_path))
        result(stage, status="passed" if site.pages_ok else "failed", url=url,
               output=str(pdf_path), html=str(html_path), txt=str(txt_path),
               epub=str(epub_path), pages=len(site.pages), pages_ok=site.pages_ok,
               pages_failed=site.pages_failed, chars=site.total_chars,
               errors=site.errors, titles=[p.title for p in site.pages],
               seconds=round(time.time() - started, 1), provider=PROVIDER, model=MODEL)
    except Exception as exc:
        result(stage, status="failed", url=url, seconds=round(time.time() - started, 1),
               error=f"{type(exc).__name__}: {exc}")


def stage_site_witchcult() -> None:
    _site("https://witchculttranslation.com/2021/01/23/arc-7-chapter-1-initiation/",
          "site-witchcult", 4)


def stage_site_vcv() -> None:
    _site("https://vcvenglish.com/", "site-vcv", 3)


# -------------------------------------------------------------------- youtube
def stage_youtube() -> None:
    """Download the real subtitles of the video, translate them, keep timing."""
    import subprocess

    from trarium.processors.pipeline import TranslationPipeline
    from trarium.subtitles import read_subtitles

    folder = out_dir("youtube")
    url = "https://www.youtube.com/watch?v=5XLEjBlSaAY"
    started = time.time()
    payload = {"url": url, "status": "failed"}
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "yt-dlp"], check=True,
                       timeout=240)
        subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download",
                        "--write-auto-subs", "--write-subs", "--sub-langs", "en.*,fa.*",
                        "--sub-format", "vtt", "-o", str(folder / "video.%(ext)s"), url],
                       check=True, timeout=300, capture_output=True, text=True)
        candidates = sorted(folder.glob("video*.vtt"))
        payload["subtitle_files"] = [p.name for p in candidates]
        if not candidates:
            payload["error"] = "هیچ زیرنویسی برای این ویدیو پیدا نشد (بدون زیرنویس یا مسدود)."
            result("youtube", **payload)
            return
        parsed = read_subtitles(candidates[0])
        payload.update({"kind": parsed.kind, "cues": len(parsed.cues)})
        pipe = TranslationPipeline(common.translator(), "fa", max_workers=4,
                                   short_prompt=SHORT_PROMPT)
        texts = [cue.text for cue in parsed.cues]
        translated = [pipe.translate_block(text) if text.strip() else "" for text in texts]
        srt_path = folder / "video_fa.srt"
        srt_path.write_text(parsed.render(translated), encoding="utf-8")
        payload.update({"status": "passed" if srt_path.exists() else "failed",
                        "output": str(srt_path), "bytes": srt_path.stat().st_size,
                        "seconds": round(time.time() - started, 1),
                        "provider": PROVIDER, "model": MODEL})
    except Exception as exc:
        payload.update({"error": f"{type(exc).__name__}: {exc}",
                        "seconds": round(time.time() - started, 1)})
    result("youtube", **payload)


# -------------------------------------------------------------------- library
def stage_library() -> None:
    from trarium.library import Library

    library = Library()
    payload = {"root": str(library.root), "books": []}
    wanted = ["Undine_Trarium_Test_6_Pages.pdf", "Josep Moore - Mangas ENG.pdf",
              "_OceanofPDF.com_Omniscient_readers_viewpoint_novel_2_-_singNsong.epub",
              "MU_KPB_013_Undine_-_Illustrated_by_Arthur_Rackham.pdf"]
    try:
        for name in wanted:
            book = library.add_book(FILES / name, category="تست کامل ۲.۱۹")
            payload["books"].append({"id": book["id"], "title": book["title"],
                                     "ext": book["ext"], "status": book["status"]})
        playlists = {p["name"]: p for p in library.list_playlists()}
        playlist = playlists.get("تست کامل ۲.۱۹") or library.create_playlist("تست کامل ۲.۱۹")
        for book in payload["books"]:
            library.add_to_playlist(playlist["id"], book["id"])
        payload.update({
            "status": "passed", "playlist": playlist["name"],
            "playlist_count": library.playlist(playlist["id"])["count"],
            "duplicate_check": library.add_book(FILES / wanted[0])["id"],
            "search": [b["title"] for b in library.list_books(search="Undine")],
            "stats": library.stats(),
        })
    except Exception as exc:
        payload.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    result("library", **payload)


# -------------------------------------------------------------------- formats
def stage_formats() -> None:
    from trarium.convert import convert_file

    folder = out_dir("formats")
    payload = {"status": "passed", "conversions": []}
    sources = [folder.parent / "undine6" / "Undine_Trarium_Test_6_Pages_fa.pdf",
               FILES / "Josep Moore - Mangas ENG.pdf"]
    for source in sources:
        if not source.exists():
            payload["conversions"].append({"source": source.name, "status": "skipped",
                                           "reason": "خروجی مرحلهٔ قبل موجود نیست"})
            continue
        for fmt in ("docx", "html", "txt", "epub"):
            target = folder / f"{source.stem}.{fmt}"
            try:
                report = convert_file(str(source), str(target), fmt)
                ok = target.exists() and target.stat().st_size > 0
                if ok and fmt == "epub":
                    with zipfile.ZipFile(target) as archive:
                        ok = "mimetype" in archive.namelist()
                payload["conversions"].append({
                    "source": source.name, "format": fmt, "status": "passed" if ok else "failed",
                    "bytes": target.stat().st_size if target.exists() else 0,
                    "report": report.__dict__ if hasattr(report, "__dict__") else str(report),
                })
            except Exception as exc:
                payload["conversions"].append({"source": source.name, "format": fmt,
                                               "status": "failed",
                                               "error": f"{type(exc).__name__}: {exc}"})
    if any(item["status"] == "failed" for item in payload["conversions"]):
        payload["status"] = "failed"
    result("formats", **payload)


# ------------------------------------------------------------- manga quality
def stage_manga_quality() -> None:
    import numpy as np
    from PIL import Image, ImageFilter

    from trarium.manga import MangaSettings, analyze, enhance

    folder = out_dir("manga-quality")
    rng = np.random.default_rng(7)
    page = Image.new("L", (820, 1200), 246)
    pixels = np.asarray(page, dtype=np.float32)
    pixels[80:1120, 60:760] = 235
    noise = rng.normal(0, 12, pixels.shape)
    image = Image.fromarray(np.clip(pixels + noise, 0, 255).astype(np.uint8)).convert("RGB")
    image = image.filter(ImageFilter.GaussianBlur(1.1))
    image.save(folder / "input.png")
    try:
        enhanced, report = enhance(image, MangaSettings(scale="2x"))
        enhanced.save(folder / "output.png")
        result("manga-quality", status="passed", input=str(folder / "input.png"),
               output=str(folder / "output.png"), analysis=analyze(image),
               report=report, size=[enhanced.width, enhanced.height])
    except Exception as exc:
        result("manga-quality", status="failed", error=f"{type(exc).__name__}: {exc}")


STAGES = {
    "repair": stage_repair, "undine6": stage_undine6, "manga": stage_manga,
    "epub": stage_epub, "site-witchcult": stage_site_witchcult, "site-vcv": stage_site_vcv,
    "youtube": stage_youtube, "library": stage_library, "formats": stage_formats,
    "manga-quality": stage_manga_quality,
}

if __name__ == "__main__":
    for name in sys.argv[1:] or list(STAGES):
        print(f"== {name}", flush=True)
        STAGES[name]()
