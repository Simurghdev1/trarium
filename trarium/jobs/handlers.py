"""Job handlers. Each one is idempotent: completed units are skipped on resume."""
from __future__ import annotations

import os

import json
import re
import zipfile
from pathlib import Path

from trarium.extractors import get_extractor
from trarium.extractors.base import ExtractedBook
from trarium.extractors.formats import SUBTITLE_EXTENSIONS
from trarium.extractors.generic import UnsupportedFormatError, sniff_container
from trarium.output import save_as_epub, save_as_html, save_as_pdf, save_as_text
from trarium.processors import TextChunker
from trarium.processors.pipeline import TranslationPipeline


def _safe_name(value: str, fallback: str = "translated") -> str:
    name = "".join(c for c in (value or "") if c.isalnum() or c in " _-").strip()
    return name or fallback


def _parse_page_selection(value) -> set[int]:
    """"1,3-5" or [1, 3, 5] → zero-based page indexes."""
    if not value:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {max(0, int(item) - 1) for item in value if str(item).strip().isdigit()}
    pages: set[int] = set()
    for part in str(value).replace("،", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            if start.strip().isdigit() and end.strip().isdigit():
                first, last = int(start), int(end)
                pages.update(range(max(1, first) - 1, max(first, last)))
        elif part.isdigit() and int(part) > 0:
            pages.add(int(part) - 1)
    return pages


def _title_stem(ctx, source: Path | None = None) -> str:
    """Human file name for outputs (never the internal input.<ext>)."""
    display = str(ctx.config.get("display_name") or "")
    if display:
        return _safe_name(Path(display).stem)
    title = str(ctx.job.get("title") or "")
    if title and Path(title).stem not in {"input"}:
        return _safe_name(title)
    if source is not None:
        return _safe_name(source.stem)
    return "translated"


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


def _ledger(ctx, source: Path):
    """دفتر مراحل Smart Resume؛ خطای آن هرگز ترجمه را متوقف نمی‌کند."""
    try:
        from trarium.resume import ResumeLedger, project_key
        cfg = ctx.config
        target_lang = ctx.job.get("target_lang") or "fa"
        key = project_key(source, target_lang, str(cfg.get("provider") or ""),
                          str(cfg.get("model") or ""))
        ledger = ResumeLedger()
        ledger.ensure(key, title=ctx.job.get("title") or source.name, source_path=str(source),
                      target_lang=target_lang, provider=str(cfg.get("provider") or ""),
                      model=str(cfg.get("model") or ""))
        return ledger, key
    except Exception:
        return None, None


def _save_book_result(ctx, result, fmt: str, stem: str) -> Path:
    fmt = (fmt or "txt").lower()
    out_dir = ctx.directory / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_safe_name(stem)}_{result.target_lang}.{fmt}"
    if fmt == "pdf":
        save_as_pdf(result, str(path))
    elif fmt == "epub":
        from trarium.epub_cover import cover_from_source
        save_as_epub(result, str(path), cover=cover_from_source(ctx.job.get("source_path")),
                     compress=bool((ctx.config or {}).get("epub_compress", True)))
    elif fmt == "html":
        save_as_html(result, str(path))
    else:
        save_as_text(result, str(path))
    return path


# -------------------------------------------------------------------------- office
def _handle_office(ctx, source: Path, suffix: str) -> bool:
    """DOCX/PPTX/XLSX را در همان فرمت و با همان ساختار برمی‌گرداند."""
    from trarium.office import SUPPORTED_EXTENSIONS, translate_office

    if suffix not in SUPPORTED_EXTENSIONS:
        return False
    cfg = ctx.config
    ctx.set_stage("ترجمه سند آفیس")
    ctx.ensure_units([f"سند {suffix}"])
    pipeline = _pipeline(ctx, chunk_size=int(cfg.get("chunk_size", 14000)),
                         workers=1, delay=float(cfg.get("delay", 0.0)))
    ctx.unit_started(0, suffix)
    try:
        content = translate_office(source, suffix, pipeline)
    except Exception as exc:
        ctx.unit_done(0, failed=True, error=str(exc), title=suffix)
        raise RuntimeError(f"ترجمهٔ سند آفیس انجام نشد: {exc}") from exc
    out_dir = ctx.directory / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_lang = ctx.job.get("target_lang") or "fa"
    out = out_dir / f"{_title_stem(ctx, source)}_{target_lang}{suffix}"
    out.write_bytes(content)
    stats = pipeline.stats
    ctx.unit_done(0, title=suffix)
    ctx.update(total_units=1, completed_units=1, progress=1.0,
               input_tokens=stats.get("in_tokens", 0), output_tokens=stats.get("out_tokens", 0),
               api_requests=stats.get("api_requests", 0))
    ctx.finish(out)
    return True


# ------------------------------------------------------------------- structured
def _handle_structured(ctx, source: Path, suffix: str) -> bool:
    """JSON/CSV/نوت‌بوک/کد را با حفظ ساختار ترجمه می‌کند."""
    from trarium.structured import STRUCTURED_EXTENSIONS, translate_structured

    if suffix not in STRUCTURED_EXTENSIONS:
        return False
    cfg = ctx.config
    ctx.set_stage("ترجمه ساختاریافته")
    ctx.ensure_units([f"ساختار {suffix}"])
    pipeline = _pipeline(ctx, chunk_size=int(cfg.get("chunk_size", 14000)),
                         workers=1, delay=float(cfg.get("delay", 0.0)))
    ctx.unit_started(0, suffix)
    try:
        content = translate_structured(source, suffix, pipeline)
    except Exception as exc:
        ctx.unit_done(0, failed=True, error=str(exc), title=suffix)
        raise RuntimeError(f"ترجمهٔ ساختاریافته انجام نشد: {exc}") from exc
    if content is None:
        return False
    out_dir = ctx.directory / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_lang = ctx.job.get("target_lang") or "fa"
    out = out_dir / f"{_title_stem(ctx, source)}_{target_lang}{suffix}"
    out.write_text(content, encoding="utf-8")
    stats = pipeline.stats
    ctx.unit_done(0, title=suffix)
    ctx.update(total_units=1, completed_units=1, progress=1.0,
               input_tokens=stats.get("in_tokens", 0), output_tokens=stats.get("out_tokens", 0),
               api_requests=stats.get("api_requests", 0))
    ctx.finish(out)
    return True


# ----------------------------------------------------------------------- subtitle
def _handle_subtitle(ctx, source: Path, suffix: str) -> bool:
    """زیرنویس را با همان زمان‌بندی برمی‌گرداند؛ اگر نشد False می‌دهد."""
    from trarium.subtitles import read_subtitles

    document = read_subtitles(source)
    if document is None or not document.cues:
        return False

    cfg = ctx.config
    ctx.set_stage("ترجمه زیرنویس")
    store_file = ctx.directory / "subtitle_texts.json"
    try:
        texts = json.loads(store_file.read_text(encoding="utf-8")) if store_file.exists() else []
    except ValueError:
        texts = []
    if len(texts) != len(document.cues):
        texts = [cue.text for cue in document.cues]

    batches: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, cue in enumerate(document.cues):
        if not cue.text.strip():
            continue
        if current and size + len(cue.text) + 2 > 3000:
            batches.append(current)
            current, size = [], 0
        current.append(index)
        size += len(cue.text) + 2
    if current:
        batches.append(current)
    if not batches:
        return False

    ctx.ensure_units([f"زیرنویس بخش {i + 1}" for i in range(len(batches))])
    pipeline = _pipeline(ctx, chunk_size=int(cfg.get("chunk_size", 14000)),
                         workers=1, delay=float(cfg.get("delay", 0.0)))
    failed = 0
    for index, batch in enumerate(batches):
        if ctx.is_done(index):
            continue
        ctx.unit_started(index, f"زیرنویس بخش {index + 1}")
        joined = "\n\n".join(document.cues[i].text.strip() for i in batch)
        try:
            output = pipeline.translate_block(joined)
            parts = [part.strip() for part in re.split(r"\n\s*\n", output.strip()) if part.strip()]
            if len(parts) == len(batch):
                for cue_index, part in zip(batch, parts):
                    texts[cue_index] = part
            else:
                # اگر مدل مرزها را نگه نداشت، cue‌به‌cue ترجمه می‌شود تا زمان‌بندی به هم نریزد.
                for cue_index in batch:
                    texts[cue_index] = pipeline.translate_block(document.cues[cue_index].text.strip())
            ctx.unit_done(index, title=f"زیرنویس بخش {index + 1}")
        except Exception as exc:
            failed += 1
            ctx.unit_done(index, failed=True, error=str(exc), title=f"زیرنویس بخش {index + 1}")
        store_file.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
        ctx.checkpoint()

    out_dir = ctx.directory / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_lang = ctx.job.get("target_lang") or "fa"
    out = out_dir / f"{_title_stem(ctx, source)}_{target_lang}{suffix}"
    out.write_text(document.render(texts), encoding="utf-8")
    stats = pipeline.stats
    ctx.update(total_units=len(batches), completed_units=len(batches) - failed, failed_units=failed,
               progress=1.0, input_tokens=stats.get("in_tokens", 0),
               output_tokens=stats.get("out_tokens", 0), api_requests=stats.get("api_requests", 0))
    ctx.finish(out)
    return True


# ------------------------------------------------------------------------ youtube
def handle_youtube(ctx) -> None:
    """زیرنویس اصلی یوتیوب را می‌گیرد و با مسیر ترجمهٔ زیرنویس ترجمه می‌کند."""
    from trarium.youtube import fetch_subtitles

    cfg = ctx.config
    ctx.set_stage("دریافت زیرنویس یوتیوب")
    cookies = cfg.get("cookies_path") or None
    try:
        source, meta = fetch_subtitles(str(cfg.get("url") or ""), ctx.directory / "youtube",
                                       cfg.get("languages") or None, cookies=cookies)
    finally:
        # کوکی یوتیوب اطلاعات ورود کاربر است؛ بعد از دریافت نگهش نمی‌داریم.
        if cookies:
            Path(cookies).unlink(missing_ok=True)
    kind = "خودکار" if meta.get("generated") else "دستی"
    ctx.log(f"زیرنویس {kind} ({meta.get('language') or '?'}) با {meta.get('method')} دریافت شد: {meta.get('file')}")
    ctx.update(source_path=str(source))
    if not _handle_subtitle(ctx, source, source.suffix.lower()):
        raise RuntimeError("زیرنویس دریافت‌شده متنی برای ترجمه نداشت.")


# --------------------------------------------------------------------------- book
def handle_book(ctx) -> None:
    cfg = ctx.config
    source = Path(ctx.job.get("source_path") or "")
    if not source.exists():
        raise FileNotFoundError("فایل ورودی تسک پیدا نشد.")
    suffix = source.suffix.lower()
    if suffix in SUBTITLE_EXTENSIONS and _handle_subtitle(ctx, source, suffix):
        return
    if _handle_office(ctx, source, suffix):
        return
    if _handle_structured(ctx, source, suffix):
        return
    ledger, track = _ledger(ctx, source)
    ctx.set_stage("بررسی کیفیت صفحات")
    if track:
        ledger.start(track, "repair")
    try:
        from trarium.repair import analyze_book
        quality = analyze_book(source)
        for issue in quality.issues:
            if issue.severity != "info":
                ctx.log(f"کیفیت صفحه {issue.page or '-'}: {issue.detail}", "warning")
        if track:
            ledger.complete(track, "repair", quality.summary_fa())
    except Exception as exc:
        if track:
            ledger.fail(track, "repair", str(exc))
    ctx.set_stage("استخراج متن")
    if track:
        ledger.start(track, "ingest")
    is_pdf = suffix == ".pdf" or sniff_container(source) == "pdf"
    book = get_extractor(str(source), use_ocr=is_pdf).extract(str(source))
    if not book.chapters:
        raise UnsupportedFormatError("هیچ متن قابل ترجمه‌ای در فایل پیدا نشد.")
    if track:
        ledger.complete(track, "ingest", f"{len(book.chapters)} بخش")
    chunk_size = int(cfg.get("chunk_size", 14000))
    chunker = TextChunker(max_chars=max(2000, chunk_size), overlap=0)
    chunks = chunker.chunk_by_chapters(book.chapters)
    if not chunks:
        raise UnsupportedFormatError("متنی برای ترجمه پیدا نشد.")
    ctx.ensure_units([str(c.get("title") or f"بخش {i + 1}") for i, c in enumerate(chunks)])
    if ctx.completed_indexes():
        ctx.log("بخش‌های تکمیل‌شده از Checkpoint خوانده می‌شوند و دوباره به API نمی‌روند.")
    ctx.set_stage("ترجمه")
    if track:
        ledger.start(track, "translate")

    def on_unit(idx: int, unit: dict) -> None:
        total = len(chunks)
        failed = bool(unit.get("failed"))
        ctx.unit_done(idx, failed=failed, error=str(unit.get("error") or ""),
                      title=str(unit.get("title") or ""), total=total)
        if track:
            ledger.mark_unit(track, "translate", str(idx), "failed" if failed else "completed",
                             str(unit.get("error") or ""))

    pipeline = _pipeline(ctx, chunk_size=chunk_size, workers=int(cfg.get("workers", 3)),
                         delay=float(cfg.get("delay", 0.0)), on_unit_done=on_unit)
    ctx.checkpoint()
    result = pipeline.run(book)
    if result.translated_chunks == 0:
        detail = getattr(result, "error_message", "") or ""
        events = ctx.store.events(ctx.job_id, 1)
        if not detail and events:
            detail = events[-1]["message"]
        raise RuntimeError("هیچ بخشی ترجمه نشد؛ خطا: " + (detail or "خطای نامشخص"))
    if track:
        ledger.complete(track, "translate",
                        f"{result.translated_chunks}/{len(chunks)} بخش",
                        progress=len(chunks) and result.translated_chunks / len(chunks))
        ledger.complete(track, "ocr", "برای این فایل OCR لازم نبود")
    ctx.set_stage("ساخت خروجی")
    if track:
        ledger.start(track, "output")
    path = _save_book_result(ctx, result, str(cfg.get("output_format") or "txt"),
                             book.title or ctx.job["title"])
    ctx.update(total_units=len(chunks), completed_units=result.translated_chunks,
               failed_units=result.failed_chunks, input_tokens=result.estimated_input_tokens,
               output_tokens=result.estimated_output_tokens, api_requests=result.api_requests)
    if track:
        ledger.complete(track, "output", path.name)
        ledger.set_status(track, "completed")
    ctx.finish(path)


# ---------------------------------------------------------------------------- pdf
def _classic_ocr_pages(ctx, source: Path, skip_pages, cfg) -> tuple[dict, set]:
    """OCR.space for pages that have no text layer (only when its key exists)."""
    import pymupdf

    key = str(cfg.get("ocr_space_key") or os.environ.get("OCR_SPACE_KEY") or "")
    if not key or cfg.get("ocr_space") is False:
        return {}, set()
    pages: set[int] = set()
    with pymupdf.open(str(source)) as doc:
        for i, page in enumerate(doc):
            if i in (skip_pages or set()):
                continue
            if len(page.get_text().strip()) < 20 and page.get_images():
                pages.add(i)
        if not pages:
            return {}, set()
        from trarium.ocr_space import ocr_page

        result = {}
        for i in sorted(pages):
            ctx.checkpoint()
            ctx.set_stage(f"OCR صفحهٔ {i + 1}")
            try:
                result[i] = ocr_page(doc[i], language=str(cfg.get("ocr_lang") or "eng"), dpi=200, key=key)
            except Exception as exc:
                ctx.log(f"OCR صفحهٔ {i + 1} ناموفق: {exc}", "warning")
    return result, set(result)


def handle_pdf(ctx) -> None:
    import pymupdf

    from trarium.pdf_optimized import translate_pdf_optimized

    cfg = ctx.config
    source = Path(ctx.job.get("source_path") or "")
    if not source.exists():
        raise FileNotFoundError("فایل PDF تسک پیدا نشد.")
    ledger, track = _ledger(ctx, source)
    try:
        from trarium.repair import analyze_book
        ctx.set_stage("بررسی کیفیت صفحات")
        if track:
            ledger.start(track, "repair")
        quality = analyze_book(source)
        for issue in quality.issues:
            if issue.severity != "info":
                ctx.log(f"کیفیت صفحه {issue.page or '-'}: {issue.detail}", "warning")
        if track:
            ledger.complete(track, "repair", quality.summary_fa())
    except Exception as exc:
        if track:
            ledger.fail(track, "repair", str(exc))
    ctx.set_stage("تحلیل صفحه‌ها")
    if track:
        ledger.complete(track, "ingest", f"{Path(source).name}")
        ledger.start(track, "ocr")
    doc = pymupdf.open(str(source))
    total_pages = len(doc)
    doc.close()
    ctx.ensure_units([f"صفحه {i + 1}" for i in range(total_pages)])
    target_lang = ctx.job.get("target_lang") or "fa"
    out = ctx.directory / "output" / f"{_title_stem(ctx, source)}_{target_lang}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    page_range = cfg.get("pages") or ""
    last_completed = -1

    def progress(value: float, message: str) -> None:
        nonlocal last_completed
        ctx.checkpoint()
        value = max(0.0, min(1.0, float(value)))
        completed = int(value * total_pages)
        if completed > last_completed:
            # Never insert a unit past the last real page (that is what made a
            # two-page job report "3 of 2").
            for idx in range(last_completed + 1, min(completed, total_pages)):
                ctx.store.set_unit(ctx.job_id, idx, "completed")
            last_completed = min(completed, total_pages)
        stage = "OCR و تحلیل" if value < 0.40 else ("ترجمه" if value < 0.80 else "ساخت PDF")
        ctx.update(progress=value, stage=stage, current_unit=message[:300],
                   completed_units=max(0, min(total_pages, completed)))

    skip_pages = _parse_page_selection(page_range)
    # Same settings as the classic QA pipeline that gave the clean result: text
    # layer first, OCR.space only for pages without one, cover with the sampled
    # paper colour (never white boxes), skip text printed on artwork, measure
    # with the real font, keep cover/plate pages untouched, review and QC.
    precomputed, force_ocr = _classic_ocr_pages(ctx, source, skip_pages, cfg)
    _, stats = translate_pdf_optimized(
        str(source), ctx.make_translator(), target_lang, str(out),
        skip_pages=skip_pages,
        translation_workers=int(cfg.get("workers", 3)),
        translation_batch_chars=int(cfg.get("chunk_size", 14000)),
        progress_callback=progress, return_stats=True,
        enable_ocr=bool(cfg.get("enable_ocr", False)) and not precomputed,
        ocr_method=str(cfg.get("ocr_method") or "auto"),
        ocr_lang=str(cfg.get("ocr_lang") or "eng"),
        api_key=str(cfg.get("ocr_api_key") or "") or None,
        vision_model=str(cfg.get("ocr_vision_model") or "") or None,
        safe_optimization=bool(cfg.get("safe_optimization", True)),
        protect_nontranslatable=bool(cfg.get("protect_spans", True)),
        adaptive_batch=bool(cfg.get("adaptive_batch", True)),
        use_checkpoint=bool(cfg.get("use_checkpoint", True)),
        use_cache=bool(cfg.get("use_cache", True)),
        self_review=bool(cfg.get("self_review", True)),
        final_check=bool(cfg.get("final_check", True)),
        uniform_font=True, skip_text_on_image=True, match_background=True,
        precomputed_ocr=precomputed or None, force_ocr_pages=force_ocr or None,
        style=str(cfg.get("style") or "") or None,
        translation_qc=True, qc_repair=bool(cfg.get("qc_repair", True)),
        cover_detection=bool(cfg.get("cover_detection", True)),
        vision_api_key=os.environ.get("GEMINI_API_KEY") or None,
        short_prompt=str(cfg.get("short_prompt") or ""),
    )
    if stats.translated_blocks == 0 and stats.total_blocks > 0:
        reason = stats.first_translation_error or stats.first_write_error or "خطای نامشخص"
        raise RuntimeError("هیچ بلوکی ترجمه نشد؛ PDF ترجمه‌نشده ذخیره نشد. علت: " + str(reason))
    for idx in range(total_pages):
        ctx.store.set_unit(ctx.job_id, idx, "completed")
    if track:
        if bool(cfg.get("enable_ocr", False)):
            ledger.complete(track, "ocr", "OCR صفحات تصویری انجام شد")
        else:
            ledger.complete(track, "ocr", "OCR خاموش بود؛ لایهٔ متن موجود استفاده شد")
        ledger.complete(track, "translate", f"{total_pages} صفحه", progress=1.0)
        ledger.complete(track, "output", Path(out).name)
        ledger.set_status(track, "completed")
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
    rel_names: dict[Path, Path] = {}
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
                    continue
                rel = Path(name.replace("\\", "/"))
                if rel.is_absolute() or ".." in rel.parts:
                    continue
                # Keep the folder structure: two "01.png" in different folders
                # must not overwrite each other.
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
                images.append(target)
                rel_names[target] = rel
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
            relative = rel_names.get(image) or Path(image.name)
            destination = enhanced_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            report = enhance_file(image, destination, settings)
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
        for file in sorted(enhanced_dir.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(enhanced_dir).as_posix())
        archive.writestr("report.json", json.dumps(reports, ensure_ascii=False, indent=2))
    ctx.finish(zip_path)
