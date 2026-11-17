"""Regression tests for automatic cover and decorative page detection.

The rules under test are the promises the feature makes:

* the cover of a book or a comic, a title page, a full-page plate and an
  advertisement are found and left out of the translation;
* a page with a picture but a story on it is still translated — an illustration
  does not make a comic page a cover;
* when the evidence is thin the page is translated and only reported;
* a page's status can always be overridden by hand;
* a page that is exempt is never rewritten: its text and its picture are
  byte-for-byte the ones of the source file.
"""
from __future__ import annotations

import pymupdf
import pytest

from trarium.page_detector import (
    AD_PAGE,
    BOOK_COVER,
    ILLUSTRATION_PAGE,
    MANGA_COVER,
    NORMAL_PAGE,
    TITLE_PAGE,
    UNCERTAIN,
    CoverDetectionConfig,
    classify_features,
    detect_pages,
    parse_page_status,
    parse_vision_answer,
    summary,
)
from trarium.pdf_optimized import translate_pdf_optimized


def features(**overrides):
    """A quiet page of prose; every test changes only what it is about."""
    base = {
        "words": 120,
        "lines": 12,
        "chars": 900,
        "avg_line_len": 75.0,
        "long_lines": 10,
        "centered_ratio": 0.0,
        "max_font_ratio": 0.02,
        "image_coverage": 0.0,
        "image_count": 0,
        "size_deviation": 0.0,
        "edge_page": False,
        "front_matter": False,
        "has_text_layer": True,
        "doc_comic": False,
        "linkish": False,
        "colorfulness": 0.0,
        "dark_ratio": 0.0,
        "ink_bands": 0,
        "max_band_frac": 0.0,
    }
    base.update(overrides)
    return base


def page(**overrides):
    """A cover: full-page picture, one short centred display line, first page."""
    base = {
        "words": 4, "lines": 1, "chars": 18, "avg_line_len": 18.0, "long_lines": 0,
        "centered_ratio": 1.0, "max_font_ratio": 0.06, "image_coverage": 1.0,
        "image_count": 1, "edge_page": True, "front_matter": True,
    }
    base.update(overrides)
    return features(**base)


class TestStructure:
    def test_prose_page_is_translated(self):
        status, confidence, _ = classify_features(features())
        assert status == NORMAL_PAGE
        assert confidence >= 0.9

    def test_a_picture_does_not_turn_a_story_page_into_a_cover(self):
        # A manga page: panels everywhere and a dozen lines of dialogue.
        status, _, _ = classify_features(features(
            image_coverage=0.85, words=70, lines=9, avg_line_len=26.0,
            long_lines=1, centered_ratio=0.3,
        ))
        assert status == NORMAL_PAGE

    def test_a_story_page_with_a_full_page_illustration_is_translated(self):
        status, _, _ = classify_features(features(
            image_coverage=1.0, words=45, lines=5, avg_line_len=50.0, long_lines=3,
        ))
        assert status == NORMAL_PAGE

    def test_cover_of_a_book(self):
        status, confidence, reasons = classify_features(page())
        assert status == BOOK_COVER
        assert confidence >= 0.72
        assert reasons

    def test_cover_of_a_comic(self):
        status, _, _ = classify_features(page(doc_comic=True))
        assert status == MANGA_COVER

    def test_cover_in_the_middle_of_the_file(self):
        # Same page, but the file's usual size, no position bonus: the picture,
        # the display line and the centring still add up to a cover.
        status, confidence, _ = classify_features(page(
            edge_page=False, front_matter=False, max_font_ratio=0.05,
        ))
        assert status == BOOK_COVER
        assert confidence >= 0.72

    def test_title_page_without_illustration(self):
        status, confidence, _ = classify_features(features(
            words=8, lines=3, avg_line_len=14.0, long_lines=0,
            centered_ratio=1.0, max_font_ratio=0.045, image_coverage=0.05,
        ))
        assert status == TITLE_PAGE
        assert confidence >= 0.72

    def test_wordless_plate_inside_the_book(self):
        status, _, _ = classify_features(features(
            words=0, lines=0, chars=0, avg_line_len=0.0, long_lines=0,
            image_coverage=0.95, has_text_layer=True,
        ))
        assert status == ILLUSTRATION_PAGE

    def test_wordless_page_without_a_text_layer_stays_translatable(self):
        # A scanned comic page: its dialogue is in the picture, so OCR must run.
        status, _, reasons = classify_features(features(
            words=0, lines=0, chars=0, avg_line_len=0.0, long_lines=0,
            image_coverage=0.95, has_text_layer=False,
        ))
        assert status == NORMAL_PAGE
        assert reasons

    def test_advertisement_page(self):
        status, confidence, _ = classify_features(features(
            words=28, lines=6, avg_line_len=20.0, long_lines=0,
            linkish=True, image_coverage=0.2,
        ))
        assert status == AD_PAGE
        assert confidence >= 0.72

    def test_thin_evidence_is_reported_but_translated(self):
        # A quote page: a picture, four centred lines, nothing else.
        status, confidence, reasons = classify_features(page(
            words=12, lines=3, avg_line_len=12.0, max_font_ratio=0.025,
            centered_ratio=0.67, edge_page=False, front_matter=False,
            image_coverage=0.9,
        ))
        assert status == UNCERTAIN
        assert confidence < 0.72
        assert reasons

    def test_a_high_threshold_turns_a_cover_into_a_checked_page(self):
        config = CoverDetectionConfig(threshold=0.95)
        status, confidence, reasons = classify_features(page(), config)
        assert status == UNCERTAIN
        assert confidence < 0.95
        assert reasons

    def test_disabled_detection_translates_everything(self):
        status, _, _ = classify_features(page(), CoverDetectionConfig(enabled=False))
        assert status == NORMAL_PAGE

    def test_manual_override_wins(self):
        assert CoverDetectionConfig(manual={0: MANGA_COVER}).override(0) == MANGA_COVER
        assert CoverDetectionConfig(manual={"0": "normal_page"}).override(0) == NORMAL_PAGE
        assert CoverDetectionConfig(manual={0: "NOT_A_STATUS"}).override(0) is None


class TestHelpers:
    def test_parse_page_status(self):
        assert parse_page_status("1=MANGA_COVER, 4=NORMAL_PAGE") == {0: MANGA_COVER, 3: NORMAL_PAGE}
        assert parse_page_status("2=BOOK_COVER,bad,0=AD_PAGE,3=nope") == {1: BOOK_COVER}
        assert parse_page_status(None) == {}

    def test_parse_vision_answer(self):
        answer = parse_vision_answer('```json\n{"status": "MANGA_COVER", "confidence": 0.9, "reason": "x"}\n```')
        assert answer and answer[0] == MANGA_COVER and answer[1] == 0.9
        assert parse_vision_answer("I think it is a cover") is None
        assert parse_vision_answer('{"status": "SOMETHING"}') is None

    def test_summary_counts(self):
        decisions = [
            pymupdf.open().new_page() for _ in range(0)
        ] and []
        from trarium.page_detector import PageDecision

        decisions = [
            PageDecision(0, BOOK_COVER, 0.9),
            PageDecision(1, NORMAL_PAGE, 0.9),
            PageDecision(2, UNCERTAIN, 0.5),
            PageDecision(3, AD_PAGE, 0.8),
        ]
        counts = summary(decisions)
        assert counts["pages_total"] == 4
        assert counts["translated_pages"] == 2
        assert counts["exempt_pages"] == [1, 4]
        assert counts["book_covers"] == 1
        assert counts["ad_pages"] == 1
        assert counts["uncertain_pages"] == 1


# ---------------------------------------------------------------------------
# A real PDF: the cover must come out of the pipeline untouched
# ---------------------------------------------------------------------------
def _picture(path):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 48, 48))
    pix.clear_with(190)
    pix.save(str(path))
    return str(path)


def _book(path, picture):
    doc = pymupdf.open()
    cover = doc.new_page(width=420, height=595)
    cover.insert_image(cover.rect, filename=picture)
    cover.insert_text((110, 90), "THE BOOK", fontsize=40)
    cover.insert_text((150, 540), "By A. Writer", fontsize=14)

    body = doc.new_page(width=420, height=595)
    paragraph = ("The knight came to the fisherman and asked for a light. " * 6).strip()
    body.insert_textbox(pymupdf.Rect(50, 60, 370, 500), paragraph, fontsize=11)

    ad = doc.new_page(width=420, height=595)
    for index, line in enumerate([
        "More books from this publisher",
        "Read the next volume today",
        "https://example.com/store",
        "Follow us on instagram",
    ]):
        ad.insert_text((70, 90 + index * 24), line, fontsize=11)
    doc.save(str(path))
    doc.close()
    return str(path)


class Stub:
    provider = "stub"
    model = "stub-1"
    last_usage: dict = {}

    def __init__(self):
        self.calls = 0

    def translate(self, text: str, target_lang: str = "fa") -> str:
        self.calls += 1
        return "شوالیه به ماهیگیر رسید و از او نور خواست و کتاب‌ها را برداشت."


def test_detect_pages_on_a_real_pdf(tmp_path):
    document = _book(tmp_path / "book.pdf", _picture(tmp_path / "art.png"))
    with pymupdf.open(document) as doc:
        decisions = detect_pages(doc)
    assert [d.status for d in decisions] == [BOOK_COVER, NORMAL_PAGE, AD_PAGE]
    assert decisions[0].confidence >= 0.72


def test_untouched_pages_come_out_of_the_pipeline_byte_identical(tmp_path):
    source = _book(tmp_path / "book.pdf", _picture(tmp_path / "art.png"))
    output = tmp_path / "book_fa.pdf"
    _, stats = translate_pdf_optimized(
        source, Stub(), target_lang="fa", output_path=str(output),
        translation_workers=1, return_stats=True, translation_qc=False,
        cover_detection=True, vision_api_key=None,
    )
    assert stats.page_status_counts["book_covers"] == 1
    assert stats.page_status_counts["ad_pages"] == 1
    assert 1 in stats.detected_exempt_pages and 3 in stats.detected_exempt_pages

    with pymupdf.open(source) as before, pymupdf.open(output) as after:
        # The cover and the advertisement are the source pages, unchanged.
        assert before[0].get_text() == after[0].get_text()
        assert before[2].get_text() == after[2].get_text()
        assert before[0].rect == after[0].rect
        for index in (0, 2):
            assert before[index].get_pixmap(dpi=60).samples == after[index].get_pixmap(dpi=60).samples
        # The story page is translated (glyphs are stored in their shaped form).
        story = after[1].get_text()
        assert len([c for c in story if "\u0600" <= c <= "\u06ff"]) >= 10
        assert "The knight came" not in story


def test_cover_detection_can_be_turned_off(tmp_path):
    source = _book(tmp_path / "book.pdf", _picture(tmp_path / "art.png"))
    output = tmp_path / "book_fa.pdf"
    _, stats = translate_pdf_optimized(
        source, Stub(), target_lang="fa", output_path=str(output),
        translation_workers=1, return_stats=True, translation_qc=False,
        cover_detection=False,
    )
    assert stats.page_status_counts == {}
    assert stats.detected_exempt_pages == []


def test_manual_status_forces_a_cover_to_be_translated(tmp_path):
    source = _book(tmp_path / "book.pdf", _picture(tmp_path / "art.png"))
    output = tmp_path / "book_fa.pdf"
    _, stats = translate_pdf_optimized(
        source, Stub(), target_lang="fa", output_path=str(output),
        translation_workers=1, return_stats=True, translation_qc=False,
        cover_detection=True, manual_page_status={0: NORMAL_PAGE},
    )
    assert 1 not in stats.detected_exempt_pages
    with pymupdf.open(output) as after:
        assert len([c for c in after[0].get_text() if "\u0600" <= c <= "\u06ff"]) > 20
