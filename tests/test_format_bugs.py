"""تست رگرسیون برای باگ‌هایی که روی فرمت‌های جدید گزارش شده بود."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from trarium.extractors import get_extractor
from trarium.extractors.formats import (
    LEGACY_DOCUMENT_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    extension_of,
)
from trarium.extractors.generic import UnsupportedFormatError


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _docx(path: Path, body_xml: str) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "word/document.xml",
            "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
            f"<w:body>{body_xml}</w:body></w:document>",
        )
    return path


class ExtractRegressionTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    # --- EPUB ---------------------------------------------------------------
    def test_epub_uses_top_level_item_constant(self):
        from ebooklib import epub

        path = self.tmp / "book.epub"
        book = epub.EpubBook()
        book.set_identifier("trarium-test")
        book.set_title("Sample Book")
        book.set_language("en")
        chapter = epub.EpubHtml(title="One", file_name="ch1.xhtml", lang="en")
        chapter.content = "<html><body><p>Epub body sentence.</p></body></html>"
        book.add_item(chapter)
        book.toc = (chapter,)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav", chapter]
        epub.write_epub(str(path), book)

        extracted = get_extractor(str(path)).extract(str(path))
        self.assertEqual(extracted.title, "Sample Book")
        self.assertIn("Epub body sentence.", extracted.full_text)

    # --- زیرنویس ------------------------------------------------------------
    def test_srt_keeps_numeric_dialogue_and_drops_markup(self):
        path = _write(
            self.tmp / "a.srt",
            "1\n00:00:01,000 --> 00:00:02,000\n<i>42</i>\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nHello there, general.\n".encode(),
        )
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("42", book.full_text)
        self.assertIn("Hello there, general.", book.full_text)
        self.assertNotIn("<i>", book.full_text)
        self.assertNotIn("-->", book.full_text)

    def test_vtt_cue_identifier_is_not_translated(self):
        path = _write(
            self.tmp / "a.vtt",
            "WEBVTT\n\nintro-cue\n00:00:01.000 --> 00:00:02.000\nThis is the real subtitle line.\n".encode(),
        )
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("This is the real subtitle line.", book.full_text)
        self.assertNotIn("intro-cue", book.full_text)

    def test_microdvd_sub_strips_codes_and_splits_lines(self):
        path = _write(
            self.tmp / "a.sub",
            "{25}{75}First line|second line\n{100}{150}Another line.\n".encode(),
        )
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("First line\nsecond line", book.full_text)
        self.assertNotIn("{25}", book.full_text)

    def test_hex_subtitle_format_is_refused(self):
        path = _write(self.tmp / "a.scc", b"Scenarist_SCC V1.0\n\n00:00:01:00\t9420 9420\n")
        with self.assertRaises(UnsupportedFormatError) as ctx:
            get_extractor(str(path)).extract(str(path))
        self.assertIn("باینری", str(ctx.exception))

    # --- FB2 / MHTML / RTF ---------------------------------------------------
    def test_fb2_drops_base64_image_and_keeps_title(self):
        payload = "iVBORw0KGgo" * 500
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<FictionBook><description><title-info><book-title>Real Title</book-title>'
            "</title-info></description><body><section><p>The actual sentence.</p>"
            f"</section></body><binary id=\"cover.jpg\" content-type=\"image/jpeg\">{payload}</binary>"
            "</FictionBook>"
        )
        path = _write(self.tmp / "a.fb2", xml.encode())
        book = get_extractor(str(path)).extract(str(path))
        self.assertEqual(book.title, "Real Title")
        self.assertNotIn("iVBORw0KGgo", book.full_text)
        self.assertLess(len(book.full_text), 500)
        self.assertIn("The actual sentence.", book.full_text)

    def test_mhtml_decodes_quoted_printable(self):
        raw = (
            "MIME-Version: 1.0\r\n"
            'Content-Type: text/html; charset=utf-8\r\n'
            "Content-Transfer-Encoding: quoted-printable\r\n\r\n"
            "<html><body><p>Hello=20world from MHTML.</p></body></html>"
        )
        path = _write(self.tmp / "page.mhtml", raw.encode())
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Hello", book.full_text)
        self.assertIn("world from MHTML.", book.full_text)
        self.assertNotIn("=20", book.full_text)
        self.assertNotIn("Content-Type", book.full_text)

    def test_rtf_drops_font_table_and_reads_codepage_accent(self):
        path = _write(
            self.tmp / "a.rtf",
            b"{\\rtf1\\ansi\\ansicpg1252{\\fonttbl{\\f0 Times New Roman;}}"
            b"Caf\\'e9 Se\\~nor.\\par Second line.}",
        )
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Café", book.full_text)
        self.assertNotIn("Times New Roman", book.full_text)
        self.assertNotIn("fonttbl", book.full_text)

    # --- DOCX / XLSX --------------------------------------------------------
    def test_docx_textbox_text_is_kept_in_order(self):
        body = (
            "<w:p><w:r><w:t>Before box.</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Outer start.</w:t></w:r>"
            "<w:r><w:txbxContent><w:p><w:r><w:t>Inside box.</w:t></w:r></w:p></w:txbxContent></w:r>"
            "<w:r><w:t> Outer end.</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>After box.</w:t></w:r></w:p>"
        )
        path = _docx(self.tmp / "a.docx", body)
        book = get_extractor(str(path)).extract(str(path))
        text = book.full_text
        for fragment in ("Before box.", "Inside box.", "After box.", "Outer end."):
            self.assertIn(fragment, text)
        self.assertLess(text.index("Before box."), text.index("Inside box."))
        self.assertLess(text.index("Inside box."), text.index("After box."))
        # متن تکست‌باکس نباید به پاراگراف بعدی بچسبد
        self.assertNotIn("box.After", text)

    def test_xlsx_sheets_are_ordered_numerically(self):
        path = self.tmp / "a.xlsx"
        with zipfile.ZipFile(path, "w") as zf:
            for number, label in ((1, "First sheet"), (2, "Second sheet"), (10, "Tenth sheet")):
                zf.writestr(
                    f"xl/worksheets/sheet{number}.xml",
                    f"<worksheet><sheetData><row><c><is><t>{label}</t></is></c></row></sheetData></worksheet>",
                )
        book = get_extractor(str(path)).extract(str(path))
        self.assertLess(book.full_text.index("First sheet"), book.full_text.index("Second sheet"))
        self.assertLess(book.full_text.index("Second sheet"), book.full_text.index("Tenth sheet"))

    # --- آرشیو و بسته‌ها -----------------------------------------------------
    def test_software_package_inside_zip_is_refused(self):
        path = self.tmp / "app.jar"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("AndroidManifest.xml", "<manifest>ignore me</manifest>")
            zf.writestr("res/values/strings.xml", "<resources><string>ignore</string></resources>")
        with self.assertRaises(UnsupportedFormatError) as ctx:
            get_extractor(str(path)).extract(str(path))
        self.assertIn("بسته", str(ctx.exception))

    def test_zip_reads_document_inside_archive(self):
        nested = _docx(
            self.tmp / "inner.docx",
            "<w:p><w:r><w:t>Text inside the archived docx.</w:t></w:r></w:p>",
        )
        path = self.tmp / "bundle.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("inner.docx", nested.read_bytes())
            zf.writestr("notes.txt", "Plain note text inside the archive.")
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Text inside the archived docx.", book.full_text)
        self.assertIn("Plain note text inside the archive.", book.full_text)

    def test_zip_without_text_explains_what_is_inside(self):
        path = self.tmp / "images.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
            zf.writestr("b.jpg", b"\xff\xd8\xff" + b"\x00" * 32)
        with self.assertRaises(UnsupportedFormatError) as ctx:
            get_extractor(str(path)).extract(str(path))
        message = str(ctx.exception)
        self.assertIn("a.png", message)
        self.assertIn("b.jpg", message)

    # --- کدگذاری و اندازه ---------------------------------------------------
    def test_utf16_without_bom_is_read(self):
        text = "# Title\n\nاین یک متن فارسی سالم است.\n\nSecond paragraph here."
        path = _write(self.tmp / "a.md", text.encode("utf-16-le"))
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("این یک متن فارسی سالم است.", book.full_text)
        self.assertIn("Second paragraph here.", book.full_text)

    def test_oversized_file_is_refused_instead_of_truncated(self):
        path = self.tmp / "big.md"
        with open(path, "wb") as handle:
            handle.seek(25 * 1024 * 1024)
            handle.write(b"x")
        with self.assertRaises(UnsupportedFormatError) as ctx:
            get_extractor(str(path)).extract(str(path))
        self.assertIn("مگابایت", str(ctx.exception))

    def test_notebook_code_cells_are_not_translated(self):
        notebook = {
            "cells": [
                {"cell_type": "markdown", "source": ["# Chapter\n", "\n", "Narrative text."]},
                {"cell_type": "code", "source": ["print('do not translate')"]},
            ]
        }
        path = _write(self.tmp / "a.ipynb", json.dumps(notebook).encode())
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Narrative text.", book.full_text)
        self.assertNotIn("do not translate", book.full_text)

    def test_notebook_without_markdown_is_refused(self):
        notebook = {"cells": [{"cell_type": "code", "source": ["x = 1"]}]}
        path = _write(self.tmp / "code.ipynb", json.dumps(notebook).encode())
        with self.assertRaises(UnsupportedFormatError) as ctx:
            get_extractor(str(path)).extract(str(path))
        self.assertIn("markdown", str(ctx.exception))

    def test_m4a_is_reported_as_audio_not_video(self):
        path = _write(self.tmp / "voice.m4a", b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 64)
        with self.assertRaises(UnsupportedFormatError) as ctx:
            get_extractor(str(path)).extract(str(path))
        self.assertIn("صوتی", str(ctx.exception))

    # --- پسوندها ------------------------------------------------------------
    def test_dotfiles_have_extensions(self):
        self.assertEqual(extension_of(".gitignore"), ".gitignore")
        self.assertEqual(extension_of("dir/.env"), ".env")
        self.assertEqual(extension_of("book.md"), ".md")

    def test_rb_is_ruby_not_legacy_book(self):
        self.assertNotIn(".rb", LEGACY_DOCUMENT_EXTENSIONS)
        self.assertNotIn(".scc", SUBTITLE_EXTENSIONS)
        self.assertNotIn(".stl", SUBTITLE_EXTENSIONS)


if __name__ == "__main__":
    unittest.main()
