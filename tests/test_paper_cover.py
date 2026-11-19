"""A scan's text box over a picture is left alone; one on paper is covered."""
import pymupdf

from trarium.pdf_optimized import _is_plain_paper


def _page_with_art():
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    # White paper with a line of text on the left half.
    page.insert_text((20, 60), "Plain text on paper", fontsize=12)
    # A colourful "illustration" on the right half: horizontal colour bands.
    colours = [(1, 0, 0), (0, 0.6, 1), (1, 0.85, 0.2), (0.2, 0.2, 0.2), (0.9, 0.5, 0.7)]
    for i in range(40):
        page.draw_rect(pymupdf.Rect(200, 40 + i * 8, 400, 48 + i * 8), color=None,
                       fill=colours[i % len(colours)], width=0)
    return doc, page


def test_text_on_paper_is_plain():
    doc, page = _page_with_art()
    assert _is_plain_paper(page, pymupdf.Rect(18, 48, 140, 64))
    doc.close()


def test_box_over_artwork_is_not_plain():
    doc, page = _page_with_art()
    assert not _is_plain_paper(page, pymupdf.Rect(220, 60, 380, 300))
    doc.close()
