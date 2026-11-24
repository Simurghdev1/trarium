"""Translate one or more files for real (used by GitHub Actions).

Usage:
    python qa/translate_files.py --input book.pdf novel.epub --outdir out
    python qa/translate_files.py --list files.txt --outdir out

Every input gets its own folder inside ``--outdir`` holding the translated file,
a ``metrics.json`` with the real numbers and the log of the run. API keys are
read from the environment and are never printed.

PDFs keep their layout (the translation is written over the source text, the
artwork is left alone and the paper colour is sampled page by page). Every
other format goes through the normal pipeline and is written back in its own
format.

The script never raises on a single bad file: it records the failure in the
metrics and moves on, so one broken book cannot kill a whole batch.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import traceback
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trarium.failover import AUTH, NETWORK, PERMANENT, QUOTA, RATE_LIMIT, TRANSIENT, classify_error
from trarium.translators.base import BaseTranslator
from trarium.translators.fallback import FallbackTranslator  # noqa: E402
from trarium.translators.gemini import GeminiTranslator  # noqa: E402
from trarium.translators.openai_compatible import OpenAICompatibleTranslator  # noqa: E402
from trarium.translators.openrouter import OpenRouterTranslator  # noqa: E402

# Ordered by measured availability on the free tier: the two lite models that
# answer reliably come first. A model that answers 429 is dropped and the next
# one takes over; a model that is merely overloaded (503) is retried first.
GEMINI_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
]
OPENROUTER_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "deepseek/deepseek-chat-v3.1:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
]

PERSIAN = lambda ch: "\u0600" <= ch <= "\u06ff"  # noqa: E731


class ResilientTranslator(BaseTranslator):
    """Retry the whole chain when every model failed for a temporary reason.

    The fallback chain already moves on to the next model, but when *every*
    model is momentarily overloaded (Gemini answers 503 "high demand") the
    block is lost. Here the chain is simply tried again after a pause, so a
    short overload costs a few seconds instead of a missing chapter.
    """

    def __init__(self, inner: BaseTranslator, retries: int = 4, base_delay: float = 4.0):
        self.inner = inner
        self.retries = retries
        self.base_delay = base_delay
        self.transient_retries = 0

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    @property
    def provider(self) -> str:
        return self.inner.provider

    def get_token_limit(self) -> int:
        return self.inner.get_token_limit()

    def status(self) -> dict:
        return self.inner.status() if hasattr(self.inner, "status") else {}

    def translate(self, text: str, target_lang: str = "fa", instructions: str = "") -> str:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                if instructions:
                    try:
                        return self.inner.translate(text, target_lang, instructions)
                    except TypeError:
                        pass
                return self.inner.translate(text, target_lang)
            except Exception as exc:  # noqa: BLE001 - the whole point is to retry
                last = exc
                kind = classify_error(exc)
                if kind in (TRANSIENT, NETWORK):
                    delay, allowed = self.base_delay * (2 ** attempt), self.retries
                elif kind == RATE_LIMIT:
                    # A per-minute limit resets quickly; a daily limit does not,
                    # so this path gets a single short retry and no more.
                    delay, allowed = 10.0, 1
                else:
                    raise
                if attempt >= allowed:
                    raise
                self.transient_retries += 1
                print(
                    f"  ⚠️ موقتی ({kind}): {str(exc)[:110]} — {delay:.0f} ثانیه بعد دوباره",
                    flush=True,
                )
                time.sleep(delay)
        raise last  # pragma: no cover - the loop always returns or raises


def build_translator(providers: list[str]) -> BaseTranslator:
    candidates, labels = [], []
    if "gemini" in providers and os.environ.get("GEMINI_API_KEY"):
        for model in GEMINI_MODELS:
            candidates.append(GeminiTranslator(api_key=os.environ["GEMINI_API_KEY"], model_name=model))
            labels.append(f"gemini/{model}")
    if "openrouter" in providers and os.environ.get("OPENROUTER_API_KEY"):
        for model in OPENROUTER_MODELS:
            candidates.append(OpenRouterTranslator(api_key=os.environ["OPENROUTER_API_KEY"], model_name=model))
            labels.append(f"openrouter/{model}")
    if "openai_compatible" in providers and os.environ.get("TRARIUM_BASE_URL"):
        candidates.append(
            OpenAICompatibleTranslator(
                api_key=os.environ.get("TRARIUM_API_KEY") or "local",
                model_name=os.environ.get("TRARIUM_MODEL") or "local-model",
                base_url=os.environ["TRARIUM_BASE_URL"],
            )
        )
        labels.append(f"openai-compatible/{os.environ.get('TRARIUM_MODEL') or 'local-model'}")
    if not candidates:
        raise SystemExit(
            "No API key is configured (GEMINI_API_KEY / OPENROUTER_API_KEY / TRARIUM_BASE_URL); "
            "nothing to translate."
        )
    return ResilientTranslator(FallbackTranslator(candidates, labels))


def safe_name(value: str) -> str:
    """A short, filesystem-safe folder name for the output of one input."""
    stem = pathlib.Path(value).stem
    cleaned = re.sub(r"[^\w\-. ]+", " ", stem, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:60] or "input"


def parse_pages(spec: str | None) -> set[int]:
    """``"3,5,10-12"`` -> {2, 4, 9, 10, 11} (zero-based page indexes)."""
    pages: set[int] = set()
    if not spec:
        return pages
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            pages.update(range(start - 1, end))
        else:
            pages.add(int(part) - 1)
    return pages


def screenshot(pdf_path, out_dir: pathlib.Path, prefix: str, pages: list[int], dpi: int = 90) -> None:
    """Render a few pages side by side so the result can be judged by eye."""
    try:
        import pymupdf
    except ImportError:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with pymupdf.open(pdf_path) as doc:
            for number in pages:
                if not (0 <= number < doc.page_count):
                    continue
                pix = doc[number].get_pixmap(dpi=dpi)
                pix.save(str(out_dir / f"{prefix}-p{number + 1}.png"))
    except Exception as exc:  # screenshots are a nicety, never a blocker
        print(f"  (screenshot skipped: {exc})", flush=True)


def count_chars(text: str) -> dict[str, int]:
    return {
        "persian_chars": sum(1 for ch in text if PERSIAN(ch)),
        "latin_chars": sum(1 for ch in text if ch.isascii() and ch.isalpha()),
    }


def translate_pdf(source: pathlib.Path, outdir: pathlib.Path, translator, args) -> dict:
    """Layout-preserving PDF translation: the classic Trarium PDF path."""
    import pymupdf

    from trarium.pdf_optimized import translate_pdf_optimized

    target = outdir / f"02_translated_{args.target_lang}.pdf"
    skip = parse_pages(args.pages)

    started = time.time()
    path, stats = translate_pdf_optimized(
        str(source),
        translator,
        target_lang=args.target_lang,
        output_path=str(target),
        skip_pages=skip,
        enable_ocr=args.ocr,
        ocr_method=args.ocr_method,
        force_ocr_pages=parse_pages(args.ocr_pages),
        translation_workers=args.workers,
        translation_batch_chars=args.batch_chars,
        return_stats=True,
        self_review=True,
        final_check=True,
        uniform_font=True,
        skip_text_on_image=True,
        match_background=True,
        style=args.style,
        translation_qc=True,
        qc_repair=not args.no_qc_repair,
        cover_detection=not args.no_cover_detection,
        cover_threshold=args.cover_threshold,
        vision_api_key=os.environ.get("GEMINI_API_KEY"),
        progress_callback=lambda p, m: print(f"  {p * 100:.0f}% {m}", flush=True),
    )
    seconds = time.time() - started

    with pymupdf.open(path) as out:
        text = unicodedata.normalize("NFKC", "".join(p.get_text() for p in out))
        pages_total = out.page_count

    metrics = {
        "kind": "pdf",
        "source": str(source),
        "output": str(path),
        "target_lang": args.target_lang,
        "style": args.style,
        "seconds": round(seconds, 1),
        "pages_total": pages_total,
        "pages_written": stats.pages_written,
        "blocks": stats.total_blocks,
        "translated_blocks": stats.translated_blocks,
        "failed_blocks": stats.failed_blocks,
        "write_failures": stats.write_failures,
        "reviewed_blocks": stats.reviewed_blocks,
        "repaired_blocks": stats.repaired_blocks,
        "skipped_artwork": stats.skipped_artwork,
        "skipped_noise_pages": stats.skipped_noise_pages,
        "layout_moved": stats.layout_moved,
        "layout_shrunk": stats.layout_shrunk,
        "layout_overflow": stats.layout_overflow,
        "qc_ok": stats.qc_ok,
        "qc_issues": stats.qc_issues,
        "translation_issues": stats.translation_issues,
        "translation_issue_kinds": stats.translation_issue_kinds,
        "page_status": stats.page_status_counts,
        "detected_exempt_pages": stats.detected_exempt_pages,
        "providers": translator.status(),
        "transient_retries": getattr(translator, "transient_retries", 0),
        "bytes": pathlib.Path(path).stat().st_size if pathlib.Path(path).exists() else 0,
    }
    metrics.update(count_chars(text))

    show = [0, 1, 2]
    screenshot(source, outdir / "screenshots", "original", show)
    screenshot(path, outdir / "screenshots", "translated", show)
    return metrics


def translate_book(source: pathlib.Path, outdir: pathlib.Path, translator, args) -> dict:
    """EPUB / TXT / DOCX / HTML ... through the normal pipeline."""
    from trarium.extractors import get_extractor
    from trarium.output import save_as_epub, save_as_pdf, save_as_text
    from trarium.processors.pipeline import TranslationPipeline

    extractor = get_extractor(str(source), use_ocr=False)
    book = extractor.extract(str(source))
    if not book.total_chars:
        raise RuntimeError("no text could be extracted from this file")

    pipeline = TranslationPipeline(
        translator=translator,
        target_lang=args.target_lang,
        chunk_size=args.chunk_size,
        max_workers=args.workers,
    )
    started = time.time()
    result = pipeline.run(book)
    seconds = time.time() - started

    suffix = source.suffix.lower().lstrip(".")
    out_format = args.out_format or (suffix if suffix in {"epub", "pdf", "txt", "html"} else "txt")
    output = outdir / f"02_translated_{args.target_lang}.{out_format}"

    if out_format == "epub":
        from trarium.epub_cover import cover_from_source

        save_as_epub(result, str(output), cover=cover_from_source(str(source)),
                     compress=not args.no_epub_compress)
    elif out_format == "pdf":
        save_as_pdf(result, str(output))
    elif out_format == "html":
        from trarium.output import save_as_html

        save_as_html(result, str(output))
    else:
        save_as_text(result, str(output))

    text = result.translated_text or ""
    metrics = {
        "kind": out_format,
        "source": str(source),
        "output": str(output),
        "target_lang": args.target_lang,
        "title": result.title,
        "author": result.author,
        "seconds": round(seconds, 1),
        "chapters": len(book.chapters),
        "total_chars": result.total_chars,
        "translated_chunks": result.translated_chunks,
        "total_chunks": result.total_chunks,
        "failed_chunks": result.failed_chunks,
        "cache_hits": result.cache_hits,
        "checkpoint_hits": result.checkpoint_hits,
        "api_requests": result.api_requests,
        "estimated_input_tokens": result.estimated_input_tokens,
        "estimated_output_tokens": result.estimated_output_tokens,
        "errors": result.errors[:20],
        "providers": translator.status(),
        "transient_retries": getattr(translator, "transient_retries", 0),
        "bytes": output.stat().st_size if output.exists() else 0,
        "source_bytes": source.stat().st_size if source.exists() else 0,
    }
    if out_format == "epub":
        metrics["epub_compress"] = not args.no_epub_compress
        metrics["size_ratio_vs_source"] = (
            round(metrics["bytes"] / metrics["source_bytes"], 3) if metrics["source_bytes"] else None
        )
    metrics.update(count_chars(text))
    return metrics


def run_one(source: pathlib.Path, out_root: pathlib.Path, translator, args) -> dict:
    outdir = out_root / safe_name(str(source))
    outdir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        if source.suffix.lower() == ".pdf":
            metrics = translate_pdf(source, outdir, translator, args)
        else:
            metrics = translate_book(source, outdir, translator, args)
        metrics["ok"] = True
    except Exception as exc:
        metrics = {
            "ok": False,
            "kind": source.suffix.lower().lstrip("."),
            "source": str(source),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4000:],
        }
        print(f"  FAILED: {metrics['error']}", flush=True)
    metrics["wall_seconds"] = round(time.time() - started, 1)
    (outdir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", nargs="*", default=[], help="input files")
    parser.add_argument("--list", default="", help="a file with one input path per line")
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--target-lang", default="fa")
    parser.add_argument("--providers", default="gemini,openrouter")
    parser.add_argument("--style", default="neutral")
    parser.add_argument("--out-format", default="", help="txt|pdf|epub|html (auto for books)")
    parser.add_argument("--no-epub-compress", action="store_true",
                        help="write the EPUB without Deflate compression (default: compressed)")
    parser.add_argument("--pages", default=None, help="pages to skip, e.g. 1,5,10-12")
    parser.add_argument("--ocr", action="store_true", help="OCR scanned PDF pages")
    parser.add_argument("--ocr-method", default="ocrspace", help="auto|ocrspace|pdf24|openrouter")
    parser.add_argument("--ocr-pages", default="",
                        help="pages whose own text layer is noise and must be read by OCR, e.g. 12,17,21")
    parser.add_argument("--no-cover-detection", action="store_true")
    parser.add_argument("--no-qc-repair", action="store_true")
    parser.add_argument("--cover-threshold", type=float, default=0.72)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-chars", type=int, default=7000)
    parser.add_argument("--chunk-size", type=int, default=14000)
    parser.add_argument("--skip-existing", action="store_true", help="skip files that already have an output")
    args = parser.parse_args()

    inputs = [pathlib.Path(p) for p in args.input]
    if args.list:
        list_path = pathlib.Path(args.list)
        if list_path.exists():
            for line in list_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    inputs.append(pathlib.Path(line))
    inputs = [p for p in inputs if p.exists()]
    if not inputs:
        print("no input file was found; nothing to do.")
        return 2

    out_root = pathlib.Path(args.outdir)
    out_root.mkdir(parents=True, exist_ok=True)
    translator = build_translator(args.providers.split(","))

    summary = []
    for source in inputs:
        outdir = out_root / safe_name(str(source))
        if args.skip_existing and (outdir / "metrics.json").exists():
            print(f"skip (already done): {source}", flush=True)
            continue
        print(f"=== {source} -> {outdir}", flush=True)
        metrics = run_one(source, out_root, translator, args)
        summary.append(metrics)
        print(json.dumps({k: v for k, v in metrics.items() if k != "traceback"},
                         ensure_ascii=False, indent=1)[:2000], flush=True)

    ok = sum(1 for m in summary if m.get("ok"))
    (out_root / "summary.json").write_text(
        json.dumps({"total": len(summary), "ok": ok, "failed": len(summary) - ok,
                    "runs": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"DONE: {ok}/{len(summary)} file(s) translated.", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
