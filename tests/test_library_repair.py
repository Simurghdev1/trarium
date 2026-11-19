"""Tests for the library, repair, conversion, resume, templates and analytics."""
from __future__ import annotations

import hashlib
import time
import zipfile
from pathlib import Path

import pytest

from trarium import templates
from trarium.analytics import dashboard
from trarium.convert import ConversionDocument, _write_minimal_docx, convert_file, read_document
from trarium.jobs.store import JobStore
from trarium.library import Library, STATUS_COMPLETED, STATUS_READY
from trarium.repair import analyze_book, repair_pdf
from trarium.resume import ResumeLedger, project_key, stage_table
from trarium.usage import UsageStore


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TRARIUM_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    yield home


# ------------------------------------------------------------------ templates
def test_builtin_templates_cover_the_requested_profiles():
    keys = {item.key for item in templates.list_templates()}
    assert {"manga", "manhwa", "manhua", "web-novel", "light-novel",
            "academic", "technical"} <= keys


def test_applying_a_template_only_touches_its_own_keys():
    base = {"chunk_size": 9000, "workers": 9, "output_format": "txt",
            "provider": "gemini", "target_lang": "fa"}
    applied = templates.apply_template("manga", base)
    assert applied["chunk_size"] == 6000
    assert applied["ocr_lang"] == "jpn"
    assert applied["provider"] == "gemini"  # untouched
    assert base["chunk_size"] == 9000  # original dict not mutated
    assert templates.apply_template("does-not-exist", base) == base


def test_custom_template_is_persisted_and_deletable():
    saved = templates.save_custom("پروفایل من", {"chunk_size": 5500, "workers": 2})
    assert saved.key.startswith("custom-")
    assert any(item.key == saved.key for item in templates.list_templates())
    assert templates.get_template(saved.key).settings["chunk_size"] == 5500
    assert templates.delete_custom(saved.key) is True
    assert templates.get_template(saved.key) is None


# --------------------------------------------------------------------- resume
def test_resume_ledger_tracks_stages_and_survives_a_new_instance(tmp_path):
    source = tmp_path / "book.epub"
    source.write_bytes(b"demo")
    key = project_key(source, "fa", "gemini", "m1")
    ledger = ResumeLedger()
    ledger.ensure(key, title="کتاب", source_path=str(source))
    ledger.start(key, "repair")
    ledger.complete(key, "repair", "سالم")
    ledger.start(key, "ingest")
    ledger.complete(key, "ingest", "۱۲ بخش")
    ledger.complete(key, "ocr", "لازم نبود")
    ledger.start(key, "translate")
    ledger.mark_unit(key, "translate", "3", "completed")

    fresh = ResumeLedger()
    assert fresh.is_done(key, "ingest") is True
    assert fresh.is_done(key, "translate") is False
    snapshot = fresh.snapshot(key)
    assert snapshot["next_stage"] == "translate"
    assert snapshot["resume_from"] == "ocr"
    assert fresh.done_units(key, "translate") == {"3"}
    assert snapshot["progress"] == round(3 / 5, 4)
    assert any(row["مرحله"] == "ترجمه" for row in stage_table(fresh, key))


def test_resume_ledger_marks_failures():
    ledger = ResumeLedger()
    key = project_key("x.pdf", "fa")
    ledger.ensure(key)
    ledger.start(key, "ocr")
    ledger.fail(key, "ocr", "engine missing")
    row = ledger.stage(key, "ocr")
    assert row["status"] == "failed"
    assert "engine" in row["error"]


# --------------------------------------------------------------------- repair
def _make_pdf(path: Path) -> None:
    import pymupdf
    document = pymupdf.open()
    texts = [
        "This is page one with enough text to be treated as a real page of content.",
        "This is page one with enough text to be treated as a real page of content.",
        "",
        "Page three has different content that must be kept in the repaired copy.",
    ]
    for text in texts:
        page = document.new_page()
        if text:
            page.insert_text((72, 100), text)
    document.save(str(path))
    document.close()


def test_repair_detects_duplicates_and_blank_pages(tmp_path):
    source = tmp_path / "scan.pdf"
    _make_pdf(source)
    report = analyze_book(source)
    assert report.pages == 4
    assert 2 in report.duplicates
    assert 3 in report.blank_pages
    assert report.quality_score < 100
    assert report.can_repair is True
    assert "تکراری" in report.summary_fa()


def test_repair_writes_a_new_file_and_keeps_the_original(tmp_path):
    source = tmp_path / "scan.pdf"
    _make_pdf(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "fixed.pdf"
    result = repair_pdf(source, output)
    assert output.exists()
    assert result.pages_before == 4
    assert result.pages_after == 3
    assert result.removed == [2]
    assert result.source_unchanged is True
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with pytest.raises(ValueError):
        repair_pdf(source, source)


# ------------------------------------------------------------------ conversion
def _sample_document() -> ConversionDocument:
    return ConversionDocument(
        title="نمونهٔ فارسی",
        blocks=[
            {"type": "heading", "text": "فصل یک", "level": 1},
            {"type": "paragraph", "text": "این یک پاراگراف فارسی است."},
            {"type": "table", "rows": [["نام", "مقدار"], ["الف", "۱"]]},
        ],
        headings=1, tables=1,
    )


def test_docx_round_trip_keeps_headings_and_tables(tmp_path):
    docx_path = tmp_path / "sample.docx"
    _write_minimal_docx(_sample_document(), docx_path, True)
    parsed = read_document(docx_path)
    assert parsed.headings == 1
    assert parsed.tables == 1
    assert any(block["text"] == "فصل یک" for block in parsed.blocks)
    with zipfile.ZipFile(docx_path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "<w:bidi/>" in xml
    assert "Heading1" in xml


@pytest.mark.parametrize("target", ["html", "txt", "epub", "pdf"])
def test_convert_docx_to_every_supported_format(tmp_path, target):
    docx_path = tmp_path / "sample.docx"
    _write_minimal_docx(_sample_document(), docx_path, True)
    output = tmp_path / f"out.{target}"
    report = convert_file(docx_path, output, target, rtl=True)
    assert output.exists() and output.stat().st_size > 0
    assert report.output_format == target
    assert report.headings == 1
    assert report.source_unchanged is True
    if target == "html":
        assert 'dir="rtl"' in output.read_text(encoding="utf-8")


def test_convert_refuses_to_overwrite_the_source(tmp_path):
    docx_path = tmp_path / "sample.docx"
    _write_minimal_docx(_sample_document(), docx_path, True)
    with pytest.raises(ValueError):
        convert_file(docx_path, docx_path, "html")


# -------------------------------------------------------------------- library
def test_library_persists_books_without_starting_jobs(tmp_path):
    library = Library()
    book = library.add_book(b"hello world", name="story.txt", category="رمان")
    assert book["status"] == STATUS_READY
    assert book["category"] == "رمان"
    # A fresh instance (new process) still sees the book and its settings.
    reopened = Library()
    stored = reopened.get_book(book["id"])
    assert stored and stored["filename"] == "story.txt"
    reopened.set_settings(book["id"], {"target_lang": "en", "template": "web-novel"})
    assert Library().get_book(book["id"])["settings"]["target_lang"] == "en"
    assert Path(book["path"]).exists()
    # Adding to the library must never create a translation job.
    assert JobStore().stats()["total"] == 0


def test_library_playlists_and_delete_without_removing_the_file(tmp_path):
    library = Library()
    book = library.add_book(b"content", name="b.txt")
    playlist = library.create_playlist("مانگاهای من")
    library.add_to_playlist(playlist["id"], book["id"])
    assert [item["id"] for item in library.list_books(playlist=playlist["id"])] == [book["id"]]
    assert library.stats()["playlists"] == 1
    library.remove_from_playlist(playlist["id"], book["id"])
    assert library.list_books(playlist=playlist["id"]) == []
    path = Path(book["path"])
    assert library.delete_book(book["id"], remove_file=False) is True
    assert library.get_book(book["id"]) is None
    assert path.exists()


def test_library_search_sort_and_status_updates():
    library = Library()
    first = library.add_book(b"one", name="Alpha.txt", title="آلفا")
    second = library.add_book(b"two", name="Beta.txt", title="بتا")
    library.set_status(second["id"], STATUS_COMPLETED)
    assert [book["id"] for book in library.list_books(search="آلفا")] == [first["id"]]
    assert library.list_books(sort="title_asc")[0]["title"] in {"آلفا"}
    assert library.stats()["completed"] == 1
    assert set(library.stats()["counts"]) <= {"ready", "completed"}


# ------------------------------------------------------------------ analytics
def test_analytics_reads_real_jobs_and_usage():
    store = JobStore()
    job_id = store.create_job("book", "کتاب تست", total_units=10)
    now = time.time()
    store.update(job_id, status="completed", completed_units=10, started_at=now - 120,
                 finished_at=now, input_tokens=1000, output_tokens=500, cost_usd=0.02)
    usage = UsageStore()
    usage.record("gemini", "gemini-3.8-flash", input_tokens=1000, output_tokens=500)
    data = dashboard(30, job_store=store, usage_store=usage)
    assert data["books_translated"] == 1
    assert data["pages_processed"] == 10
    assert data["avg_pages_per_minute"] == 5.0
    assert data["tokens_all"] >= 1500
    assert data["providers"] and data["providers"][0]["provider"] == "gemini"
    assert data["history"][0]["status"] == "completed"


def test_analytics_is_empty_but_valid_without_data():
    data = dashboard(7, job_store=JobStore(), usage_store=UsageStore())
    assert data["books_translated"] == 0
    assert data["pages_processed"] == 0
    assert data["providers"] == []
