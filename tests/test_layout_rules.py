"""Rules that protect the page: no translation on artwork, no white boxes."""
from __future__ import annotations

import unicodedata

import pymupdf
import pytest

from trarium.pdf_optimized import (
    _block_on_artwork,
    _has_page_image,
    translate_pdf_optimized,
)

PAPER = (0.85, 0.79, 0.62)  # a cream scan, not white


class _Stub:
    provider = "stub"
    model = "stub-1"
    last_usage: dict = {}

    def __init__(self, answer="این یک جملهٔ آزمایشی فارسی است."):
        self.answer = answer
        self.calls = 0

    def translate(self, text: str, target_lang: str = "fa") -> str:
        self.calls += 1
        return self.answer


def _colour(rect, pixmap) -> tuple[float, float, float]:
    """Median colour of a rectangle of a rendered page."""
    clip = pymupdf.Rect(rect)
    pix = pixmap
    return pix


def _sample(page_image, point) -> tuple[int, int, int]:
    pix = page_image
    x, y = int(point[0]), int(point[1])
    index = (y * pix.width + x) * pix.n
    return tuple(pix.samples[index: index + 3])


def test_full_page_image_marks_a_scan(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 300), 0)
    pix.clear_with(200)
    page.insert_image(page.rect, pixmap=pix)
    assert _has_page_image(page)
    go = doc.new_page(width=400, height=600)
    small = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), 0)
    small.clear_with(90)
    go.insert_image(pymupdf.Rect(50, 50, 120, 120), pixmap=small)
    assert not _has_page_image(go)
    doc.close()


def test_block_on_small_artwork_is_detected(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    small = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60), 0)
    small.clear_with(20)
    page.insert_image(pymupdf.Rect(100, 100, 200, 200), pixmap=small)
    assert _block_on_artwork(page, pymupdf.Rect(110, 120, 190, 150))
    assert not _block_on_artwork(page, pymupdf.Rect(110, 400, 190, 430))
    doc.close()


def _scan_like(path, text="Hello world, a sentence printed on a scanned page."):
    """A page image with printed-looking text above it, like a real scan."""
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=600)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 210, 300), 0)
    pix.clear_with(217)  # cream paper
    page.insert_image(page.rect, pixmap=pix)
    page.insert_text((60, 120), text, fontsize=11)
    doc.save(path)
    doc.close()
    return path


def test_scanned_text_is_replaced_not_left_underneath(tmp_path):
    source = _scan_like(tmp_path / "scan.pdf")
    out = tmp_path / "out.pdf"
    _, stats = translate_pdf_optimized(
        str(source), _Stub(), "fa", str(out), 1, enable_ocr=False,
        translation_workers=1, return_stats=True, use_cache=False, use_checkpoint=False,
    )
    assert stats.translated_blocks >= 1
    with pymupdf.open(out) as doc:
        text = unicodedata.normalize("NFKC", doc[0].get_text())
    assert "Hello world" not in text
    # The text layer stores shaped glyphs, so the letters are checked by script.
    assert len([c for c in text if "\u0600" <= c <= "\u06ff"]) > 20


def test_fill_matches_the_paper_instead_of_white(tmp_path):
    source = _scan_like(tmp_path / "scan.pdf")
    out = tmp_path / "out.pdf"
    translate_pdf_optimized(
        str(source), _Stub(), "fa", str(out), 1, enable_ocr=False,
        translation_workers=1, use_cache=False, use_checkpoint=False,
        match_background=True,
    )
    with pymupdf.open(out) as doc:
        pix = doc[0].get_pixmap(dpi=72)
        # A point inside the covered box but away from the glyphs.
        sample = _sample(pix, (pix.width - 20, int(86 * 72 / 72)))
    assert min(sample) < 245, f"box was filled white: {sample}"


def test_artwork_blocks_stay_untouched(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=600)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 210, 300), 0)
    pix.clear_with(217)
    page.insert_image(page.rect, pixmap=pix)
    art = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 80), 0)
    art.clear_with(40)
    page.insert_image(pymupdf.Rect(80, 200, 260, 380), pixmap=art)
    page.insert_text((100, 260), "A caption printed over the drawing.", fontsize=10)
    source = tmp_path / "art.pdf"
    doc.save(source)
    doc.close()

    out = tmp_path / "out.pdf"
    _, stats = translate_pdf_optimized(
        str(source), _Stub(), "fa", str(out), 1, enable_ocr=False,
        translation_workers=1, return_stats=True, use_cache=False,
        use_checkpoint=False, skip_text_on_image=True,
    )
    assert stats.skipped_artwork >= 1
    with pymupdf.open(out) as doc:
        assert "caption printed over the drawing" in doc[0].get_text()
