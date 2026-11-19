"""End-to-end layout tests on real and synthetic PDFs.

These exercise the part of the OCR pipeline that decides *where* text lands:
raster detection, background fill, box growth, reading order and redaction.  No
network and no OCR model is needed - blocks are fed through the exact code path
used for real OCR results (``precomputed_ocr`` / native text), and the output
pixels are measured against the source page.
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

from trarium.pdf_optimized import extract_text_blocks, translate_pdf_optimized
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
    # insert_htmlbox stores Arabic presentation forms; NFKC maps them back so
    # the checks measure the text a reader actually sees.
    return unicodedata.normalize("NFKC", text or "")


def _persian_runs(text: str) -> int:
    return len(PERSIAN_RUN.findall(_normalized(text)))


def _persian_chars(text: str) -> int:
    return len(re.findall(r"[\u0600-\u06FF]", _normalized(text)))


def _cover_rects(page, exclude=()):
    """Filled rectangles added by the writer, ignoring pre-existing artwork."""
    known = {tuple(round(v, 2) for v in rect) for rect in exclude}
    rects = []
    for drawing in page.get_drawings():
        if drawing.get("fill") is None:
            continue
        rect = drawing["rect"]
        key = tuple(round(v, 2) for v in (rect.x0, rect.y0, rect.x1, rect.y1))
        if key in known:
            continue
        rects.append(rect)
    return rects


def _fills(page):
    return [tuple(round(float(c), 3) for c in drawing.get("fill") or ())
            for drawing in page.get_drawings() if drawing.get("fill") is not None]


def _beige_scan(path: Path, with_vector_text=True) -> list[tuple[float, float, float, float]]:
    """A scanned page: printed glyph pixels baked into an image + a text layer."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=560)
    image = Image.new("RGB", (800, 1120), (246, 239, 219))
    draw = ImageDraw.Draw(image)
    rows = [(60, 120, 300), (60, 150, 280), (60, 200, 320), (60, 230, 260)]
    boxes = []
    for x, y, width in rows:
        for step in range(0, width, 12):
            draw.rectangle([x * 2 + step * 2, y * 2, x * 2 + step * 2 + 14, y * 2 + 11],
                           fill=(45, 42, 38))
        boxes.append((x, y - 2, x + width, y + 14))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    page.insert_image(page.rect, stream=buffer.getvalue())
    if with_vector_text:
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


class LayoutWriterTests(unittest.TestCase):
    def test_baked_scan_gets_page_coloured_cover_not_white(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "scan.pdf", Path(td) / "scan_fa.pdf"
            _beige_scan(src)
            _, stats = translate_pdf_optimized(str(src), MarkerTranslator(), "fa", str(dst),
                                               use_cache=False, use_checkpoint=False,
                                               return_stats=True, translation_workers=1)
            self.assertGreater(stats.translated_blocks, 0)
            self.assertEqual(stats.write_failures, 0)
            # Every written block sat on a page image, so every block got a cover.
            self.assertEqual(stats.covered_blocks, stats.translated_blocks)

            source_doc = pymupdf.open(str(src))
            output_doc = pymupdf.open(str(dst))
            try:
                fills = _fills(output_doc[0])
                self.assertTrue(fills, "a raster-backed page must be covered")
                for fill in fills:
                    self.assertGreater(len(fill), 2)
                    self.assertLess(max(fill), 0.999, f"pure white cover painted: {fill}")
                    # The cream page colour must be sampled, not assumed.
                    self.assertGreater(fill[0], 0.85)
                    self.assertGreater(fill[0], fill[2])
                before, after = _render(source_doc[0]), _render(output_doc[0])
                added_white = ((after >= 248).all(axis=2) & ~(before >= 248).all(axis=2)).mean()
                self.assertLess(added_white, 0.002, "white rectangles were introduced")
                self.assertNotIn("Printed line one", output_doc[0].get_text())
                self.assertGreater(_persian_runs(output_doc[0].get_text()), 0)
            finally:
                source_doc.close()
                output_doc.close()

    def test_art_under_a_caption_is_not_painted_over(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "photo.pdf", Path(td) / "photo_fa.pdf"
            _dark_photo(src)
            _, stats = translate_pdf_optimized(str(src), MarkerTranslator(), "fa", str(dst),
                                               use_cache=False, use_checkpoint=False,
                                               return_stats=True, translation_workers=1)
            self.assertEqual(stats.write_failures, 0)
            self.assertEqual(stats.covered_blocks, 0, "a dark photograph must not be covered")
            output_doc = pymupdf.open(str(dst))
            try:
                self.assertFalse(_fills(output_doc[0]), "no rectangle should be drawn on the photo")
                self.assertNotIn("Caption on the photo", output_doc[0].get_text())
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
                # Both translations survive: the second block's box may not
                # swallow the first block's freshly written text (one block is
                # about 22 Persian characters, so two blocks leave ~44).
                self.assertGreater(_persian_chars(text), 30)
                covers = _cover_rects(output_doc[0])
                for i in range(len(covers)):
                    for j in range(i + 1, len(covers)):
                        overlap = covers[i] & covers[j]
                        area = 0.0 if overlap.is_empty else overlap.get_area()
                        self.assertLessEqual(area, 1.0, "covers overlap and destroy each other")
            finally:
                output_doc.close()

    def test_manga_bubbles_keep_the_artwork_between_them(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "manga.pdf", Path(td) / "manga_fa.pdf"
            bubbles = _manga_page(src)
            blocks = []
            for index, (x0, y0, x1, y1) in enumerate(bubbles):
                # OCR-shaped blocks: the bubble interior, not the whole panel.
                blocks.append({
                    "bbox": (x0 + 12, y0 + 18, x1 - 12, y1 - 18),
                    "text": f"Dialogue line number {index + 1} inside the bubble",
                    "font_size": 11,
                })
            _, stats = translate_pdf_optimized(str(src), MarkerTranslator(), "fa", str(dst),
                                               enable_ocr=True, precomputed_ocr={0: blocks},
                                               use_cache=False, use_checkpoint=False,
                                               return_stats=True, translation_workers=1)
            self.assertEqual(stats.write_failures, 0)
            source_doc = pymupdf.open(str(src))
            output_doc = pymupdf.open(str(dst))
            try:
                existing = [(d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1)
                            for d in source_doc[0].get_drawings() if d.get("fill") is not None]
                covers = _cover_rects(output_doc[0], exclude=existing)
                self.assertTrue(covers, "each bubble must be covered")
                for bubble in bubbles:
                    self.assertTrue(any(c.intersects(pymupdf.Rect(*bubble)) for c in covers),
                                    f"bubble {bubble} was not covered")
                for drawing in output_doc[0].get_drawings():
                    fill = drawing.get("fill")
                    if fill is None:
                        continue
                    rect = drawing["rect"]
                    if not any(abs(rect.x0 - c.x0) < 0.6 and abs(rect.y0 - c.y0) < 0.6
                               and abs(rect.x1 - c.x1) < 0.6 and abs(rect.y1 - c.y1) < 0.6
                               for c in covers):
                        continue  # the bubble artwork itself
                    self.assertGreater(min(fill), 0.9, "bubble interior must be white")
                for cover in covers:
                    inside = any(
                        cover.x0 >= x0 - 2 and cover.y0 >= y0 - 2
                        and cover.x1 <= x1 + 2 and cover.y1 <= y1 + 2
                        for (x0, y0, x1, y1) in bubbles
                    )
                    self.assertTrue(inside, f"cover spilled outside its bubble: {cover}")
                before, after = _render(source_doc[0]), _render(output_doc[0])
                artwork = slice(300, before.shape[0])  # everything below the bubbles
                difference = np.abs(after[artwork] - before[artwork]).mean()
                self.assertLess(difference, 0.5, "the artwork below the bubbles changed")
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
            self.assertGreater(stats.translated_blocks, 25)

            source_doc = pymupdf.open(str(source))
            output_doc = pymupdf.open(str(dst))
            try:
                before, after = _render(source_doc[0]), _render(output_doc[0])
                added_white = ((after >= 248).all(axis=2) & ~(before >= 248).all(axis=2)).mean()
                self.assertLess(added_white, 0.003, "page gained white rectangles")
                self.assertNotIn("CONTENTS", output_doc[0].get_text())

                source_boxes = [pymupdf.Rect(b["bbox"]) for b in extract_text_blocks(source_doc[0])]
                for drawing in output_doc[0].get_drawings():
                    if drawing.get("fill") is None:
                        continue
                    cover = pymupdf.Rect(drawing["rect"])
                    matched = any(
                        cover.x0 >= b.x0 - 2 and cover.y0 >= b.y0 - 2
                        and cover.x1 <= b.x1 + 2 and cover.y1 <= b.y1 + 2
                        for b in source_boxes)
                    self.assertTrue(matched, f"cover does not match any source text box: {cover}")
            finally:
                source_doc.close()
                output_doc.close()


if __name__ == "__main__":
    unittest.main()
