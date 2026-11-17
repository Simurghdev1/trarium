"""ترجمهٔ کتاب اسکن‌شدهٔ Undine با تراریوم + تست زنجیرهٔ جایگزینی مدل‌ها.

کتاب لایهٔ متنی دارد (چاپ تمیز با پس‌زمینهٔ کاغذ اسکن‌شده)، پس OCR لازم نیست؛
فقط صفحه‌های تصویری دست‌نخورده می‌مانند.

    GEMINI_API_KEY=... python qa/run_undine_test.py \
        --input undine.pdf --outdir qa/outputs/undine-test --pages 24-29
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import unicodedata

import pymupdf

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trarium.page_detector import parse_page_status  # noqa: E402
from trarium.pdf_optimized import translate_pdf_optimized  # noqa: E402
from trarium.translators.fallback import FallbackTranslator  # noqa: E402
from trarium.translators.gemini import GeminiTranslator  # noqa: E402
from trarium.translators.openrouter import OpenRouterTranslator  # noqa: E402

# Ordered by measured speed and free quota: a light model first, a heavy one as
# backup. A model that answers 429 is dropped and the next one takes over.
GEMINI_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
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


def build_translator(providers: list[str]) -> FallbackTranslator:
    candidates, labels = [], []
    if "gemini" in providers and os.environ.get("GEMINI_API_KEY"):
        for model in GEMINI_MODELS:
            candidates.append(GeminiTranslator(api_key=os.environ["GEMINI_API_KEY"], model_name=model))
            labels.append(f"gemini/{model}")
    if "openrouter" in providers and os.environ.get("OPENROUTER_API_KEY"):
        for model in OPENROUTER_MODELS:
            candidates.append(OpenRouterTranslator(api_key=os.environ["OPENROUTER_API_KEY"], model_name=model))
            labels.append(f"openrouter/{model}")
    if not candidates:
        raise SystemExit("هیچ کلید API‌ای تنظیم نشده است.")
    return FallbackTranslator(candidates, labels)


def parse_pages(spec: str | None, total: int) -> set[int]:
    if not spec:
        return set()
    keep: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            keep.update(range(start - 1, end))
        else:
            keep.add(int(part) - 1)
    return {i for i in range(total) if i not in keep}


def screenshots(path: pathlib.Path, shots: pathlib.Path, prefix: str, pages: list[int], dpi: int = 90) -> None:
    with pymupdf.open(path) as doc:
        for number in pages:
            if 0 <= number < doc.page_count:
                doc[number].get_pixmap(dpi=dpi).save(shots / f"{prefix}-p{number + 1}.png")


def color_noise(path: pathlib.Path, page: int, boxes: list[tuple]) -> float:
    """How far the pixels inside each translated box drift from the paper colour."""
    with pymupdf.open(path) as doc:
        pix_page = doc[page]
        drift = []
        for box in boxes:
            rect = pymupdf.Rect(box)
            if rect.width < 3 or rect.height < 3:
                continue
            pix = pix_page.get_pixmap(clip=rect, dpi=40)
            n = pix.n
            total = pix.width * pix.height
            step = max(1, total // 400)
            values = []
            for i in range(0, total, step):
                offset = i * n
                values.append((pix.samples[offset], pix.samples[offset + 1], pix.samples[offset + 2]))
            if not values:
                continue
            base = sorted(v[0] + v[1] + v[2] for v in values)[len(values) // 2] / 3
            whiteish = sum(1 for v in values if min(v) > 235) / len(values)
            drift.append((base, whiteish))
        if not drift:
            return 0.0
        return round(sum(d[1] for d in drift) / len(drift), 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--pages", default=None, help="صفحه‌های موردنظر، مثل 24-29")
    parser.add_argument("--providers", default="gemini,openrouter")
    parser.add_argument("--ocr-pages", default="", help="صفحه‌هایی که لایهٔ متنی‌شان آشغال است (مثل 12,17,21)")
    parser.add_argument("--style", default="neutral", help="سبک ترجمه: neutral, literary, plain, manga, lightnovel, ...")
    parser.add_argument("--qc-repair", action="store_true", help="بلاک‌های ناقص را یک بار دیگر ترجمه کن")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--batch-chars", type=int, default=7000)
    parser.add_argument("--no-cover-detection", action="store_true",
                        help="تشخیص خودکار جلد/عنوان/تصویر را خاموش کن")
    parser.add_argument("--cover-threshold", type=float, default=0.72,
                        help="آستانهٔ اطمینان برای خارج کردن صفحه از ترجمه (۰ تا ۱)")
    parser.add_argument("--page-status", default="",
                        help="وضعیت دستی صفحه‌ها، مثل 1=MANGA_COVER,4=NORMAL_PAGE")
    args = parser.parse_args()

    source = pathlib.Path(args.input)
    outdir = pathlib.Path(args.outdir)
    (outdir / "screenshots").mkdir(parents=True, exist_ok=True)
    target = outdir / "02_translated_fa.pdf"

    translator = build_translator(args.providers.split(","))
    with pymupdf.open(source) as doc:
        total = doc.page_count
    skip = parse_pages(args.pages, total)

    ocr_pages = {int(x) - 1 for x in args.ocr_pages.split(",") if x.strip()}

    # Look at every page before a single character is OCR'd: a cover, a title
    # page or a plate is never sent to OCR and never touched afterwards.
    cover_decisions = []
    if not args.no_cover_detection:
        from trarium.page_detector import CoverDetectionConfig, detect_pages, summary as page_summary

        with pymupdf.open(source) as doc:
            cover_decisions = detect_pages(
                doc,
                CoverDetectionConfig(
                    threshold=args.cover_threshold,
                    manual=parse_page_status(args.page_status),
                    vision=bool(os.environ.get("GEMINI_API_KEY")),
                    vision_api_key=os.environ.get("GEMINI_API_KEY"),
                ),
            )
        print("تشخیص صفحه‌ها: " + json.dumps(page_summary(cover_decisions), ensure_ascii=False), flush=True)
        # Same rule as the pipeline: an unconfirmed picture-only page still gets
        # OCR, because its dialogue may live inside the image.
        for decision in cover_decisions:
            if (
                decision.exempt
                and ocr_pages
                and decision.source != "vision"
                and int(decision.features.get("words") or 0) == 0
                and not decision.features.get("has_text_layer")
            ):
                decision.status = "NORMAL_PAGE"
        ocr_pages = {page for page in ocr_pages if not cover_decisions[page].exempt}

    precomputed = {}
    if ocr_pages:
        from trarium.ocr_space import ocr_page

        with pymupdf.open(source) as doc:
            for number in sorted(ocr_pages):
                print(f"OCR صفحهٔ {number + 1}...", flush=True)
                precomputed[number] = ocr_page(doc[number], language="eng", dpi=200,
                                               key=os.environ.get("OCR_SPACE_KEY"))

    started = time.time()
    path, stats = translate_pdf_optimized(
        str(source), translator, target_lang="fa", output_path=str(target),
        skip_pages=skip, enable_ocr=False, return_stats=True,
        translation_workers=args.workers, translation_batch_chars=args.batch_chars,
        self_review=True, final_check=True, uniform_font=True,
        skip_text_on_image=True, match_background=True,
        precomputed_ocr=precomputed, force_ocr_pages=ocr_pages,
        style=args.style, translation_qc=True, qc_repair=args.qc_repair,
        cover_detection=not args.no_cover_detection,
        cover_threshold=args.cover_threshold,
        manual_page_status=parse_page_status(args.page_status),
        vision_api_key=os.environ.get("GEMINI_API_KEY"),
        cover_decisions=cover_decisions or None,
        progress_callback=lambda p, m: print(f"{p * 100:.0f}% {m}", flush=True),
    )
    seconds = time.time() - started

    with pymupdf.open(path) as out:
        text = unicodedata.normalize("NFKC", "".join(p.get_text() for p in out))
        pages = [i for i in range(out.page_count) if i not in skip]
    metrics = {
        "pages_total": total,
        "pages_translated": stats.page_status_counts.get("translated_pages", len(pages)),
        "seconds": round(seconds, 1),
        "blocks": stats.total_blocks,
        "translated_blocks": stats.translated_blocks,
        "failed_blocks": stats.failed_blocks,
        "write_failures": stats.write_failures,
        "skipped_artwork": stats.skipped_artwork,
        "skipped_noise_pages": stats.skipped_noise_pages,
        "reviewed_blocks": stats.reviewed_blocks,
        "persian_chars": len([c for c in text if "\u0600" <= c <= "\u06ff"]),
        "latin_chars": len([c for c in text if c.isascii() and c.isalpha()]),
        "style": stats.style,
        "translation_issues": stats.translation_issues,
        "translation_issue_kinds": stats.translation_issue_kinds,
        "repaired_blocks": stats.repaired_blocks,
        "layout_moved": stats.layout_moved,
        "layout_shrunk": stats.layout_shrunk,
        "layout_overflow": stats.layout_overflow,
        "qc_ok": stats.qc_ok,
        "qc_issues": stats.qc_issues,
        "providers": translator.status(),
        "pages_written": stats.pages_written,
        "page_status": stats.page_status_counts,
        "detected_exempt_pages": stats.detected_exempt_pages,
    }
    (outdir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "page_status.json").write_text(
        json.dumps(stats.page_decisions, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    show: list[int] = []
    if args.pages:
        for part in args.pages.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = (int(x) for x in part.split("-", 1))
                show.extend(range(start - 1, min(end, start + 2)))
            else:
                show.append(int(part) - 1)
    else:
        show = [0, 1]
    show = show[:6]
    screenshots(source, outdir / "screenshots", "original", show)
    screenshots(path, outdir / "screenshots", "translated", show)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if stats.qc_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
