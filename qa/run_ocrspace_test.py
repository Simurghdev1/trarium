"""End-to-end test of the OCR.space provider on the real Chinese scan.

Everything here is real: the OCR.space API (free plan), the translation API and
the PDF that Trarium writes. Pages are sent one at a time because the free plan
caps a request at 1 MB and three pages.

    TRARIUM_OCR_SPACE_KEY=... python qa/run_ocrspace_test.py \
        --input "全球最佳动漫盘点_中文三页详细版 (1) (1).pdf" \
        --providers gemini,openrouter

Outputs (qa/outputs/ocrspace-test/):
    01_original.pdf            the uploaded scan, untouched
    02_ocrspace_ocr.pdf        page images + OCR.space text layer
    03_translated_gemini.pdf   translation with a free Gemini model
    04_translated_openrouter.pdf
    ocr_blocks.json            raw OCR.space blocks (cached, reused by a rerun)
    metrics.json               measured numbers per provider
    screenshots/               original / ocr / translated pages and comparisons
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import time
import unicodedata

import pymupdf

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trarium import ocr_space  # noqa: E402
from trarium.pdf_optimized import translate_pdf_optimized  # noqa: E402
from trarium.translators.gemini import GeminiTranslator  # noqa: E402
from trarium.translators.openrouter import OpenRouterTranslator  # noqa: E402


def load_blocks(source: pathlib.Path, cache: pathlib.Path, key: str | None):
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        blocks = {int(k): v for k, v in data["blocks"].items()}
        return blocks, data["meta"]
    blocks, meta = ocr_space.ocr_document_blocks(source, language="chi_sim+eng", dpi=200, key=key)
    cache.write_text(
        json.dumps({"blocks": {str(k): v for k, v in blocks.items()}, "meta": meta},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return blocks, meta


def write_ocr_pdf(source: pathlib.Path, blocks: dict, target: pathlib.Path) -> None:
    """Page image + invisible text layer at the OCR boxes (a normal OCR PDF)."""
    doc = pymupdf.open()
    with pymupdf.open(source) as src:
        for index, page in enumerate(src):
            new = doc.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=page.get_pixmap(dpi=85))
            for block in blocks.get(index, []):
                x0, y0, x1, y1 = block["bbox"]
                size = max(5, min(30, (y1 - y0) * 0.8))
                try:
                    new.insert_text((x0, y1 - 1), block["text"], fontsize=size,
                                    fontname="china-s", render_mode=3)
                except Exception:
                    continue
    doc.save(target)
    doc.close()


def measure(source: pathlib.Path, ocr_pdf: pathlib.Path, output: pathlib.Path,
            stats, seconds: float, label: str) -> dict:
    out = pymupdf.open(output)
    text = unicodedata.normalize("NFKC", "".join(p.get_text() for p in out))
    spans = [s for p in out for b in p.get_text("dict")["blocks"]
             for line in b.get("lines", []) for s in line.get("spans", []) if s["text"].strip()]
    right = [s for s in spans if s["bbox"][2] > out[0].rect.width * 0.8]
    shots = output.parent / "screenshots"
    shots.mkdir(exist_ok=True)
    for i, page in enumerate(out, 1):
        page.get_pixmap(dpi=95).save(shots / f"{label}-p{i}.png")
    metrics = {
        "provider": label,
        "pages_in": pymupdf.open(source).page_count,
        "pages_out": out.page_count,
        "ocr_chars": len(pymupdf.open(ocr_pdf)[0].get_text()),
        "blocks": stats.total_blocks,
        "translated_blocks": stats.translated_blocks,
        "failed_blocks": stats.failed_blocks,
        "write_failures": stats.write_failures,
        "persian_chars": len([c for c in text if "\u0600" <= c <= "\u06ff"]),
        "chinese_left": len([c for c in text if "\u4e00" <= c <= "\u9fff"]),
        "question_marks": text.count("?"),
        "right_aligned_pct": round(len(right) / max(1, len(spans)) * 100, 1),
        "seconds": round(seconds, 1),
        "kb": round(output.stat().st_size / 1024, 1),
    }
    out.close()
    return metrics


def build_translator(name: str, model: str | None):
    if name == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "")
        return GeminiTranslator(api_key=key, model_name=model or "gemini-3.5-flash")
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return OpenRouterTranslator(api_key=key, model_name=model or "nvidia/nemotron-3-ultra-550b-a55b:free")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", default="qa/outputs/ocrspace-test")
    ap.add_argument("--providers", default="gemini,openrouter",
                    help="comma separated provider[:model] specs")
    ap.add_argument("--gemini-model", default="gemini-3.5-flash")
    ap.add_argument("--openrouter-model", default="nvidia/nemotron-3-ultra-550b-a55b:free")
    args = ap.parse_args()

    outdir = pathlib.Path(args.outdir).resolve()
    (outdir / "screenshots").mkdir(parents=True, exist_ok=True)
    source = pathlib.Path(args.input).resolve()
    shutil.copy2(source, outdir / "01_original.pdf")

    key = os.environ.get("TRARIUM_OCR_SPACE_KEY") or None
    started = time.time()
    blocks, meta = load_blocks(source, outdir / "ocr_blocks.json", key)
    ocr_seconds = time.time() - started
    print(f"ocr.space: {meta['pages']} pages, {meta['requests']} requests, "
          f"{meta['chars']} chars, {ocr_seconds:.1f}s, language={meta['language']}", flush=True)

    ocr_pdf = outdir / "02_ocrspace_ocr.pdf"
    write_ocr_pdf(source, blocks, ocr_pdf)

    shots = outdir / "screenshots"
    for prefix, path in (("original", source), ("ocr", ocr_pdf)):
        with pymupdf.open(path) as doc:
            for i, page in enumerate(doc, 1):
                page.get_pixmap(dpi=95).save(shots / f"{prefix}-p{i}.png")

    results = []
    specs = [s.strip() for s in args.providers.split(",") if s.strip()]
    for index, spec in enumerate(specs, start=3):
        provider, _, explicit_model = spec.partition(":")
        model = explicit_model or (
            args.gemini_model if provider == "gemini" else args.openrouter_model
        )
        label = (provider + "-" + model.split("/")[-1].replace(":", "-"))[:32]
        target = outdir / f"0{index}_translated_{label}.pdf"
        print(f"--- {label} ({model}) ---", flush=True)
        t0 = time.time()
        try:
            translator = build_translator(provider, model)
            _, stats = translate_pdf_optimized(
                str(source), translator, "fa", str(target), 1,
                enable_ocr=True, ocr_method="ocrspace", ocr_lang="chi_sim+eng",
                skip_pages=set(), translation_workers=2, return_stats=True,
                use_cache=False, use_checkpoint=False,
            )
        except Exception as exc:  # noqa: BLE001 - a provider failure is data for the report
            print(f"FAILED {provider}: {exc}", flush=True)
            results.append({"provider": provider, "model": model, "label": label, "error": str(exc)[:400]})
            continue
        metrics = measure(source, ocr_pdf, target, stats, time.time() - t0, label)
        metrics["provider"] = provider
        metrics["model"] = model
        metrics["usage"] = getattr(translator, "last_usage", {})
        print("METRICS:" + json.dumps(metrics, ensure_ascii=False), flush=True)
        results.append(metrics)

    best = next((r for r in results if r.get("translated_blocks")), None)
    for n in (1, 2, 3):
        left = shots / f"original-p{n}.png"
        key = best.get("provider") or best.get("label") if best else None
        right_img = shots / f"{key}-p{n}.png" if key else shots / f"translated-p{n}.png"
        if left.exists() and right_img.exists():
            from PIL import Image

            a, b = Image.open(left), Image.open(right_img)
            scale = 700 / a.width
            a = a.resize((700, int(a.height * scale)))
            b = b.resize((700, int(b.height * scale)))
            canvas = Image.new("RGB", (a.width + b.width + 16, max(a.height, b.height)), (240, 243, 247))
            canvas.paste(a, (0, 0))
            canvas.paste(b, (a.width + 16, 0))
            canvas.save(shots / f"compare-p{n}.png", optimize=True)

    payload = {
        "ocr": {**meta, "seconds": round(ocr_seconds, 1), "engine": "ocr.space (free plan)"},
        "providers": results,
    }
    (outdir / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
