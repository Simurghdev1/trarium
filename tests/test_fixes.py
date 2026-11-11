"""تست‌های آفلاین اصلاحات نسخهٔ ۲٫۱۶٫۰ — بدون شبکه، بدون کلید API.

اجرا:
    python -m pytest tests/ -v
"""
import io
import os
import posixpath
import sys
import zipfile

import pymupdf
import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trarium.extractors import get_extractor                       # noqa: E402
from trarium.output import save_as_epub, save_as_pdf                # noqa: E402
from trarium.pdf_optimized import (                                  # noqa: E402
    _translate_with_markers,
    _vision_image_data_url,
    translate_pdf_optimized,
)
from trarium.processors import TextChunker                          # noqa: E402
from trarium.processors.pipeline import TranslationResult           # noqa: E402


# ---------------------------------------------------------------- helpers

def _make_pdf(path, pages=2):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Chapter {i + 1}: Sample Heading", fontsize=18, fontname="hebo")
        page.insert_text((72, 140), "This is body text on the sample page.", fontsize=11)
    doc.save(path)
    doc.close()
    return path


def _result(text, lang="fa"):
    return TranslationResult(
        title="نمونه", author="نویسنده", original_lang="en", target_lang=lang,
        translated_text=text, total_chunks=1, translated_chunks=1,
        total_chars=len(text), translated_chars=len(text),
    )


class MarkerTranslator:
    """مترجم جعلی که تگ‌های BLOCK را حفظ می‌کند و متن را جایگزین می‌کند."""

    def __init__(self):
        self.calls = 0

    def translate(self, text, target_lang="fa"):
        import re
        self.calls += 1
        # متن اصلی را عیناً برنمی‌گرداند؛ وگرنه نمی‌توان سنجید که redaction
        # واقعاً متن انگلیسی را حذف کرده است.
        return re.sub(r"\[BLOCK_(\d+)\][^\[]*", r"[BLOCK_\1] این ترجمه است", text)

    def get_token_limit(self):
        return 100_000


# ---------------------------------------------------------------- EPUB

def test_epub_extraction_works(tmp_path):
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("test-id")
    book.set_title("Sample")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="c1.xhtml", lang="en")
    chapter.content = "<html><body><h1>Chapter 1</h1><p>Hello world paragraph.</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    book.toc = [chapter]

    path = str(tmp_path / "book.epub")
    epub.write_epub(path, book, {})

    extracted = get_extractor(path).extract(path)
    assert "Hello world paragraph" in extracted.full_text
    assert extracted.chapters


def test_pdf_and_txt_extraction(tmp_path):
    pdf = _make_pdf(str(tmp_path / "b.pdf"))
    book = get_extractor(pdf).extract(pdf)
    assert "Sample Heading" in book.full_text

    txt = tmp_path / "b.txt"
    txt.write_text("Chapter One\n\nFirst paragraph.\n\nSecond paragraph.", encoding="utf-8")
    plain = get_extractor(str(txt)).extract(str(txt))
    assert len(plain.chapters) == 3


# ---------------------------------------------------------------- chunker

def test_chunk_text_never_splits_words():
    text = "Sentence number one is here. " * 200
    chunks = TextChunker(max_chars=1000, overlap=0).chunk_text(text)
    assert all(len(c) <= 1000 for c in chunks)
    # هیچ کلمه‌ای نباید نصف شود (باگ قدیمی: «Sentence» → «S» + «entence»)
    assert text.split() == " ".join(chunks).split()


def test_chunk_text_handles_text_without_breaks():
    text = "x" * 5000
    chunks = TextChunker(max_chars=1000, overlap=0).chunk_text(text)
    assert chunks and all(len(c) <= 1000 for c in chunks)
    assert "".join(chunks) == text


def test_chunk_by_chapters_merges_pdf_pages():
    chapters = [{"title": f"صفحه {i + 1}", "content": "متن کوتاه صفحه."} for i in range(6)]
    chunks = TextChunker(max_chars=15000, overlap=0).chunk_by_chapters(chapters)
    assert len(chunks) == 1, "صفحات PDF باید در یک درخواست جمع شوند"


def test_chunk_by_chapters_keeps_real_chapter_boundaries():
    chapters = [{"title": f"Chapter {i + 1}", "content": "short chapter text"} for i in range(3)]
    chunks = TextChunker(max_chars=15000, overlap=0).chunk_by_chapters(chapters)
    assert len(chunks) == 3, "فصل‌های دارای عنوان نباید در هم ادغام شوند"


# ---------------------------------------------------------------- PDF output

def test_pdf_output_embeds_persian_font(tmp_path):
    path = save_as_pdf(_result("این یک متن آزمایشی فارسی است."), str(tmp_path / "o.pdf"))
    doc = pymupdf.open(path)
    text = "".join(page.get_text() for page in doc)
    fonts = [f[3] for page in doc for f in page.get_fonts()]
    spans = [s for page in doc for b in page.get_text("dict")["blocks"]
             for line in b.get("lines", []) for s in line.get("spans", [])]
    doc.close()

    assert any("Sahel" in f for f in fonts), fonts
    assert "?" not in text
    assert spans, "هیچ متنی نوشته نشده"
    # راست‌چین: انتهای سطرها نزدیک حاشیهٔ راست صفحه است
    assert max(s["bbox"][2] for s in spans) > 480


def test_pdf_output_english_stays_left_aligned(tmp_path):
    path = save_as_pdf(_result("Plain english sentence.", lang="en"), str(tmp_path / "en.pdf"))
    doc = pymupdf.open(path)
    spans = [s for page in doc for b in page.get_text("dict")["blocks"]
             for line in b.get("lines", []) for s in line.get("spans", [])]
    doc.close()
    assert min(s["bbox"][0] for s in spans) < 100


# ---------------------------------------------------------------- EPUB output

def test_epub_output_font_path_and_manifest(tmp_path):
    path = save_as_epub(_result("--- بخش ---\n\nمتن ترجمه‌شده."), str(tmp_path / "o.epub"))
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        css = zf.read("OEBPS/style.css").decode("utf-8")
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert names[0] == "mimetype"
        assert "fonts/Sahel.ttf" in css
        resolved = posixpath.normpath(posixpath.join("OEBPS", "fonts/Sahel.ttf"))
        assert resolved in names
        assert "OEBPS/fonts/Sahel.ttf" in names
        assert "fonts/Sahel.ttf" in opf
        assert "../fonts" not in css


# ---------------------------------------------------------------- hybrid PDF

def test_hybrid_pdf_keeps_images_and_removes_original_text(tmp_path):
    src = tmp_path / "with_image.pdf"
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), (150, 190, 225)).save(buf, format="PNG")

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=buf.getvalue())
    page.insert_text((72, 200), "Original english sentence here.", fontsize=14)
    doc.save(str(src))
    doc.close()

    before = pymupdf.open(str(src))
    images_before = sum(len(p.get_images()) for p in before)
    before.close()

    out = tmp_path / "translated.pdf"
    result_path = translate_pdf_optimized(
        str(src), MarkerTranslator(), "fa", str(out), 1,
        skip_pages=set(), precomputed_ocr={},
    )
    assert result_path and os.path.isfile(result_path)

    doc = pymupdf.open(result_path)
    text = "".join(p.get_text() for p in doc)
    images_after = sum(len(p.get_images()) for p in doc)
    pixel = doc[0].get_pixmap().pixel(300, 700)
    fonts = [f[3] for f in doc[0].get_fonts()]
    doc.close()

    assert images_after >= images_before, (images_before, images_after)
    assert "Original english sentence" not in text, "متن اصلی باید حذف شود"
    assert pixel != (255, 255, 255), "هیچ مستطیل سفیدی نباید روی تصویر کشیده شود"
    assert any("Sahel" in f for f in fonts), fonts
    assert "?" not in text, text[:80]


# ---------------------------------------------------------------- OCR cost

def test_vision_image_sends_far_fewer_pixels():
    """هزینهٔ توکن Vision با تعداد پیکسل تصویر تعیین می‌شود، نه حجم فایل."""
    import base64

    doc = pymupdf.open()
    page = doc.new_page()
    for i in range(40):
        page.insert_text((72, 80 + i * 18), f"Scanned line number {i + 1} of the page.", fontsize=11)

    old_pix = page.get_pixmap(dpi=300)
    old_pixels = old_pix.width * old_pix.height
    old_bytes = len(base64.b64encode(old_pix.tobytes("png")))

    data_url = _vision_image_data_url(page)
    doc.close()

    assert data_url.startswith("data:image/jpeg"), data_url[:40]
    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        new_pixels = img.size[0] * img.size[1]
        assert max(img.size) <= 1700, img.size

    print(f"pixels: {old_pixels:,} -> {new_pixels:,} | bytes(base64): {old_bytes:,} -> {len(data_url):,}")
    assert new_pixels < old_pixels * 0.3
    assert len(data_url) < old_bytes * 0.8


# ---------------------------------------------------------------- markers

def test_missing_marker_is_retried_per_block():
    class Dropping:
        def __init__(self):
            self.calls = []

        def translate(self, text, target_lang="fa"):
            self.calls.append(text)
            if "[BLOCK_" in text:
                return "[BLOCK_0]\nیک"
            return "دو"

    translator = Dropping()
    out = _translate_with_markers(translator, ["first", "second"], "fa")
    assert out == ["یک", "دو"]
    assert len(translator.calls) == 2  # یک درخواست صفحه + یک تلاش تک‌بلاکی


def test_marker_like_text_in_source_does_not_break_alignment():
    class Echo:
        def translate(self, text, target_lang="fa"):
            return text

    out = _translate_with_markers(Echo(), ["see [BLOCK_1] here", "second block"], "fa")
    assert out[0].startswith("see")
    assert out[1].startswith("second")
