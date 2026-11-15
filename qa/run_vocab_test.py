"""End-to-end run of Trarium on the English vocabulary PDF (3 pages).

Pages 1–2 carry a real text layer (vocabulary list + short chapter), page 3 is an
image-only page, so OCR runs only there. Output keeps the original page size and
geometry; the translation is written into the original text boxes.

    GEMINI_API_KEY=... TRARIUM_OCR_SPACE_KEY=... \
        python qa/run_vocab_test.py --input /tmp/vocab.pdf
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

from trarium.pdf_optimized import translate_pdf_optimized  # noqa: E402
from trarium.translators.gemini import GeminiTranslator  # noqa: E402
from trarium.translators.openrouter import OpenRouterTranslator  # noqa: E402


def build_translator(provider: str, model: str | None):
    if provider == "gemini":
        return GeminiTranslator(
            api_key=os.environ.get("GEMINI_API_KEY", ""),
            model_name=model or "gemini-flash-lite-latest",
        )
    return OpenRouterTranslator(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        model_name=model or "nvidia/nemotron-3-ultra-550b-a55b:free",
    )


def screenshots(path: pathlib.Path, shots: pathlib.Path, prefix: str, dpi: int = 95) -> None:
    with pymupdf.open(path) as doc:
        for index, page in enumerate(doc, 1):
            page.get_pixmap(dpi=dpi).save(shots / f"{prefix}-p{index}.png")


def measure(source: pathlib.Path, output: pathlib.Path, stats, seconds: float) -> dict:
    with pymupdf.open(output) as doc:
        text = unicodedata.normalize("NFKC", "".join(page.get_text() for page in doc))
    return {
        "pages_in": pymupdf.open(source).page_count,
        "pages_out": pymupdf.open(output).page_count,
        "blocks": stats.total_blocks,
        "translated_blocks": stats.translated_blocks,
        "failed_blocks": stats.failed_blocks,
        "write_failures": stats.write_failures,
        "persian_chars": len([c for c in text if "\u0600" <= c <= "\u06ff"]),
        "latin_chars": len([c for c in text if c.isascii() and c.isalpha()]),
        "question_marks": text.count("?"),
        "seconds": round(seconds, 1),
        "kb": round(output.stat().st_size / 1024, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", default="qa/outputs/vocab-test")
    ap.add_argument("--providers", default="gemini:gemini-flash-lite-latest")
    ap.add_argument("--ocr", default="ocrspace")
    ap.add_argument("--fire-final", action="store_true")
    args = ap.parse_args()

    outdir = pathlib.Path(args.outdir).resolve()
    shots = outdir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    source = pathlib.Path(args.input).resolve()
    shutil.copy2(source, outdir / "01_original.pdf")
    screenshots(source, shots, "original")

    results = []
    specs = [s.strip() for s in args.providers.split(",") if s.strip()]
    for index, spec in enumerate(specs, start=2):
        provider, _, model = spec.partition(":")
        label = f"{provider}-{(model or 'default').split('/')[-1]}"
        target = outdir / f"0{index}_translated_{label}.pdf"
        print(f"--- {label} ---", flush=True)
        started = time.time()
        try:
            translator = build_translator(provider, model or None)
            _, stats = translate_pdf_optimized(
                str(source), translator, "fa", str(target), 1,
                enable_ocr=True, ocr_method=args.ocr, ocr_lang="eng",
                skip_pages=set(), translation_workers=2, return_stats=True,
                use_cache=False, use_checkpoint=False,
                self_review=args.fire_final, final_check=args.fire_final, uniform_font=True,
            )
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            print(f"FAILED {label}: {exc}", flush=True)
            results.append({"provider": provider, "model": model, "label": label,
                            "error": str(exc)[:400]})
            continue
        metrics = measure(source, target, stats, time.time() - started)
        metrics.update(provider=provider, model=model, label=label,
                       usage=getattr(translator, "last_usage", {}),
                       reviewed_blocks=stats.reviewed_blocks,
                       qc_ok=stats.qc_ok, qc_issues=stats.qc_issues)
        screenshots(target, shots, label)
        print("METRICS:" + json.dumps(metrics, ensure_ascii=False), flush=True)
        results.append(metrics)

    (outdir / "metrics.json").write_text(
        json.dumps({"providers": results}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # side-by-side comparisons for every page
    best = next((r for r in results if r.get("translated_blocks")), None)
    if best:
        with pymupdf.open(source) as doc:
            total = doc.page_count
        for n in range(1, total + 1):
            left, right = shots / f"original-p{n}.png", shots / f"{best['label']}-p{n}.png"
            if not right.exists():
                continue
            from PIL import Image

            a, b = Image.open(left), Image.open(right)
            width = 720
            a = a.resize((width, int(a.height * width / a.width)))
            b = b.resize((width, int(b.height * width / b.width)))
            canvas = Image.new("RGB", (a.width + b.width + 16, max(a.height, b.height)), (240, 243, 247))
            canvas.paste(a, (0, 0))
            canvas.paste(b, (a.width + 16, 0))
            canvas.save(shots / f"compare-p{n}.png", optimize=True)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
