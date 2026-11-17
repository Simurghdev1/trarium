"""The pictures of a translated page must survive every viewer."""
from __future__ import annotations

import io
import pathlib
import sys

import pymupdf
import pytest
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from trarium.pdf_images import normalize_jpx_images  # noqa: E402


def _jp2(tmp_path: pathlib.Path, size=(900, 600)) -> pathlib.Path:
    """A JPEG 2000 picture, the format scanned books are stored in."""
    width, height = size
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (200 - x // 12, 190 - y // 20, 150)
    path = tmp_path / "plate.jp2"
    image.save(path, "JPEG2000")
    return path


def _pdf_with_jpx(tmp_path: pathlib.Path) -> tuple[pymupdf.Document, pathlib.Path]:
    source = _jp2(tmp_path)
    doc = pymupdf.open()
    page = doc.new_page(width=500, height=700)
    page.insert_image(pymupdf.Rect(20, 20, 480, 320), filename=str(source))
    return doc, source


def _jpx_count(doc: pymupdf.Document) -> int:
    total = 0
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Subtype")[1] != "/Image":
            continue
        if "JPXDecode" in str(doc.xref_get_key(xref, "Filter")[1]):
            total += 1
    return total


def test_jpx_images_are_reencoded_for_every_viewer(tmp_path):
    doc, _ = _pdf_with_jpx(tmp_path)
    assert _jpx_count(doc) == 1, "the fixture should start as JPEG 2000"
    before_rect = doc[0].get_image_bbox(doc[0].get_images(full=True)[0])

    assert normalize_jpx_images(doc) == 1
    assert _jpx_count(doc) == 0, "no JPEG 2000 may be left for a viewer to skip"

    # The picture itself, its size and its place are untouched.
    images = doc[0].get_images(full=True)
    assert len(images) == 1
    info = doc.extract_image(images[0][0])
    assert (info["width"], info["height"]) == (900, 600)
    rect = doc[0].get_image_bbox(images[0])
    assert abs(rect.width - before_rect.width) < 0.5
    assert abs(rect.height - before_rect.height) < 0.5
    assert abs(rect.x0 - before_rect.x0) < 0.5 and abs(rect.y0 - before_rect.y0) < 0.5

    # And it still draws: the page is not empty where the image sits.
    pix = doc[0].get_pixmap(clip=rect, dpi=72)
    colours = {pix.pixel(x, y) for x in range(0, pix.width, 17) for y in range(0, pix.height, 17)}
    assert len(colours) > 10, "a re-encoded picture must keep its detail"
    doc.close()


def test_colour_is_kept_within_a_jpeg_tolerance(tmp_path):
    doc, _ = _pdf_with_jpx(tmp_path)
    before = doc[0].get_pixmap(dpi=50)
    normalize_jpx_images(doc, quality=95)
    after = doc[0].get_pixmap(dpi=50)
    assert before.width == after.width and before.height == after.height

    diff = 0
    total = before.width * before.height
    for i in range(0, total * before.n, before.n * 50):
        diff += abs(before.samples[i] - after.samples[i])
    assert diff / (total / 50) < 6, "re-encoding must not visibly change the paper"
    doc.close()


def test_a_pdf_without_jpx_is_left_alone(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "plain text page")
    assert normalize_jpx_images(doc) == 0
    assert _jpx_count(doc) == 0
    doc.close()
