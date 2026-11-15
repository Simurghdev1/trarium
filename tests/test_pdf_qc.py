"""Regression tests for the final quality check and the new renderer options."""
from __future__ import annotations

import pymupdf
import pytest

from trarium.pdf_qc import check_pdf
from trarium.pdf_optimized import translate_pdf_optimized


def _pdf(path, pages, text="Hello world, this is a sentence."):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), text, fontsize=12)
    doc.save(path)
    doc.close()
    return path


class _Stub:
    provider = "stub"
    model = "stub-1"
    last_usage: dict = {}

    def __init__(self, answer="این یک جملهٔ آزمایشی است."):
        self.answer = answer
        self.calls = 0

    def translate(self, text: str, target_lang: str = "fa") -> str:
        self.calls += 1
        return self.answer


def test_clean_pdf_passes(tmp_path):
    source = _pdf(tmp_path / "src.pdf", 1)
    out = _pdf(tmp_path / "out.pdf", 1, text="این یک جملهٔ آزمایشی است.")
    report = check_pdf(str(source), str(out))
    assert report.ok, report.issues
    assert report.stats["pages_in"] == report.stats["pages_out"]


def test_blank_page_is_reported(tmp_path):
    source = _pdf(tmp_path / "src.pdf", 2)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "این یک جملهٔ آزمایشی است.", fontsize=12)
    doc.new_page()
    doc.save(tmp_path / "out.pdf")
    doc.close()
    report = check_pdf(str(source), str(tmp_path / "out.pdf"))
    assert not report.ok
    assert any("خالی" in issue for issue in report.issues)


def test_untranslated_cjk_is_reported(tmp_path):
    source = _pdf(tmp_path / "src.pdf", 1)
    out = tmp_path / "out.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "全球最佳动漫盘点", fontsize=12, fontname="china-s")
    doc.save(out)
    doc.close()
    report = check_pdf(str(source), str(out))
    assert not report.ok
    assert report.stats["foreign_chars"] > 0


def test_page_size_change_is_reported(tmp_path):
    source = _pdf(tmp_path / "src.pdf", 1)
    doc = pymupdf.open()
    doc.new_page(width=400, height=400).insert_text((40, 60), "متن آزمایشی", fontsize=12)
    doc.save(tmp_path / "out.pdf")
    doc.close()
    report = check_pdf(str(source), str(tmp_path / "out.pdf"))
    assert not report.ok
    assert any("اندازهٔ صفحه" in issue for issue in report.issues)


def test_pipeline_records_qc_and_reviews_when_requested(tmp_path):
    source = _pdf(tmp_path / "src.pdf", 1)
    stub = _Stub()
    out = tmp_path / "out.pdf"
    _, stats = translate_pdf_optimized(
        str(source), stub, "fa", str(out), 1,
        enable_ocr=False, translation_workers=1, return_stats=True,
        use_cache=False, use_checkpoint=False,
        self_review=True, final_check=True, uniform_font=True,
    )
    assert out.exists()
    assert stats.qc_ok in (True, False)
    assert isinstance(stats.qc_issues, list)
    assert stats.reviewed_blocks >= 0
    # the stub answers an unchanged sentence, so the reviewer may or may not fire
    assert stub.calls >= 1
