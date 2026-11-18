"""Job handlers. Each one is idempotent: completed units are skipped on resume."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from trarium.extractors import get_extractor
from trarium.extractors.base import ExtractedBook
from trarium.extractors.generic import UnsupportedFormatError, sniff_container
from trarium.output import save_as_epub, save_as_html, save_as_pdf, save_as_text
from trarium.processors import TextChunker
from trarium.processors.pipeline import TranslationPipeline


def _safe_name(value: str, fallback: str = "translated") -> str:
    name = "".join(c for c in (value or "") if c.isalnum() or c in " _-").strip()
    return name or fallback


def _pipeline(ctx, *, chunk_size: int, workers: int, delay: float, on_unit_done=None,
              target_lang: str | None = None) -> TranslationPipeline:
    cfg = ctx.config
    return TranslationPipeline(
        ctx.make_translator(),
        target_lang or ctx.job.get("target_lang") or "fa",
        max(2000, int(chunk_size)),
        float(delay),
        max(1, int(workers)),
        safe_optimization=bool(cfg.get("safe_optimization", True)),
        protect_nontranslatable=bool(cfg.get("protect_spans", True)),
        use_checkpoint=bool(cfg.get("use_checkpoint", True)),
        translation_memory=bool(cfg.get("use_cache", True)),
        adaptive_batch=bool(cfg.get("adaptive_batch", True)),
        short_prompt=str(cfg.get("short_prompt") or ""),
        control=ctx.checkpoint,
        on_unit_done=on_unit_done,
    )


def _save_book_result(ctx, result, fmt: str, stem: str) -> Path:
    fmt = (fmt or "txt").lower()
    out_dir = ctx.directory / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe_name(stem)}_{result.target_lang}.{fmt}"
    if fmt == "pdf":
        save_as_pdf(result, str(path))
    elif fmt == "epub":
        save_as_epub(result, str(path))
    elif fmt == "html":
        save_as_html(result, str(path))
    else:
        save_as_text(result, str(path))
    return path


# --------------------------------------------------------------------------- book
def handle_book(ctx) -> None:
    cfg = ctx.config
    source = Path(ctx.job.get("source_path") or "")
    if not source.exists():
        raise FileNotFoundError("فایل ورودی تسک پیدا نشد.")
    ctx.set_stage("استخراج متن")
    suffix = source.suffix.lower()
    is_pdf = suffix == ".pdf" or sniff_container(source) == "pdf"
    book = get_extractor(str(source), use_ocr=is_pdf).extract(str(source))
    if not book.chapters:
        raise UnsupportedFormatError("هیچ متن قابل ترجمه‌ای در فایل پیدا نشد.")
    chunk_size = int(cfg.get("chunk_size", 14000))
    chunker = TextChunker(max_chars=max(2000, chunk_size), overlap=0)
    chunks = chunker.chunk_by_chapters(book.chapters)
    if not chunks:
        raise UnsupportedFormatError("متنی برای ترجمه پیدا نشد.")
    ctx.ensure_units([str(c.get("title") or f"بخش {i + 1}") for i, c in enumerate(chunks)])
    if ctx.completed_indexes():
        ctx.log("بخش‌های تکمیل‌شده از Checkpoint خوانده می‌شوند و دوباره به API نمی‌روند.")
    ctx.set_stage("ترجمه")

    def on_unit(idx: int, unit: dict) -> None:
        total = len(chunks)
        ctx.unit_done(idx, failed=bool(unit.get("failed")), error=str(unit.get("error") or ""),
                      title=str(unit.get("title") or ""), total=total)

    pipeline = _pipeline(ctx, chunk_size=chunk_size, workers=int(cfg.get("workers", 3)),
                         delay=float(cfg.get("delay", 0.0)), on_unit_done=on_unit)
    ctx.checkpoint()
    result = pipeline.run(book)
    if result.translated_chunks == 0:
        raise RuntimeError("هیچ بخشی ترجمه نشد؛ خطا: "
                           + (ctx.store.events(ctx.job_id, 1)[-1]["message"] if ctx.store.events(ctx.job_id, 1) else ""))
    ctx.set_stage("ساخت خروجی")
    path = _save_book_result(ctx, result, str(cfg.get("output_format") or "txt"),
                             book.title or ctx.job["title"])
    ctx.update(total_units=len(chunks), completed_units=result.translated_chunks,
               failed_units=result.failed_chunks, input_tokens=result.estimated_input_tokens,
               output_tokens=result.estimated_output_tokens, api_requests=result.api_requests)
    ctx.finish(path)


# ---------------------------------------------------------------------------- pdf
def handle_pdf(ctx) -> None:
    import pymupdf

    from trarium.pdf_optimized import translate_pdf_optimized

    cfg = ctx.config
    source = Path(ctx.job.get("source_path") or "")
    if not source.exists():
        raise FileNotFoundError("فایل PDF تسک پیدا نشد.")
    ctx.set_stage("تحلیل صفحه‌ها")
    doc = pymupdf.open(str(source))
    total_pages = len(doc)
    doc.close()
    ctx.ensure_units([f"صفحه {i + 1}" for i in range(total_pages)])
    out = ctx.directory / "output" / f"{_safe_name(ctx.job['title'])}_fa.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    last_completed = -1

    def progress(value: float, message: str) -> None:
        nonlocal last_completed
        ctx.checkpoint()
        value = max(0.0, min(1.0, float(value)))
        completed = int(value * total_pages)
        if completed > last_completed:
            for idx in range(last_completed + 1, completed + 1):
                ctx.store.set_unit(ctx.job_id, idx, "completed")
            last_completed = completed
        stage = "OCR و تحلیل" if value < 0.40 else ("ترجمه" if value < 0.80 else "ساخت PDF")
        ctx.update(progress=value, stage=stage, current_unit=message[:300],
                   completed_units=max(0, min(total_pages, completed)))

    _, stats = translate_pdf_optimized(
        str(source), ctx.make_translator(), ctx.job.get("target_lang") or "fa", str(out),
        translation_workers=int(cfg.get("workers", 3)),
        translation_batch_chars=int(cfg.get("chunk_size", 14000)),
        progress_callback=progress, return_stats=True,
        enable_ocr=bool(cfg.get("enable_ocr", False)),
        ocr_method=str(cfg.get("ocr_method") or "auto"),
        ocr_lang=str(cfg.get("ocr_lang") or "eng"),
        api_key=str(cfg.get("ocr_api_key") or "") or None,
        vision_model=str(cfg.get("ocr_vision_model") or "") or None,
        safe_optimization=bool(cfg.get("safe_optimization", True)),
        protect_nontranslatable=bool(cfg.get("protect_spans", True)),
        adaptive_batch=bool(cfg.get("adaptive_batch", True)),
        use_checkpoint=bool(cfg.get("use_checkpoint", True)),
        use_cache=bool(cfg.get("use_cache", True)),
        short_prompt=str(cfg.get("short_prompt") or ""),
    )
    for idx in range(total_pages):
        ctx.store.set_unit(ctx.job_id, idx, "completed")
    ctx.update(progress=1.0, completed_units=total_pages, stage="PDF آماده شد",
               input_tokens=stats.input_tokens, output_tokens=stats.output_tokens,
               api_requests=stats.api_requests, failed_units=stats.failed_blocks)
    ctx.finish(out)


# -------------------------------------------------------------------------- novel
def _novel_chapters(ctx) -> list[dict]:
    cfg = ctx.config
    cache_file = ctx.directory / "chapters.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if data:
                return data
        except ValueError:
            pass
    from trarium.novel.scraper import NovelScraper
    ctx.set_stage("دریافت فصل‌ها")
    ctx.checkpoint()
    scraper = NovelScraper(use_browser=bool(cfg.get("use_browser")), delay=0.5,
                           max_chapters=int(cfg.get("chapter_count", 1)))
    chapters = scraper.scrape_chain(str(cfg.get("url") or ""), int(cfg.get("chapter_count", 1)))
    data = [{"number": c.number, "title": c.title, "text": c.text, "url": c.url} for c in chapters]
    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def handle_novel(ctx) -> None:
    cfg = ctx.config
    chapters = _novel_chapters(ctx)
    if not chapters:
        raise RuntimeError("هیچ فصلی از این لینک دریافت نشد.")
    start = max(1, int(cfg.get("chapter_start") or 1))
    end = cfg.get("chapter_end")
    end = int(end) if end not in (None, "", 0) else len(chapters)
    selected = [c for i, c in enumerate(chapters, 1) if start <= i <= min(end, len(chapters))]
    if not selected:
        raise RuntimeError(f"در بازهٔ {start} تا {end} فصلی وجود ندارد.")
    ctx.ensure_units([f"فصل {c.get('number') or i + 1}: {c.get('title') or ''}".strip()
                      for i, c in enumerate(selected)])
    translated_file = ctx.directory / "translated_chapters.json"
    try:
        translated = json.loads(translated_file.read_text(encoding="utf-8")) if translated_file.exists() else {}
    except ValueError:
        translated = {}
    cfg_prompt = str(cfg.get("short_prompt") or "")
    ctx.set_stage("ترجمه فصل‌ها")
    for idx, chapter in enumerate(selected):
        if ctx.is_done(idx):
            continue
        ctx.unit_started(idx, f"فصل {chapter.get('number') or idx + 1}")
        try:
            text = str(chapter.get("text") or "").strip()
            if not text:
                raise RuntimeError("متن فصل خالی است")
            book = ExtractedBook(title=str(chapter.get("title") or f"فصل {idx + 1}"),
                                 author="", chapters=[{"title": str(chapter.get("title") or ""), "content": text}],
                                 total_chars=len(text))
            pipeline = _pipeline(ctx, chunk_size=int(cfg.get("chunk_size", 14000)),
                                 workers=1, delay=float(cfg.get("delay", 0.0)))
            result = pipeline.run(book)
            translated[str(idx)] = {"title": book.title, "number": chapter.get("number"),
                                    "text": result.translated_text, "url": chapter.get("url", "")}
            translated_file.write_text(json.dumps(translated, ensure_ascii=False), encoding="utf-8")
            ctx.unit_done(idx, title=book.title)
        except Exception as exc:
            ctx.unit_done(idx, failed=True, error=str(exc),
                          title=str(chapter.get("title") or f"فصل {idx + 1}"))
            continue
        ctx.checkpoint()
    ok = [translated[str(i)] for i in range(len(selected)) if str(i) in translated]
    if not ok:
        raise RuntimeError("هیچ فصلی ترجمه نشد؛ خطای هر فصل در فهرست واحدها ثبت شده است.")
    ctx.set_stage("ساخت خروجی")
    from trarium.processors.pipeline import TranslationResult
    body = "\n\n".join(f"--- {item['title']} ---\n\n{item['text']}" for item in ok)
    result = TranslationResult(title=str(cfg.get("novel_title") or ok[0]["title"] or "Web Novel"),
                               author="", original_lang="auto",
                               target_lang=ctx.job.get("target_lang") or "fa",
                               translated_text=body, total_chunks=len(selected),
                               translated_chunks=len(ok), failed_chunks=len(selected) - len(ok))
    path = _save_book_result(ctx, result, str(cfg.get("output_format") or "epub"),
                             result.title or ctx.job["title"])
    ctx.finish(path)


# ------------------------------------------------------------------------ website
def handle_website(ctx) -> None:
    from trarium.websites import translate_site

    cfg = ctx.config
    ctx.set_stage("دریافت صفحه‌ها")

    def on_page(index: int, url: str, status: str, error: str = "") -> None:
        total = max(index + 1, int((ctx.store.get(ctx.job_id) or {}).get("total_units") or 0))
        if status == "started":
            ctx.store.set_unit(ctx.job_id, index, "running", title=url)
            ctx.update(total_units=total, current_unit=url[:300],
                       progress=min(0.99, index / max(total, 1)))
            ctx.checkpoint()
            return
        if status == "failed":
            ctx.store.set_unit(ctx.job_id, index, "failed", error)
            ctx.log(f"صفحه ناموفق: {url} — {error}", "error")
        else:
            ctx.store.set_unit(ctx.job_id, index, "completed")
        units = ctx.store.units(ctx.job_id)
        done = sum(1 for u in units if u["status"] == "completed")
        failed = sum(1 for u in units if u["status"] == "failed")
        ctx.update(completed_units=done, failed_units=failed,
                   progress=min(0.99, (done + failed) / max(total, 1)))
        ctx.checkpoint()

    result = translate_site(
        str(cfg.get("url") or ""),
        ctx.make_translator(),
        target_lang=ctx.job.get("target_lang") or "fa",
        max_pages=int(cfg.get("max_pages", 1)),
        same_domain=bool(cfg.get("same_domain", True)),
        delay=float(cfg.get("delay", 0.5)),
        allow_browser=bool(cfg.get("use_browser")),
        on_page=on_page,
        checkpoint=ctx.checkpoint,
        project=ctx.job["title"],
        job_id=ctx.job_id,
        page_cache_dir=ctx.directory / "pages",
    )
    if not result.pages_ok:
        raise RuntimeError("هیچ صفحه‌ای با موفقیت ترجمه نشد. " + (result.errors[0] if result.errors else ""))
    ctx.set_stage("ساخت خروجی")
    path = result.save(ctx.directory / "output", fmt=str(cfg.get("output_format") or "html"))
    ctx.update(total_units=len(result.pages), completed_units=result.pages_ok,
               failed_units=result.pages_failed, progress=1.0)
    ctx.finish(path)


# --------------------------------------------------------------------------- manga
def handle_manga(ctx) -> None:
    from trarium.manga import MangaSettings, enhance_file

    cfg = ctx.config
    settings = MangaSettings.from_dict(cfg.get("manga") or {})
    source = Path(ctx.job.get("source_path") or "")
    if not source.exists():
        raise FileNotFoundError("فایل ورودی تسک پیدا نشد.")
    work = ctx.directory / "work"
    work.mkdir(parents=True, exist_ok=True)
    enhanced_dir = ctx.directory / "enhanced"
    enhanced_dir.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
                    target = work / Path(name).name
                    target.write_bytes(archive.read(name))
                    images.append(target)
    elif source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        images = [source]
    if not images:
        raise RuntimeError("تصویری برای افزایش کیفیت پیدا نشد. ZIP یا تصویر بفرستید.")
    ctx.ensure_units([p.name for p in images])
    reports = []
    for idx, image in enumerate(images):
        if ctx.is_done(idx):
            continue
        ctx.unit_started(idx, image.name)
        ctx.checkpoint()
        try:
            report = enhance_file(image, enhanced_dir / image.name, settings)
            reports.append(report)
            ctx.unit_done(idx, title=image.name, failed=not report.get("ok"),
                          error=report.get("error", ""))
        except Exception as exc:
            ctx.unit_done(idx, failed=True, error=str(exc), title=image.name)
    if not any(enhanced_dir.iterdir()):
        raise RuntimeError("هیچ تصویری پردازش نشد.")
    out_dir = ctx.directory / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{_safe_name(ctx.job['title'])}_enhanced.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(enhanced_dir.iterdir()):
            if file.is_file():
                archive.write(file, file.name)
        archive.writestr("report.json", json.dumps(reports, ensure_ascii=False, indent=2))
    ctx.finish(zip_path)
