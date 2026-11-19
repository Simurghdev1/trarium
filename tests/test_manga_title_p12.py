"""Josep Moore p12: a three-line printed title above cover artwork."""
import pymupdf

from trarium.ocr_space import merge_lines
from trarium.pdf_optimized import _centred_growth

# OCR.space line boxes for p12 (PDF points), padded the way the API pads them.
P12 = [
    ((171, 85, 424, 122), "THE VAMPIRE WHO"),
    ((177, 107, 417, 135), "COULDN'T CHARM"),
    ((193, 138, 402, 162), "THE GONDOLIER"),
    ((101, 196, 333, 271), "THE VAMPIRE"),
    ((97, 262, 302, 326), "WHO COULDN'"),
    ((106, 314, 334, 402), "CHARM THE"),
]


def _blocks():
    return merge_lines([{"bbox": b, "text": t, "font_size": (b[3] - b[1]) * 0.78} for b, t in P12])


def test_overlapping_title_lines_become_one_block():
    title = _blocks()[0]
    assert title["text"] == "THE VAMPIRE WHO COULDN'T CHARM THE GONDOLIER"
    assert len(title["lines"]) == 3


def test_title_is_not_glued_to_artwork_lettering():
    blocks = _blocks()
    assert len(blocks) == 2
    assert blocks[1]["text"].startswith("THE VAMPIRE WHO COULDN'")
    assert blocks[1]["bbox"][1] >= 196


def test_grown_box_stays_on_the_source_text():
    cover = pymupdf.Rect(171, 85, 424, 162)
    grown = pymupdf.Rect(171, 0, 424, 170)  # blank paper all the way to the top
    out = _centred_growth(cover, grown)
    assert out.y1 == 170
    assert out.y0 >= cover.y0 - 0.25 * cover.height - 0.01
    assert out.contains(cover)


def test_two_line_title_does_not_swallow_cover_lettering():
    # p5: printed title, then same-size lettering on the cover art 25pt lower.
    lines = [((150, 60, 420, 82), "OFFICE ROMANCE"), ((140, 78, 430, 100), "WITH MY ARCHENEMY"),
             ((110, 128, 470, 152), "OFFICE ROMANCE"), ((105, 152, 475, 172), "WITH MY ARCHENEMY")]
    blocks = merge_lines([{"bbox": b, "text": t, "font_size": 16} for b, t in lines])
    assert blocks[0]["text"] == "OFFICE ROMANCE WITH MY ARCHENEMY"
    assert blocks[0]["bbox"][3] <= 100
