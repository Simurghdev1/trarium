"""End-to-end layout tests on real and synthetic PDFs.

These exercise the part of the OCR pipeline that decides *where* text lands and
how the source glyphs are removed: raster detection, pixel repair, box growth,
reading order and redaction.  No network and no OCR model is needed - blocks are
fed through the exact code path used for real OCR results (``precomputed_ocr`` /
native text), and the output pixels are measured against the source page.
"""
import io
import re
import tempfile
import unicodedata
import unittest
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw

from trarium.pdf_optimized import (_patch_raster_glyphs, extract_text_blocks,
                                   translate_pdf_optimized)
from trarium.translators.base import BaseTranslator

FA_WORDS = "ترجمه آزمایشی چیدمان صفحه"
PERSIAN_RUN = re.compile(r"[\u0600-\u06FF]{2,}")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MarkerTranslator(BaseTranslator):
    """Deterministic translator: answers every marker with Persian text."""

    def __init__(self):
        self.calls = 0

    def get_token_limit(self):
        return 100000

    def translate(self, text, target_lang="fa"):
        self.calls += 1
        markers = re.findall(r"\[TRARIUM_BLOCK_\d+\]", text)
        if markers:
            return "\n".join(f"{marker}\n{FA_WORDS} {index + 1}" for index, marker in enumerate(markers))
        return FA_WORDS


def _render(page, dpi=100) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    return array[:, :, :3].astype(np.int16)


def _normalized(text: str) -> str:
    # insert_htmlbox stores Arabic presentation forms; NFKC maps them back.
    return unicodedata.normalize("NFKC", text or "")


def _persian_runs(text: str) -> int:
    return len(PERSIAN_RUN.findall(_normalized(text)))


def _persian_chars(text: str) -> int:
    return len(re.findall(r"[\u0600-\u06FF]", _normalized(text)))


def _added_fills(page):
    return [tuple(round(float(c), 3) for c in drawing.get("fill") or ())
            for drawing in page.get_drawings() if drawing.get("fill") is not None]


def _patched(doc, boxes) -> tuple[np.ndarray, np.ndarray]:
    """Render a page, run the glyph repair, render again (no translation)."""
    page = doc[0]
    before = _render(page)
    if not _patch_raster_glyphs(page, [tuple(box) for box in boxes]):
        raise AssertionError("glyph repair was not applied")
    return before, _render(page)


def _dark_ratio(array: np.ndarray, box, page, dpi: int) -> float:
    """Share of clearly dark (printed) pixels inside a PDF-space box."""
    scale = array.shape[1] / page.rect.width
    x0, y0 = int(box[0] * scale), int(box[1] * scale)
    x1, y1 = int(box[2] * scale), int(box[3] * scale)
    region = array[max(0, y0):y1, max(0, x0):x1]
    if region.size == 0:
        return 0.0
    return float((region.mean(axis=2) < 100).mean())


def _beige_scan(path: Path) -> list[tuple[float, float, float, float]]:
    """A scanned page: printed glyph pixels baked into an image + a text layer."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=560)
    image = Image.new("RGB", (800, 1120), (246, 239, 219))
    draw = ImageDraw.Draw(image)
    # The printed bars match the text that will be laid over them, as on a real
    # scan where the text layer and the pixels describe the same lines.
    rows = [(60, 120, 132), (60, 150, 126), (60, 200, 140), (60, 230, 120)]
    boxes = []
    for x, y, width in rows:
        for step in range(0, width, 11):
            draw.rectangle([x * 2 + step * 2, y * 2, x * 2 + step * 2 + 7, y * 2 + 11],
                           fill=(45, 42, 38))
        boxes.append((x, y - 2, x + width, y + 14))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    page.insert_image(page.rect, stream=buffer.getvalue())
    page.insert_text((60, 131), "Printed line one here", fontsize=12)
    page.insert_text((60, 161), "Printed line two here", fontsize=12)
    page.insert_text((60, 211), "Another paragraph line", fontsize=12)
    page.insert_text((60, 241), "Closing sentence here", fontsize=12)
    doc.save(path)
    doc.close()
    return boxes


def _dark_photo(path: Path) -> None:
    """A dark photograph with a vector caption on top: art must survive."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=560)
    image = Image.new("RGB", (400, 560), (58, 52, 44))
    draw = ImageDraw.Draw(image)
    for y in range(0, 560, 24):
        draw.rectangle([0, y, 400, y + 10], fill=(88, 74, 52))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    page.insert_image(page.rect, stream=buffer.getvalue())
    page.insert_text((60, 260), "Caption on the photo", fontsize=12)
    doc.save(path)
    doc.close()


def _manga_page(path: Path):
    """White speech bubbles over grey artwork."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=560)
    art = Image.new("RGB", (400, 560), (235, 235, 235))
    draw = ImageDraw.Draw(art)
    for i in range(14):
        y = 300 + i * 18
        draw.line([(20, y), (380, y + 10)], fill=(40, 40, 40), width=3)
    draw.rectangle([40, 380, 200, 520], fill=(120, 120, 120))
    buffer = io.BytesIO()
    art.save(buffer, "PNG")
    page.insert_image(page.rect, stream=buffer.getvalue())
    bubbles = [(40, 60, 190, 170), (210, 60, 360, 170)]
    for rect in bubbles:
        page.draw_oval(pymupdf.Rect(*rect), color=(0, 0, 0), fill=(1, 1, 1), width=1.5)
    doc.save(path)
    doc.close()
    return bubbles


def _art_with_text(path: Path):
    """Colour artwork with dark title text printed directly on it."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=560)
    art = Image.new("RGB", (400, 560), (252, 226, 168))
    draw = ImageDraw.Draw(art)
    for i in range(0, 560, 8):
        draw.rectangle([0, i, 400, i + 4], fill=(246, 214 + i // 40, 150))
    draw.ellipse([220, 260, 380, 430], fill=(230, 130, 96))
    for step in range(0, 240, 11):
        draw.rectangle([70 + step, 150, 70 + step + 7, 172], fill=(15, 15, 20))
        draw.rectangle([70 + step, 180, 70 + step + 7, 202], fill=(15, 15, 20))
    buffer = io.BytesIO()
    art.save(buffer, "PNG")
    page.insert_image(page.rect, stream=buffer.getvalue())
    doc.save(path)
    doc.close()
    return (68, 146, 200, 206)


class LayoutWriterTests(unittest.TestCase):
    def test_printed_scan_is_repaired_without_any_rectangle(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "scan.pdf", Path(td) / "scan_fa.pdf"
            boxes = _beige_scan(src)
            _, stats = translate_pdf_optimized(str(src), MarkerTranslator(), "fa", str(dst),
                                               use_cache=False, use_checkpoint=False,
                                               return_stats=True, translation_workers=1)
            self.assertGreater(stats.translated_blocks, 0)
            self.assertEqual(stats.write_failures, 0)
            self.assertEqual(stats.covered_blocks, stats.translated_blocks)
            self.assertEqual(stats.patched_pages, 1)

            # The repair itself must erase the printed pixels (measured before any
            # Persian text is drawn, otherwise the new glyphs count as "dark").
            repair_doc = pymupdf.open(str(src))
            try:
                clean_before, clean_after = _patched(repair_doc, boxes)
                dark_before = sum(_dark_ratio(clean_before, box, repair_doc[0], 100) for box in boxes)
                dark_after = sum(_dark_ratio(clean_after, box, repair_doc[0], 100) for box in boxes)
                self.assertGreater(dark_before, 0.05)
                self.assertLess(dark_after, dark_before * 0.35,
                                "the printed text is still visible after the repair")
            finally:
                repair_doc.close()

            source_doc = pymupdf.open(str(src))
            output_doc = pymupdf.open(str(dst))
            try:
                # No rectangle is painted on the page at all.
                self.assertFalse(_added_fills(output_doc[0]), "a rectangle was drawn on the page")
                before, after = _render(source_doc[0]), _render(output_doc[0])
                added_white = ((after >= 248).all(axis=2) & ~(before >= 248).all(axis=2)).mean()
                self.assertLess(added_white, 0.002, "white rectangles were introduced")
                # The cream paper is untouched outside the glyph area.
                paper_before = before[40:60, 40:200].mean(axis=(0, 1))
                paper_after = after[40:60, 40:200].mean(axis=(0, 1))
                self.assertLess(float(np.abs(paper_before - paper_after).max()), 3.0)
                self.assertNotIn("Printed line one", output_doc[0].get_text())
                self.assertGreater(_persian_runs(output_doc[0].get_text()), 0)
            finally:
                source_doc.close()
                output_doc.close()

    def test_caption_on_a_dark_photo_is_replaced_without_damaging_the_photo(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "photo.pdf", Path(td) / "photo_fa.pdf"
            _dark_photo(src)
            _, stats = translate_pdf_optimized(str(src), MarkerTranslator(), "fa", str(dst),
                                               use_cache=False, use_checkpoint=False,
                                               return_stats=True, translation_workers=1)
            self.assertEqual(stats.write_failures, 0)
            self.assertEqual(stats.patched_pages, 1)
            source_doc = pymupdf.open(str(src))
            output_doc = pymupdf.open(str(dst))
            try:
                self.assertFalse(_added_fills(output_doc[0]))
                before, after = _render(source_doc[0]), _render(output_doc[0])
                # Only the caption band changes; the photo is re-compressed by the
                # pixel repair, so allow a small uniform difference, not a patch.
                band = before[330:380, 60:250]
                band_after = after[330:380, 60:250]
                self.assertGreater(float(np.abs(band_after - band).max()), 40,
                                   "the English caption was left on the photo")
                photo_before = before[330:520, 20:380].mean(axis=(0, 1))
                photo_after = after[330:520, 20:380].mean(axis=(0, 1))
                self.assertLess(float(np.abs(photo_before - photo_after).max()), 6.0,
                                "the photograph itself was damaged")
                self.assertNotIn("Caption on the photo", output_doc[0].get_text())
                self.assertGreater(_persian_runs(output_doc[0].get_text()), 0)
            finally:
                source_doc.close()
                output_doc.close()

    def test_text_printed_on_artwork_is_erased_without_covering_the_art(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "cover.pdf", Path(td) / "cover_fa.pdf"
            block = _art_with_text(src)
            repair_doc = pymupdf.open(str(src))
            try:
                clean_before, clean_after = _patched(repair_doc, [block])
                dark_before = _dark_ratio(clean_before, block, repair_doc[0], 100)
                dark_after = _dark_ratio(clean_after, block, repair_doc[0], 100)
                self.assertGreater(dark_before, 0.05)
                self.assertLess(dark_after, dark_before * 0.35,
                                "title text printed on the artwork was not erased")
                # The blue artwork away from the title keeps its colour.
                self.assertLess(
                    float(np.abs(clean_after[300:420, 40:120] - clean_before[300:420, 40:120]).max()), 40)
            finally:
                repair_doc.close()

            _, stats = translate_pdf_optimized(str(src), MarkerTranslator(), "fa", str(dst),
                                               enable_ocr=True,
                                               precomputed_ocr={0: [{"bbox": block,
                                                                     "text": "Cover title over art",
                                                                     "font_size": 16}]},
                                               use_cache=False, use_checkpoint=False,
                                               return_stats=True, translation_workers=1)
            self.assertEqual(stats.patched_pages, 1)
            output_doc = pymupdf.open(str(dst))
            try:
                self.assertFalse(_added_fills(output_doc[0]),
                                 "artwork must not be covered by a rectangle")
                self.assertGreater(_persian_runs(output_doc[0].get_text()), 0)
            finally:
                output_doc.close()

    def test_adjacent_blocks_do_not_erase_each_others_translation(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "two.pdf", Path(td) / "two_fa.pdf"
            doc = pymupdf.open()
            page = doc.new_page(width=400, height=560)
            page.insert_text((50, 100), "First paragraph of the page", fontsize=12)
            page.insert_text((50, 156), "Second paragraph of the page", fontsize=12)
            doc.save(src)
            doc.close()
            _, stats = translate_pdf_optimized(str(src), MarkerTranslator(), "fa", str(dst),
                                               use_cache=False, use_checkpoint=False,
                                               return_stats=True, translation_workers=1)
            self.assertEqual(stats.translated_blocks, 2)
            self.assertEqual(stats.write_failures, 0)
            output_doc = pymupdf.open(str(dst))
            try:
                text = output_doc[0].get_text()
                # One translation is ~22 Persian characters, so two survive as ~44.
                self.assertGreater(_persian_chars(text), 30)
            finally:
                output_doc.close()

    def test_manga_bubbles_keep_the_artwork_between_them(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "manga.pdf", Path(td) / "manga_fa.pdf"
            bubbles = _manga_page(src)
            blocks = [{
                "bbox": (x0 + 12, y0 + 18, x1 - 12, y1 - 18),
                "text": f"Dialogue line number {index + 1} inside the bubble",
                "font_size": 11,
            } for index, (x0, y0, x1, y1) in enumerate(bubbles)]
            _, stats = translate_pdf_optimized(str(src), MarkerTranslator(), "fa", str(dst),
                                               enable_ocr=True, precomputed_ocr={0: blocks},
                                               use_cache=False, use_checkpoint=False,
                                               return_stats=True, translation_workers=1)
            self.assertEqual(stats.write_failures, 0)
            self.assertEqual(stats.patched_pages, 1)
            source_doc = pymupdf.open(str(src))
            output_doc = pymupdf.open(str(dst))
            try:
                # The fixture draws the two bubbles itself; the writer must not
                # add any further filled shape on top of the artwork.
                self.assertEqual(_added_fills(output_doc[0]), _added_fills(source_doc[0]),
                                 "a rectangle was painted over the artwork")
                before, after = _render(source_doc[0]), _render(output_doc[0])
                artwork = slice(300, before.shape[0])  # everything below the bubbles
                difference = float(np.abs(after[artwork] - before[artwork]).mean())
                self.assertLess(difference, 4.0, "the artwork below the bubbles changed")
                self.assertGreater(_persian_runs(output_doc[0].get_text()), 0)
            finally:
                source_doc.close()
                output_doc.close()


class RealUndineTests(unittest.TestCase):
    """The reported defect: Persian drawn on top of the printed English."""

    def test_undine_pages_lose_english_and_keep_the_paper(self):
        source = PROJECT_ROOT / "trarium-files" / "Undine_Trarium_Test_6_Pages.pdf"
        if not source.exists():
            self.skipTest("sample PDF not available")
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "undine_fa.pdf"
            _, stats = translate_pdf_optimized(str(source), MarkerTranslator(), "fa", str(dst),
                                               skip_pages={3, 4, 5},
                                               use_cache=False, use_checkpoint=False,
                                               return_stats=True, translation_workers=2)
            self.assertEqual(stats.write_failures, 0)
            self.assertEqual(stats.covered_blocks, stats.translated_blocks)
            self.assertEqual(stats.patched_pages, 3)
            self.assertGreater(stats.translated_blocks, 25)

            # Real page: the printed contents lines must lose their pixels.
            repair_doc = pymupdf.open(str(source))
            try:
                boxes = [tuple(pymupdf.Rect(b["bbox"])) for b in extract_text_blocks(repair_doc[0])]
                self.assertGreater(len(boxes), 10)
                clean_before, clean_after = _patched(repair_doc, boxes)
                dark_before = sum(_dark_ratio(clean_before, b, repair_doc[0], 100) for b in boxes)
                dark_after = sum(_dark_ratio(clean_after, b, repair_doc[0], 100) for b in boxes)
                self.assertGreater(dark_before, 0.1)
                self.assertLess(dark_after, dark_before * 0.3)
            finally:
                repair_doc.close()

            source_doc = pymupdf.open(str(source))
            output_doc = pymupdf.open(str(dst))
            try:
                self.assertFalse(_added_fills(output_doc[0]))
                before, after = _render(source_doc[0]), _render(output_doc[0])
                added_white = ((after >= 248).all(axis=2) & ~(before >= 248).all(axis=2)).mean()
                self.assertLess(added_white, 0.003, "page gained white rectangles")
                self.assertNotIn("CONTENTS", output_doc[0].get_text())
                # Cream paper outside the text column is unchanged.
                paper_before = before[500:560, 40:120].mean(axis=(0, 1))
                paper_after = after[500:560, 40:120].mean(axis=(0, 1))
                self.assertLess(float(np.abs(paper_before - paper_after).max()), 12.0)
                self.assertGreater(_persian_runs(output_doc[0].get_text()), 0)
            finally:
                source_doc.close()
                output_doc.close()


if __name__ == "__main__":
    unittest.main()
