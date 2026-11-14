"""Full end-to-end Trarium run on the real Chinese PDF (image-only pages).

Steps, all with the real pipeline and a real OpenRouter key:

1. OCR the three scanned Chinese pages with a vision model.
2. Translate every block zh -> fa with a text model.
3. Write the translated PDF with Trarium's own writer.
4. Render input/output pages to PNG and compute measured metrics.

Usage (key comes from OPENROUTER_API_KEY, never from the repo):
    python qa/run_chinese_test.py --input "全球...pdf" --outdir qa/_cn
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import unicodedata
from collections import Counter

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from trarium.pdf_optimized import (  # noqa: E402
    _vision_prompt,
    _vision_image_data_url,
    _vision_image_size,
    _bbox_to_blocks,
    extract_ocr_blocks_batch_openrouter,
    translate_pdf_optimized,
)
from trarium.translators.openrouter import OpenRouterTranslator  # noqa: E402

KEY = os.environ.get("OPENROUTER_API_KEY", "")
VISION_MODEL = os.environ.get("TRARIUM_VISION_MODEL", "nvidia/nemotron-3.5-super-vl:free")
TEXT_MODEL = os.environ.get("TRARIUM_TEXT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")


def log(*args):
    print(*args, flush=True)


def ocr(doc, outdir: pathlib.Path) -> dict[int, list[dict]]:
    """OCR every page, one cached file per page so a re-run resumes."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from ocr_pages import ocr_page

    blocks: dict[int, list[dict]] = {}
    started = time.time()
    for page_no in range(1, doc.page_count + 1):
        cache = outdir / f"ocr_page{page_no}.json"
        page_blocks, how = ocr_page(doc[page_no - 1], KEY, cache)
        blocks[page_no] = page_blocks
        log(f"OCR p{page_no}: {len(page_blocks)} blocks, {sum(len(b['text']) for b in page_blocks)} chars "
            f"via {how}")
    log(f"OCR total: {round(time.time() - started, 1)}s, {sum(len(v) for v in blocks.values())} blocks")
    (outdir / "ocr_blocks.json").write_text(
        json.dumps({str(k): v for k, v in blocks.items()}, ensure_ascii=False, indent=1), encoding="utf-8")
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", default="qa/_cn")
    args = ap.parse_args()

    outdir = pathlib.Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    source = pathlib.Path(args.input).resolve()
    doc = pymupdf.open(source)
    log(f"input: {source.name} | pages={doc.page_count} | {source.stat().st_size / 1048576:.2f} MB")

    blocks = ocr(doc, outdir)
    for page_no in sorted(blocks):
        log(f"  page {page_no}: {len(blocks[page_no])} blocks")

    # The OCR cache is keyed 1-based for humans; the pipeline indexes pages from 0.
    precomputed = {page_no - 1: blocks[page_no] for page_no in blocks}
    translator = OpenRouterTranslator(api_key=KEY, model_name=TEXT_MODEL)
    target = outdir / "trarium_zh_to_fa.pdf"
    started = time.time()
    _, stats = translate_pdf_optimized(
        str(source), translator, "fa", str(target), 1,
        api_key=KEY, vision_model=VISION_MODEL, enable_ocr=True, ocr_method="openrouter",
        skip_pages=set(), translation_workers=2, precomputed_ocr=precomputed, return_stats=True,
    )
    elapsed = time.time() - started
    usage = getattr(translator, "last_usage", {}) or {}
    log(f"translation: {round(elapsed, 1)}s | blocks translated={stats.translated_blocks} "
        f"failed={getattr(stats, 'failed_blocks', 0)} | usage={usage}")

    metrics = measure(source, target, blocks, elapsed, stats, usage)
    metrics["text_pdf"] = build_text_pdf(blocks, translator, outdir)

    (outdir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    log("PDF_QA_METRICS:" + json.dumps(metrics, ensure_ascii=False))
    return 0


def build_text_pdf(blocks, translator, outdir: pathlib.Path) -> dict:
    """Re-typeset the same real translation as a clean Persian PDF."""
    from trarium.extractors.base import ExtractedBook
    from trarium.output import save_as_pdf
    from trarium.processors.pipeline import TranslationPipeline

    chapters = []
    for page_no in sorted(blocks):
        text = "\n".join(b["text"] for b in blocks[page_no] if b["text"].strip())
        if text.strip():
            chapters.append({"title": f"صفحه {page_no}", "content": text})
    book = ExtractedBook("بررسی بهترین انیمه‌های جهان", "منبع چینی", chapters, "zh", 0)
    started = time.time()
    result = TranslationPipeline(translator, "fa", chunk_size=4000, max_workers=2).run(book)
    result.title = "بررسی بهترین انیمه‌های جهان"
    result.author = "ترجمهٔ خودکار از چینی"
    path = outdir / "trarium_zh_to_fa_text.pdf"
    save_as_pdf(result, str(path))
    elapsed = round(time.time() - started, 1)
    doc = pymupdf.open(path)
    body = unicodedata.normalize("NFKC", "".join(p.get_text() for p in doc))
    spans = [s for p in doc for b in p.get_text("dict")["blocks"]
             for line in b.get("lines", []) for s in line.get("spans", []) if s["text"].strip()]
    right = [s for s in spans if s["bbox"][2] > doc[0].rect.width * 0.8]
    pages = doc.page_count
    doc.close()
    log(f"text PDF: {pages} pages, {round(path.stat().st_size / 1024)} KB, {elapsed}s")
    return {
        "pages": pages,
        "seconds": elapsed,
        "output_kb": round(path.stat().st_size / 1024, 1),
        "persian_chars": len([c for c in body if "\u0600" <= c <= "\u06ff"]),
        "chinese_chars_left": len([c for c in body if "\u4e00" <= c <= "\u9fff"]),
        "question_marks": body.count("?"),
        "right_aligned_pct": round(len(right) / max(1, len(spans)) * 100, 1),
        "failed_chunks": result.failed_chunks,
        "chunks": result.total_chunks,
        "file": str(path.relative_to(outdir)),
    }


def measure(source, target, blocks, elapsed, stats, usage) -> dict:
    src = pymupdf.open(source)
    out = pymupdf.open(target)
    src_pages = [[b["text"] for b in blocks[i]] for i in range(1, src.page_count + 1)]
    out_text = "".join(page.get_text() for page in out)
    # Persian letters are stored as presentation forms in the PDF, so normalise
    # before counting or the numbers come out meaningless.
    normalised = unicodedata.normalize("NFKC", out_text)
    persian = len([c for c in normalised if "\u0600" <= c <= "\u06FF"])
    chinese_left = len([c for c in out_text if "\u4e00" <= c <= "\u9fff"])
    fonts = sorted({f[3] for p in out for f in p.get_fonts()})
    spans = [s for p in out for b in p.get_text("dict")["blocks"]
             for line in b.get("lines", []) for s in line.get("spans", []) if s["text"].strip()]
    right = [s for s in spans if s["bbox"][2] > out[0].rect.width * 0.8]
    images = {"before": sum(len(p.get_images(full=True)) for p in src),
              "after": sum(len(p.get_images(full=True)) for p in out)}
    # render pages for the report
    pages_dir = pathlib.Path(target).parent / "pages"
    pages_dir.mkdir(exist_ok=True)
    for prefix, document in (("zh_input", src), ("zh_output", out)):
        for i, page in enumerate(document, 1):
            page.get_pixmap(dpi=100).save(pages_dir / f"{prefix}-p{i}.png")

    return {
        "source_pages": src.page_count,
        "output_pages": out.page_count,
        "ocr_blocks": sum(len(v) for v in blocks.values()),
        "ocr_chars_zh": sum(len(t) for page in src_pages for t in page),
        "translated_blocks": stats.translated_blocks,
        "failed_blocks": getattr(stats, "failed_blocks", 0),
        "persian_chars_output": persian,
        "chinese_chars_left": chinese_left,
        "fonts": fonts,
        "sahel_embedded": any("Sahel" in f for f in fonts),
        "question_marks": out_text.count("?"),
        "spans": len(spans),
        "right_aligned_pct": round(len(right) / max(1, len(spans)) * 100, 1),
        "images_before": images["before"],
        "images_after": images["after"],
        "ocr_seconds": None,
        "translate_seconds": round(elapsed, 1),
        "usage": usage,
        "output_kb": round(pathlib.Path(target).stat().st_size / 1024, 1),
        "text_sample": out_text[:400],
    }


if __name__ == "__main__":
    raise SystemExit(main())
