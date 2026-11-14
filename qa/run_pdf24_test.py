"""End-to-end test of the PDF24 OCR provider on the real Chinese scan.

What is real here
-----------------
* the input PDF (3 image-only pages, the file from the repo),
* the OCR text layer used for translation (produced earlier by the pipeline's
  vision OCR and stored in ``qa/_cn/ocr_blocks.json``),
* the translation (Google AI Studio, free model ``gemini-3.5-flash``, real key),
* the final PDF, written by Trarium itself.

What is *not* the real PDF24 binary
-----------------------------------
``pdf24-Ocr.exe`` only ships with PDF24 Creator for Windows and cannot run on
this Linux machine. So the provider is exercised through a stand-in executable
that implements the documented command-line contract exactly
(``-outputFile``/``-language``/``-dpi`` + one input file, exit code 0/1) and
returns a PDF that has a text layer. The Trarium code path — copy input, build
arguments, run the process, check the exit code, read the layer, translate — is
the same one that will call the real exe on Windows.

Run:
    TRARIUM_PDF24_BIN=... python qa/run_pdf24_test.py --input "....pdf"
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import unicodedata

import pymupdf

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa"))

from trarium.pdf_optimized import translate_pdf_optimized  # noqa: E402
from trarium.translators.gemini import GeminiTranslator  # noqa: E402

STANDIN = '''#!/usr/bin/env {python}
"""Stand-in for pdf24-Ocr.exe implementing its documented CLI contract."""
import sys, pathlib, shutil, os
from PIL import Image

args = sys.argv[1:]
out, lang, dpi, files = None, "eng", "300", []
i = 0
while i < len(args):
    a = args[i]
    if a == "-outputFile": out = args[i + 1]; i += 2
    elif a == "-language": lang = args[i + 1]; i += 2
    elif a == "-dpi": dpi = args[i + 1]; i += 2
    elif a.startswith("-"): i += 1
    else: files.append(a); i += 1
if not out or len(files) != 1:
    sys.stderr.write("usage: pdf24-Ocr.exe -outputFile out.pdf input.pdf\\n")
    sys.exit(1)
prepared = os.environ.get("PDF24_STANDIN_LAYER")
if not prepared or not pathlib.Path(prepared).exists():
    sys.stderr.write("stand-in: no prepared text layer\\n")
    sys.exit(1)
shutil.copyfile(prepared, out)
sys.exit(0)
'''


def build_text_layer(blocks_path: pathlib.Path, source: pathlib.Path, target: pathlib.Path) -> None:
    """Write a PDF that looks like what an OCR tool returns: page image + text layer."""
    blocks = json.loads(blocks_path.read_text(encoding="utf-8"))
    src = pymupdf.open(source)
    doc = pymupdf.open()
    for page_no in range(1, src.page_count + 1):
        page = src[page_no - 1]
        new = doc.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=page.get_pixmap(dpi=90))
        for block in blocks.get(str(page_no), []):
            x0, y0, x1, y1 = block["bbox"]
            try:
                new.insert_text((x0, max(8, y1 - 1)), block["text"], fontname="china-s", fontsize=max(6, min(14, (y1 - y0) * 0.7)))
            except Exception:
                continue
    doc.save(target)
    doc.close()
    src.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", default="qa/outputs/pdf24-test")
    ap.add_argument("--blocks", default="qa/_cn/ocr_blocks.json")
    args = ap.parse_args()

    outdir = pathlib.Path(args.outdir).resolve()
    (outdir / "screenshots").mkdir(parents=True, exist_ok=True)
    source = pathlib.Path(args.input).resolve()
    shutil.copy2(source, outdir / "01_original.pdf")

    layer = outdir / "ocr_layer_source.pdf"
    build_text_layer(pathlib.Path(args.blocks), source, layer)

    binary = outdir / "pdf24-Ocr.exe"
    binary.write_text(STANDIN.format(python=sys.executable), encoding="utf-8")
    binary.chmod(0o755)
    os.environ["TRARIUM_PDF24_BIN"] = str(binary)
    os.environ["PDF24_STANDIN_LAYER"] = str(layer)

    from trarium import ocr_pdf24
    print("pdf24 binary:", ocr_pdf24.find_binary(), flush=True)
    print("available:", ocr_pdf24.is_available(), flush=True)

    translator = GeminiTranslator(api_key=os.environ["GEMINI_API_KEY"], model_name="gemini-3.5-flash")
    target = outdir / "03_translated_fa.pdf"
    started = time.time()
    _, stats = translate_pdf_optimized(
        str(source), translator, "fa", str(target), 1,
        enable_ocr=True, ocr_method="pdf24", ocr_lang="chi_sim+eng",
        skip_pages=set(), translation_workers=2, return_stats=True,
        use_cache=False, use_checkpoint=False,
    )
    elapsed = time.time() - started
    print(f"translation: {elapsed:.1f}s blocks={stats.translated_blocks} failed={stats.failed_blocks}",
          flush=True)
    print("pdf24 ocr file:", stats.ocr_pdf_path, flush=True)
    if stats.ocr_pdf_path and pathlib.Path(stats.ocr_pdf_path).exists():
        shutil.copy2(stats.ocr_pdf_path, outdir / "02_pdf24_ocr.pdf")

    metrics = measure(source, outdir / "02_pdf24_ocr.pdf", target, stats, elapsed, translator)
    (outdir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PDF24_METRICS:" + json.dumps(metrics, ensure_ascii=False), flush=True)
    return 0


def measure(source, ocr_pdf, translated, stats, elapsed, translator) -> dict:
    out = pymupdf.open(translated)
    text = unicodedata.normalize("NFKC", "".join(p.get_text() for p in out))
    spans = [s for p in out for b in p.get_text("dict")["blocks"]
             for line in b.get("lines", []) for s in line.get("spans", []) if s["text"].strip()]
    right = [s for s in spans if s["bbox"][2] > out[0].rect.width * 0.8]
    pages_dir = pathlib.Path(translated).parent / "screenshots"
    for prefix, doc in (("original", pymupdf.open(source)), ("ocr", pymupdf.open(ocr_pdf)), ("translated", out)):
        for i, page in enumerate(doc, 1):
            page.get_pixmap(dpi=95).save(pages_dir / f"{prefix}-p{i}.png")
    result = {
        "source_pages": pymupdf.open(source).page_count,
        "translated_pages": out.page_count,
        "ocr_text_layer_chars": len(pymupdf.open(ocr_pdf)[0].get_text()),
        "translated_blocks": stats.translated_blocks,
        "failed_blocks": stats.failed_blocks,
        "write_failures": stats.write_failures,
        "persian_chars": len([c for c in text if "\u0600" <= c <= "\u06ff"]),
        "chinese_chars_left": len([c for c in text if "\u4e00" <= c <= "\u9fff"]),
        "question_marks": text.count("?"),
        "right_aligned_pct": round(len(right) / max(1, len(spans)) * 100, 1),
        "usage": getattr(translator, "last_usage", {}),
        "seconds": round(elapsed, 1),
        "output_kb": round(pathlib.Path(translated).stat().st_size / 1024, 1),
        "ocr_engine": "PDF24 CLI (documented contract; Windows exe unavailable on Linux)",
        "ocr_pdf": "02_pdf24_ocr.pdf",
    }
    out.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
