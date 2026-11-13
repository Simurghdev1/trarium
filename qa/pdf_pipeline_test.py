"""Real PDF workflow test: real input documents in, real PDFs and screenshots out.

Unlike ``qa_suite.py`` (which builds its own minimal PDFs), this module processes
the committed real-world fixtures in ``qa/fixtures/``:

* ``en_source.pdf``   – "Attention Is All You Need" (arXiv:1706.03762v7), 3 pages
* ``fa_source.pdf``   – real Persian Wikipedia prose, 2 pages, genuine text layer
* ``scan_source.pdf`` – a scanned leaf of *The Modern Hercules* (Internet Archive)

Every output PDF here is produced by Trarium's own writers. Page images are
rendered from those real PDFs, so the screenshots in the report show exactly what
the pipeline emitted.

Only the translation *content* comes from the local mock provider: no AI API key
exists in the environment, so the text is deterministic placeholder Persian and
translation quality is explicitly out of scope (reported as N/A).

Run:
    python -m pytest qa/pdf_pipeline_test.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import sys
import time
import unicodedata
from collections import Counter

import pytest

HERE = pathlib.Path(__file__).resolve().parent
TARGET = pathlib.Path(os.environ.get("TRARIUM_TARGET", HERE.parent)).resolve()
OUT = pathlib.Path(os.environ.get("PDF_QA_OUT", HERE / "_pdf_out")).resolve()
FIX = HERE / "fixtures"
(OUT / "pages").mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HOME", str(OUT / "home"))
os.environ["TRARIUM_OPENROUTER_HTTP"] = "requests"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TARGET))

import pymupdf  # noqa: E402
import requests  # noqa: E402
from mock_provider import STATE, MockServer  # noqa: E402

from trarium.output import ATTRIBUTION_TEXT, save_as_pdf  # noqa: E402
from trarium.pdf_optimized import translate_pdf_optimized  # noqa: E402
from trarium.processors.pipeline import TranslationResult  # noqa: E402
from trarium.translators import openrouter_http  # noqa: E402
from trarium.translators.openai_compatible import OpenAICompatibleTranslator  # noqa: E402

METRICS: dict[str, dict] = {}
PERSIAN = re.compile(r"[\u0600-\u06FF]")
ARABIC_FORMS = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")


def record(name: str, **values) -> None:
    METRICS.setdefault(name, {}).update(values)


@pytest.fixture(scope="module")
def mock():
    with MockServer() as server:
        STATE.reset()
        yield server


@pytest.fixture(autouse=True)
def route_openrouter(monkeypatch, mock):
    def fake_post(url, headers, payload, timeout=180):
        response = requests.post(mock.base_url + "/chat/completions", headers=headers, json=payload, timeout=timeout)
        try:
            data = response.json()
        except Exception:
            data = response.text
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        return openrouter_http.OpenRouterResponse(
            response.status_code, dict(response.headers), data, response.text, "requests"
        )

    monkeypatch.setattr(openrouter_http, "post_json", fake_post)
    import trarium.translators.openrouter as or_module

    monkeypatch.setattr(or_module, "post_json", fake_post)


def translator_for(mock) -> OpenAICompatibleTranslator:
    return OpenAICompatibleTranslator(
        api_key="qa-mock", model_name="mock/model", base_url=mock.base_url, provider_name="Mock"
    )


# ------------------------------------------------------------------ helpers
def normalise(text: str) -> str:
    """Presentation forms -> base letters, so RTL output can be compared fairly."""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", text)


def letters(text: str) -> Counter:
    return Counter(ch for ch in normalise(text) if not ch.isspace())


def image_hashes(doc: pymupdf.Document) -> list[list[str]]:
    hashes = []
    for page in doc:
        page_hashes = []
        for info in page.get_images(full=True):
            try:
                page_hashes.append(hashlib.sha256(doc.xref_stream(info[0]) or b"").hexdigest())
            except Exception:
                page_hashes.append("unreadable")
        hashes.append(page_hashes)
    return hashes


def render_pages(pdf_path: pathlib.Path, prefix: str, dpi: int = 100) -> list[pathlib.Path]:
    out = []
    with pymupdf.open(pdf_path) as doc:
        for index, page in enumerate(doc, 1):
            pix = page.get_pixmap(dpi=dpi)
            path = OUT / "pages" / f"{prefix}-p{index}.png"
            pix.save(path)
            out.append(path)
    return out


def ink_ratio(png_path: pathlib.Path) -> float:
    from PIL import Image

    with Image.open(png_path) as img:
        grey = img.convert("L").resize((240, 240))
        pixels = list(grey.getdata())
    dark = sum(1 for value in pixels if value < 235)
    return round(dark / len(pixels) * 100, 2)


def inspect_pdf(pdf_path: pathlib.Path, prefix: str) -> dict:
    """Page-by-page measurements taken from the produced PDF itself."""
    rendered = render_pages(pdf_path, prefix)
    with pymupdf.open(pdf_path) as doc:
        text = "".join(page.get_text() for page in doc)
        fonts = sorted({f[3] for page in doc for f in page.get_fonts()})
        spans = []
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span["text"].strip():
                            spans.append((span["bbox"], page.rect.width, span["text"]))
        pages = doc.page_count
        width = doc[0].rect.width if pages else 595
    ratios = [ink_ratio(p) for p in rendered]
    blank = [i + 1 for i, ratio in enumerate(ratios) if ratio < 0.4]
    attribution_page = bool(pages) and normalise(doc_last_text(pdf_path)) == normalise(ATTRIBUTION_TEXT)
    content_pages = pages - 1 if attribution_page else pages
    right = [1 for bbox, page_w, _ in spans if bbox[2] > page_w * 0.8]
    return {
        "file": str(pdf_path.relative_to(OUT)),
        "bytes": pdf_path.stat().st_size,
        "pages": pages,
        "page_width": round(width, 1),
        "fonts": fonts,
        "sahel_embedded": any("Sahel" in f for f in fonts),
        "text_chars": len(text),
        "persian_chars": len(PERSIAN.findall(text)),
        "question_marks": text.count("?"),
        "spans": len(spans),
        "right_aligned_spans": len(right),
        "right_aligned_pct": round(len(right) / max(1, len(spans)) * 100, 1),
        "rendered": [str(p.relative_to(OUT)) for p in rendered],
        "ink_ratio_pct": ratios,
        "blank_pages": blank,
        "content_pages": content_pages,
        "attribution_page": attribution_page,
        "blank_content_pages": [p for p in blank if p <= content_pages],
        "page_sizes": page_sizes(pdf_path),
        "text": text,
    }


def doc_last_text(pdf_path: pathlib.Path) -> str:
    with pymupdf.open(pdf_path) as doc:
        return doc[doc.page_count - 1].get_text() if doc.page_count else ""


def page_sizes(pdf_path: pathlib.Path) -> list[str]:
    with pymupdf.open(pdf_path) as doc:
        return sorted({f"{int(p.rect.width)}x{int(p.rect.height)}" for p in doc})


def side_by_side(left: pathlib.Path, right: pathlib.Path, out: pathlib.Path) -> None:
    from PIL import Image

    with Image.open(left) as a, Image.open(right) as b:
        height = max(a.height, b.height)
        canvas = Image.new("RGB", (a.width + b.width + 24, height), (245, 247, 250))
        canvas.paste(a, (0, 0))
        canvas.paste(b, (a.width + 24, 0))
        canvas.save(out)


# ------------------------------------------------------------------- tests
def test_english_pdf_to_persian(mock):
    """Real English text PDF -> hybrid Persian PDF through the real pipeline."""
    source = FIX / "en_source.pdf"
    target = OUT / "en_to_fa.pdf"
    with pymupdf.open(source) as before:
        source_text = "".join(page.get_text() for page in before)
        source_images = image_hashes(before)
        source_pages = before.page_count
    render_pages(source, "en_source")
    STATE.reset()
    started = time.perf_counter()
    _, stats = translate_pdf_optimized(
        str(source), translator_for(mock), "fa", str(target), 1,
        skip_pages=set(), translation_workers=2, return_stats=True,
    )
    elapsed = time.perf_counter() - started

    after = inspect_pdf(target, "en_to_fa_src")
    with pymupdf.open(target) as doc:
        target_images = image_hashes(doc)

    record(
        "en_to_fa",
        source_pages=source_pages,
        output_pages=after["pages"],
        output_content_pages=after["content_pages"],
        attribution_page=after["attribution_page"],
        output_page_sizes=after["page_sizes"],
        source_text_chars=len(source_text),
        source_words=len(source_text.split()),
        translated_blocks=stats.translated_blocks,
        failed_blocks=getattr(stats, "failed_blocks", 0),
        seconds=round(elapsed, 2),
        output_kb=round(after["bytes"] / 1024, 1),
        persian_chars_in_output=after["persian_chars"],
        source_text_removed="Provided proper attribution" not in after["text"],
        images_before=sum(len(v) for v in source_images),
        images_after=sum(len(v) for v in target_images),
        images_identical=source_images == target_images[:source_pages],
        api_requests=STATE.chat_requests,
        prompt_tokens=STATE.prompt_tokens,
        completion_tokens=STATE.completion_tokens,
        blank_pages=after["blank_content_pages"],
        rendered_pages=len(after["rendered"]),
    )
    for index, page_png in enumerate(after["rendered"][: source_pages], 1):
        src_png = OUT / "pages" / f"en_source-p{index}.png"
        side_by_side(src_png, OUT / page_png, OUT / "pages" / f"compare-en-p{index}.png")

    assert after["content_pages"] == source_pages, "content page count changed during hybrid translation"
    assert stats.translated_blocks >= 1
    assert after["persian_chars"] > 0, "no Persian text in the translated PDF"
    assert "Provided proper attribution" not in after["text"], "English source text was not replaced"
    assert source_images == target_images[:source_pages], "page images/figures were re-encoded or lost"
    assert not after["blank_content_pages"], f"blank content pages: {after['blank_content_pages']}"
    assert len(after["page_sizes"]) == 1, f"mixed paper sizes in one document: {after['page_sizes']}"


def test_persian_pdf_rtl_rendering():
    """Real Persian text rendered by Trarium's own PDF writer."""
    source_text = (FIX / "fa_source.txt").read_text(encoding="utf-8")
    render_pages(FIX / "fa_source.pdf", "fa_source")
    result = TranslationResult(
        title="سعدی", author="ویکی‌پدیای فارسی", original_lang="en", target_lang="fa",
        translated_text=source_text,
    )
    target = OUT / "fa_rtl.pdf"
    save_as_pdf(result, str(target))
    info = inspect_pdf(target, "fa_rtl")

    source_letters = letters(source_text)
    output_letters = letters(info["text"])
    kept = sum((source_letters & output_letters).values())
    preservation = round(kept / max(1, sum(source_letters.values())) * 100, 2)
    record(
        "fa_rtl",
        source_chars=len(source_text),
        source_letters=sum(source_letters.values()),
        output_letters=sum(output_letters.values()),
        preserved_letters=kept,
        preservation_pct=preservation,
        pages=info["pages"],
        fonts=info["fonts"],
        sahel_embedded=info["sahel_embedded"],
        question_marks=info["question_marks"],
        presentation_forms_before_extraction=len(ARABIC_FORMS.findall(source_text)),
        right_aligned_pct=info["right_aligned_pct"],
        blank_pages=info["blank_content_pages"],
        attribution_page=info["attribution_page"],
        ink_ratio_pct=info["ink_ratio_pct"],
        bytes=info["bytes"],
    )
    side_by_side(
        OUT / "pages" / "fa_source-p1.png",
        OUT / "pages" / "fa_rtl-p1.png",
        OUT / "pages" / "compare-fa-p1.png",
    )

    assert info["sahel_embedded"], "Persian font is not embedded"
    assert info["question_marks"] == 0, "missing glyphs rendered as '?'"
    assert preservation >= 90, f"Persian text was dropped in the PDF ({preservation}% kept)"
    assert not info["blank_content_pages"], f"blank pages: {info['blank_content_pages']}"
    assert info["right_aligned_pct"] >= 80, "Persian text is not right-aligned"


def test_english_pdf_ltr_rendering():
    """The same writer in LTR mode, to separate RTL bugs from general ones."""
    source = pymupdf.open(FIX / "en_source.pdf")
    text = source[1].get_text().strip()[:2500]
    source.close()
    result = TranslationResult(
        title="Attention Is All You Need", author="Vaswani et al.", original_lang="en",
        target_lang="en", translated_text=text,
    )
    target = OUT / "en_ltr.pdf"
    save_as_pdf(result, str(target))
    info = inspect_pdf(target, "en_ltr")

    source_letters = letters(text)
    output_letters = letters(info["text"])
    kept = sum((source_letters & output_letters).values())
    preservation = round(kept / max(1, sum(source_letters.values())) * 100, 2)
    record(
        "en_ltr",
        source_chars=len(text),
        output_letters=sum(output_letters.values()),
        preserved_letters=kept,
        preservation_pct=preservation,
        pages=info["pages"],
        right_aligned_pct=info["right_aligned_pct"],
        blank_pages=info["blank_content_pages"],
        question_marks=info["question_marks"],
    )
    assert preservation >= 95, f"English text was lost in LTR mode ({preservation}% kept)"
    assert not info["blank_content_pages"]


def test_scanned_pdf_ocr_path(mock):
    """The real scanned leaf goes through the OCR path (vision model is mocked)."""
    STATE.reset()
    target = OUT / "scan_ocr.pdf"
    started = time.perf_counter()
    _, stats = translate_pdf_optimized(
        str(FIX / "scan_source.pdf"), translator_for(mock), "fa", str(target), 1,
        api_key="qa-mock", vision_model="mock/vision", enable_ocr=True, ocr_method="openrouter",
        skip_pages=set(), translation_workers=1, return_stats=True,
    )
    elapsed = time.perf_counter() - started
    info = inspect_pdf(target, "scan_ocr")
    record(
        "scan_ocr",
        pages=info["pages"],
        vision_requests=STATE.vision_requests,
        image_pixels=STATE.image_pixels,
        image_bytes=STATE.image_bytes,
        image_mimes=sorted(set(STATE.image_mimes)),
        translated_blocks=stats.translated_blocks,
        seconds=round(elapsed, 2),
        output_kb=round(info["bytes"] / 1024, 1),
        persian_chars=info["persian_chars"],
        blank_pages=info["blank_content_pages"],
        ocr_accuracy="N/A",
        ocr_accuracy_reason=(
            "The vision endpoint is the local mock (no AI API key in the environment), "
            "so OCR text cannot be compared with the page's ground truth."
        ),
    )
    assert target.exists()
    assert STATE.vision_requests >= 1, "the scanned page never reached the OCR path"
    assert not info["blank_content_pages"], "the scanned page rendered blank after OCR"


def test_collect_metrics():
    """Write the machine-readable metrics and a Markdown section for the report."""
    payload = {name: {k: v for k, v in data.items() if k != "text"} for name, data in METRICS.items()}
    (OUT / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    merged = json.loads((OUT / "metrics.json").read_text(encoding="utf-8"))
    assert {"en_to_fa", "fa_rtl", "en_ltr", "scan_ocr"} <= set(merged)
    print("\nPDF_QA_METRICS:" + json.dumps(merged, ensure_ascii=False))
