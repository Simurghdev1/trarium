"""Real end-to-end audit suite for Trarium v2.17.0 (and any patched copy).

Everything here runs for real: an HTTP mock provider speaks the same protocol as
OpenRouter/Groq/OpenAI-compatible endpoints, so the transport, retry, parsing,
caching and PDF/EPUB writers are exercised — only a third-party API key is
replaced. No network access is required.

Run:
    TRARIUM_TARGET=/path/to/project QA_TMP=/tmp/qa python -m pytest qa_suite.py -v
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import resource
import sys
import time
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TARGET = Path(os.environ.get("TRARIUM_TARGET", HERE.parent)).resolve()
QA_TMP = Path(os.environ.get("QA_TMP") or (HERE / "_tmp")).resolve()
(QA_TMP / "home").mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(QA_TMP / "home")
os.environ["TRARIUM_OPENROUTER_HTTP"] = "requests"  # deterministic transport

sys.path.insert(0, str(TARGET))

import pymupdf  # noqa: E402
import requests  # noqa: E402
from PIL import Image  # noqa: E402
from mock_provider import STATE, MockServer  # noqa: E402

from trarium.cache import TranslationCache  # noqa: E402
from trarium.checkpoint import CheckpointStore  # noqa: E402
from trarium.extractors.base import ExtractedBook  # noqa: E402
from trarium.output import save_as_epub, save_as_pdf, save_as_text  # noqa: E402
from trarium.pdf_optimized import (  # noqa: E402
    extract_ocr_blocks_batch_openrouter,
    translate_pdf_optimized,
)
from trarium.processors import TextChunker  # noqa: E402
from trarium.processors.pipeline import TranslationPipeline, TranslationResult  # noqa: E402
from trarium.translators import openrouter_http  # noqa: E402
from trarium.translators.openai_compatible import OpenAICompatibleTranslator  # noqa: E402
from trarium.translators.openrouter import FREE_MODELS, OpenRouterTranslator  # noqa: E402

RESULTS: dict[str, dict] = {}


def record(name: str, **values):
    RESULTS.setdefault(name, {}).update(values)


@pytest.fixture()
def mock():
    with MockServer() as server:
        STATE.reset()
        yield server


@pytest.fixture()
def translator(mock):
    return OpenAICompatibleTranslator(
        api_key="test-key", model_name="mock/model", base_url=mock.base_url, provider_name="Mock"
    )


def _route_openrouter(monkeypatch, mock):
    """Point OpenRouter's hard-coded endpoint at the local mock (real HTTP still happens)."""

    def fake_post(url, headers, payload, timeout=180):
        response = requests.post(mock.base_url + "/chat/completions", headers=headers, json=payload, timeout=timeout)
        try:
            data = response.json()
        except Exception:
            data = response.text
        # Mirror the real transport's error classification.
        if openrouter_http._is_waf_security_block(response.status_code, response.text, data):
            raise openrouter_http.OpenRouterSecurityBlockError(
                "WAF blocked", source="waf", http_status=403, raw=data
            )
        if response.status_code == 403:
            raise openrouter_http.OpenRouterSecurityBlockError(
                "403 blocked", source="api_guardrail_or_account", http_status=403, raw=data
            )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
        return openrouter_http.OpenRouterResponse(
            response.status_code, dict(response.headers), data, response.text, "requests"
        )

    monkeypatch.setattr(openrouter_http, "post_json", fake_post)
    import trarium.translators.openrouter as _or_module
    monkeypatch.setattr(_or_module, "post_json", fake_post)


def _make_text_pdf(path: Path, pages: int = 2):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((70, 100), f"Chapter {i + 1}: The Quiet Garden", fontsize=16, fontname="hebo")
        page.insert_textbox(
            pymupdf.Rect(70, 120, 520, 220),
            "The original English text sits on top of the illustration. "
            "The previous version covered this area with a white box and damaged the picture. "
            "Now the text is replaced and the picture stays intact.",
            fontsize=11,
        )
    doc.save(path)
    doc.close()
    return path


def _image_pdf(path: Path, pages: int = 1, with_text: bool = True):
    buf = io.BytesIO()
    img = Image.new("RGB", (900, 1200), (150, 190, 225))
    for x in range(0, 900, 30):
        for y in range(0, 1200, 30):
            img.putpixel((x, y), (60, 90, 140))
    img.save(buf, format="PNG")
    blob = buf.getvalue()

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=blob)
        if with_text:
            page.insert_text((60, 200), f"Original english sentence number {i + 1}.", fontsize=14)
    doc.save(path)
    doc.close()
    return path, blob


# --------------------------------------------------------------------- chunker
def test_chunk_text_does_not_split_words():
    text = "Sentence number one is here. " * 120
    chunks = TextChunker(max_chars=900, overlap=0).chunk_text(text)
    joined = " ".join(chunks).split()
    record("chunker", source_tokens=len(text.split()), chunk_tokens=len(joined), chunks=len(chunks))
    assert joined == text.split(), "chunker split a word in half"


def test_chunk_text_handles_unbroken_text():
    text = "x" * 4000
    chunks = TextChunker(max_chars=900, overlap=0).chunk_text(text)
    assert "".join(chunks) == text


# -------------------------------------------------------------------- pipeline
def test_pipeline_end_to_end_http(translator, tmp_path):
    book = ExtractedBook(
        "Book", "Author",
        [{"title": f"Chapter {i}", "content": ("Hello world. " * 40).strip()} for i in range(1, 4)],
        "", 0,
    )
    cache = TranslationCache(str(tmp_path / "c.sqlite3"))
    cp = CheckpointStore(str(tmp_path / "cp.sqlite3"))
    result = TranslationPipeline(translator, "fa", chunk_size=4000, max_workers=2, cache=cache, checkpoint=cp).run(book)
    record("pipeline", api_requests=STATE.chat_requests, prompt_tokens=STATE.prompt_tokens,
           completion_tokens=STATE.completion_tokens, chunks=result.total_chunks,
           failed=result.failed_chunks, translated_chars=result.translated_chars)
    assert result.failed_chunks == 0
    assert result.translated_chars > 0
    assert result.api_requests >= 1


def test_pipeline_reuses_cache_without_new_requests(translator, tmp_path):
    book = ExtractedBook("Book", "", [{"title": "Chapter 1", "content": "Hello world. " * 30}], "", 0)
    cache = TranslationCache(str(tmp_path / "c.sqlite3"))
    cp = CheckpointStore(str(tmp_path / "cp.sqlite3"))
    TranslationPipeline(translator, "fa", chunk_size=4000, max_workers=1, cache=cache, checkpoint=cp).run(book)
    first = STATE.chat_requests
    second = TranslationPipeline(translator, "fa", chunk_size=4000, max_workers=1, cache=cache, checkpoint=cp).run(book)
    record("cache", first_run_requests=first, second_run_requests=STATE.chat_requests - first,
           cache_hits=second.cache_hits, checkpoint_hits=second.checkpoint_hits)
    assert STATE.chat_requests == first, "cache/checkpoint did not prevent a duplicate API call"
    assert second.cache_hits + second.checkpoint_hits >= 1


def test_pipeline_retries_transient_500(translator):
    STATE.reset(mode="ok", fail_remaining=1)
    book = ExtractedBook("B", "", [{"title": "C1", "content": "Hello world. " * 20}], "", 0)
    result = TranslationPipeline(translator, "fa", chunk_size=4000, max_workers=1).run(book)
    record("retry_500", requests=STATE.requests, failed=result.failed_chunks)
    assert result.failed_chunks == 0
    assert STATE.requests >= 2


def test_pipeline_survives_empty_provider_output(translator):
    STATE.reset(mode="empty")
    book = ExtractedBook("B", "", [{"title": "C1", "content": "Content that must not be lost. " * 10}], "", 0)
    result = TranslationPipeline(translator, "fa", chunk_size=4000, max_workers=1).run(book)
    record("empty_output", requests=STATE.requests, failed=result.failed_chunks)
    assert result.failed_chunks == 1
    assert "Content that must not be lost." in result.translated_text


def test_openrouter_waf_error_is_not_retried(mock, monkeypatch):
    _route_openrouter(monkeypatch, mock)
    STATE.reset(mode="waf403")
    translator = OpenRouterTranslator(api_key="test", model_name="openrouter/free")
    with pytest.raises(openrouter_http.OpenRouterSecurityBlockError):
        translator.translate("Hello", "fa")
    record("waf", http_requests=STATE.requests)
    assert STATE.requests <= 2, "a deterministic 403 was retried like a transient error"


# ------------------------------------------------------------------ OCR costs
def test_vision_ocr_payload_cost(mock, monkeypatch):
    _route_openrouter(monkeypatch, mock)
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        for line in range(12):
            page.insert_text((60, 90 + line * 20), f"Scanned page {i + 1} line {line + 1} of the book.", fontsize=11)
    # Re-fetch page objects: this PyMuPDF build invalidates older Page handles.
    pages = [(i, doc[i]) for i in range(3)]

    STATE.reset()
    blocks = extract_ocr_blocks_batch_openrouter(pages, api_key="test", model_name="openrouter/free", batch_size=3)
    doc.close()

    pixels = STATE.image_pixels
    a4_300dpi = 2480 * 3508
    record("ocr_payload", requests=STATE.vision_requests, pages=len(pixels),
           max_pixels=max(pixels), avg_pixels=sum(pixels) // max(1, len(pixels)),
           a4_300dpi_pixels=a4_300dpi,
           ratio_of_300dpi=round(sum(pixels) / max(1, len(pixels)) / a4_300dpi, 3),
           mimes=sorted(set(STATE.image_mimes)),
           bytes=sum(STATE.image_bytes), blocks=sum(len(v) for v in blocks.values()))
    assert STATE.vision_requests == 1, "one batched request for three pages"
    assert sum(len(v) for v in blocks.values()) > 0
    assert max(pixels) < a4_300dpi * 0.35, "OCR image is rendered far larger than needed"


# ------------------------------------------------------------------ PDF write
def test_pdf_hybrid_preserves_images_and_replaces_text(mock, tmp_path):
    src, original_blob = _image_pdf(tmp_path / "src.pdf", pages=1, with_text=True)
    before = pymupdf.open(src)
    before_hash = hashlib.sha256(before[0].get_images(full=True) and
                                 before.xref_stream(before[0].get_images(full=True)[0][0]) or b"").hexdigest()
    before.close()

    translator = OpenAICompatibleTranslator(api_key="t", model_name="mock/model", base_url=mock.base_url)
    out, stats = translate_pdf_optimized(
        str(src), translator, "fa", str(tmp_path / "out.pdf"), 1,
        skip_pages=set(), precomputed_ocr={}, translation_workers=1, return_stats=True,
    )

    doc = pymupdf.open(out)
    page = doc[0]
    images = page.get_images(full=True)
    after_hash = hashlib.sha256(doc.xref_stream(images[0][0]) if images else b"").hexdigest()
    pixel_center = page.get_pixmap().pixel(300, 700)
    text = "".join(p.get_text() for p in doc)
    fonts = [f[3] for f in page.get_fonts()]
    doc.close()

    record("hybrid_pdf", images_before=1, images_after=len(images), image_identical=before_hash == after_hash,
           pixel_at_300_700=list(pixel_center), translated_blocks=stats.translated_blocks,
           original_text_removed="Original english sentence" not in text, fonts=sorted(set(fonts)),
           text_len=len(text))
    assert len(images) == 1, "the page image disappeared during redaction"
    assert before_hash == after_hash, "image bytes were re-encoded/damaged by redaction"
    assert pixel_center != (255, 255, 255), "a white box was painted over the page"
    assert "Original english sentence" not in text, "source text was not removed"
    assert stats.translated_blocks >= 1


def test_save_as_pdf_keeps_every_paragraph(tmp_path):
    words = 500
    para = ("این یک جمله آزمایشی برای سنجش کامل بودن متن است " * (words // 9)).strip()
    result = TranslationResult(title="عنوان فارسی کتاب", author="نویسنده", original_lang="en",
                               target_lang="fa", translated_text=para)
    path = save_as_pdf(result, str(tmp_path / "full.pdf"))
    doc = pymupdf.open(path)
    text = "".join(p.get_text() for p in doc)
    pages = len(doc)
    doc.close()
    source_letters = len(para.replace(" ", ""))
    written_letters = len(text.replace(" ", "").replace("\n", ""))
    record("pdf_completeness", source_letters=source_letters, written_letters=written_letters,
           pages=pages, ratio=round(written_letters / max(1, source_letters), 3))
    assert written_letters >= source_letters * 0.92, "paragraph text was silently dropped in the PDF"


def test_save_as_pdf_rtl_font_and_no_missing_glyphs(tmp_path):
    result = TranslationResult(title="عنوان", author="", original_lang="en", target_lang="fa",
                               translated_text="سلام دنیا، این متن فارسی آزمایشی است.")
    path = save_as_pdf(result, str(tmp_path / "rtl.pdf"))
    doc = pymupdf.open(path)
    text = "".join(p.get_text() for p in doc)
    fonts = [f[3] for p in doc for f in p.get_fonts()]
    spans = [s for p in doc for b in p.get_text("dict")["blocks"] for line in b.get("lines", []) for s in line.get("spans", [])]
    doc.close()
    record("rtl", fonts=sorted(set(fonts)), has_question_marks="?" in text,
           max_x1=round(max(s["bbox"][2] for s in spans), 1), text_len=len(text))
    assert any("Sahel" in f for f in fonts), "Persian font is not embedded"
    assert "?" not in text, "unmapped glyphs (?????) in the output"
    assert max(s["bbox"][2] for s in spans) > 480, "Persian text is not right-aligned"


# ----------------------------------------------------------------------- EPUB
def test_epub_declares_font_and_is_readable(tmp_path):
    result = TranslationResult(title="کتاب", author="", original_lang="en", target_lang="fa",
                               translated_text="--- فصل اول ---\n\nمتن ترجمه شده.")
    path = save_as_epub(result, str(tmp_path / "b.epub"))
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        css = zf.read("OEBPS/style.css").decode("utf-8")
    record("epub", has_font_file="OEBPS/fonts/Sahel.ttf" in names,
           font_in_manifest="Sahel.ttf" in opf, font_in_css="Sahel.ttf" in css,
           first_entry=names[0], entries=len(names))
    assert names[0] == "mimetype"
    assert "OEBPS/fonts/Sahel.ttf" in names
    assert "Sahel.ttf" in opf, "font file is bundled but not declared in the OPF manifest"


# ------------------------------------------------------------------ providers
def test_openai_compatible_exposes_connection_probe():
    translator = OpenAICompatibleTranslator(api_key="k", model_name="m", base_url="http://127.0.0.1:1/v1")
    assert hasattr(translator, "test_connection"), "UI connection probe is dead code (defined at module level)"


def test_openrouter_catalog_slugs_exist():
    catalog = HERE / "openrouter_models.json"
    if not catalog.exists():
        pytest.skip("catalog snapshot missing")
    ids = {m["id"] for m in json.loads(catalog.read_text())["data"]}
    missing = sorted({slug for slug in FREE_MODELS.values() if slug not in ids})
    record("model_slugs", checked=len(FREE_MODELS), missing=missing, catalog_size=len(ids))
    if os.environ.get("QA_STRICT_SLUGS") != "1":
        pytest.skip(f"catalog drift (informational): {missing}")
    assert not missing, f"model slugs missing from the live OpenRouter catalog: {missing}"


# --------------------------------------------------------------- OCR fallback
def test_scanned_pdf_uses_vision_ocr(mock, monkeypatch, tmp_path):
    _route_openrouter(monkeypatch, mock)
    # build a truly scanned page: render text into a bitmap, then embed it
    render = pymupdf.open()
    page = render.new_page()
    page.insert_text((60, 120), "Scanned source text for OCR testing.", fontsize=16)
    pix = page.get_pixmap(dpi=110)
    blob = pix.tobytes("png")
    render.close()

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=blob)
    doc.save(tmp_path / "scan.pdf")
    doc.close()

    translator = OpenAICompatibleTranslator(api_key="t", model_name="mock/model", base_url=mock.base_url)
    out, stats = translate_pdf_optimized(
        str(tmp_path / "scan.pdf"), translator, "fa", str(tmp_path / "scan_out.pdf"),
        1, api_key="test", vision_model="openrouter/free", enable_ocr=True, ocr_method="openrouter",
        skip_pages=set(), translation_workers=1, return_stats=True,
    )
    record("ocr_fallback", vision_requests=STATE.vision_requests, translated_blocks=stats.translated_blocks)
    assert STATE.vision_requests >= 1
    assert stats.translated_blocks >= 1


# --------------------------------------------------------- resource behaviour
def test_memory_and_time_for_30_page_hybrid(mock, tmp_path):
    src, _ = _image_pdf(tmp_path / "big.pdf", pages=30, with_text=True)
    translator = OpenAICompatibleTranslator(api_key="t", model_name="mock/model", base_url=mock.base_url)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    translate_pdf_optimized(str(src), translator, "fa", str(tmp_path / "big_out.pdf"), 1,
                            skip_pages=set(), translation_workers=4)
    elapsed = time.perf_counter() - started
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    record("resources", pages=30, seconds=round(elapsed, 2), peak_rss_mb=round(after / 1024, 1),
           rss_delta_mb=round((after - before) / 1024, 1), requests=STATE.chat_requests,
           output_mb=round((tmp_path / "big_out.pdf").stat().st_size / 1048576, 2))
    assert (tmp_path / "big_out.pdf").exists()


def test_no_duplicate_function_definitions():
    """The file re-defines core helpers; the later definition silently wins."""
    source = (TARGET / "trarium" / "pdf_optimized.py").read_text(encoding="utf-8")
    names = re.findall(r"^def (_translate_blocks_resilient|_translation_chunks|_parse_marked_translation)\(", source, re.M)
    duplicates = sorted({n for n in names if names.count(n) > 1})
    record("duplicate_defs", duplicates=duplicates)
    assert not duplicates, f"duplicate definitions in pdf_optimized.py: {duplicates}"


def test_text_and_attribution_output(tmp_path):
    result = TranslationResult(title="T", author="", original_lang="en", target_lang="fa",
                               translated_text="--- فصل ---\n\nیک متن.")
    path = save_as_text(result, str(tmp_path / "o.txt"))
    content = Path(path).read_text(encoding="utf-8")
    assert "ترجمه شده توسط تراریوم" in content


def test_prompt_overhead_per_request(translator):
    """Measure how many tokens each request spends on instructions vs. content."""
    book = ExtractedBook(
        "B", "", [{"title": f"Chapter {i}", "content": ("The quick brown fox jumps over the lazy dog. " * 12).strip()} for i in range(1, 9)], "", 0
    )
    STATE.reset()
    TranslationPipeline(translator, "fa", chunk_size=4000, max_workers=1,
                        translation_memory=False if hasattr(TranslationPipeline, "translation_memory") else True).run(book)
    prompts = list(STATE.prompts)
    overhead = 0
    imported = False
    try:
        from tiktoken import get_encoding
        enc = get_encoding("cl100k_base")
        imported = True
    except Exception:
        enc = None
    if imported:
        for prompt in prompts:
            body = prompt.split("\n\n")[-1]
            overhead += len(enc.encode(prompt)) - len(enc.encode(body))
    record("prompt_overhead", requests=STATE.chat_requests, total_prompt_tokens=STATE.prompt_tokens,
           overhead_tokens=overhead, overhead_per_request=overhead // max(1, STATE.chat_requests))
    assert STATE.chat_requests >= 1


def teardown_module(module):
    out = HERE / f"qa_results_{os.environ.get('QA_LABEL', 'run')}.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nQA_SUMMARY_FILE:", out)
    print("QA_SUMMARY_JSON:" + json.dumps(RESULTS, ensure_ascii=False))
