import tempfile
import unittest
import zipfile
from pathlib import Path

from trarium.extractors import get_extractor
from trarium.extractors.generic import UnsupportedFormatError


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class FormatSupportTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_txt_with_ocr_flag_no_longer_crashes(self):
        path = _write(self.tmp / "a.txt", "Hello world.\n\nSecond paragraph.".encode())
        book = get_extractor(str(path), use_ocr=True).extract(str(path))
        self.assertIn("Hello world.", book.full_text)

    def test_docx_paragraphs_and_metadata(self):
        path = self.tmp / "doc.docx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "word/document.xml",
                "<w:document><w:body>"
                "<w:p><w:r><w:t>Chapter One</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>It was a </w:t></w:r><w:r><w:t>dark night.</w:t></w:r></w:p>"
                "</w:body></w:document>",
            )
            zf.writestr(
                "docProps/core.xml",
                "<cp:coreProperties><dc:title>My Book</dc:title>"
                "<dc:creator>Jane</dc:creator></cp:coreProperties>",
            )
        book = get_extractor(str(path)).extract(str(path))
        self.assertEqual(book.title, "My Book")
        self.assertEqual(book.author, "Jane")
        self.assertIn("It was a dark night.", book.full_text)

    def test_pptx_slides(self):
        path = self.tmp / "deck.pptx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "ppt/slides/slide1.xml",
                "<p:sld><a:p><a:t>Slide title</a:t></a:p><a:p><a:t>Body line</a:t></a:p></p:sld>",
            )
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Slide title", book.full_text)
        self.assertIn("Body line", book.full_text)

    def test_xlsx_shared_strings(self):
        path = self.tmp / "sheet.xlsx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "xl/sharedStrings.xml",
                "<sst><si><t>Full name</t></si><si><t>Description of the item</t></si></sst>",
            )
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                '<worksheet><sheetData><row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row></sheetData></worksheet>',
            )
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Full name | Description of the item", book.full_text)

    def test_odt_text(self):
        path = self.tmp / "doc.odt"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "content.xml",
                "<office:document-content><text:p>سلام دنیا، این یک جملهٔ آزمایشی است.</text:p>"
                "<text:p>خط <text:span>دوم</text:span> هم اینجاست.</text:p></office:document-content>",
            )
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("سلام دنیا، این یک جملهٔ آزمایشی است.", book.full_text)
        self.assertIn("خط دوم هم اینجاست.", book.full_text)

    def test_rtf(self):
        path = _write(self.tmp / "a.rtf", rb"{\rtf1\ansi Hello \b world\b0.\par Second line.}")
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Hello world.", book.full_text)
        self.assertIn("Second line.", book.full_text)

    def test_html(self):
        path = _write(
            self.tmp / "page.html",
            "<html><head><title>Site</title><style>b{}</style></head>"
            "<body><h1>Title</h1><p>First.</p><p>Second.</p></body></html>".encode(),
        )
        book = get_extractor(str(path)).extract(str(path))
        self.assertEqual(book.title, "Site")
        self.assertIn("First.", book.full_text)
        self.assertNotIn("b{}", book.full_text)

    def test_srt_subtitles(self):
        path = _write(
            self.tmp / "sub.srt",
            "1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n"
            "2\n00:00:04,000 --> 00:00:06,000\nGeneral Kenobi.\n".encode(),
        )
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Hello there.", book.full_text)
        self.assertIn("General Kenobi.", book.full_text)
        self.assertNotIn("-->", book.full_text)

    def test_ass_subtitles(self):
        path = _write(
            self.tmp / "sub.ass",
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\i1}Hello there, friend{\\i0}\\NWorld is waiting for you\n".encode(),
        )
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Hello there, friend", book.full_text)
        self.assertIn("World is waiting for you", book.full_text)
        self.assertNotIn("{\\i1}", book.full_text)

    def test_unknown_extension_with_text_is_translatable(self):
        path = _write(self.tmp / "book.unknownext", "This is a translatable sentence.\n\nAnd another one.".encode())
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("translatable sentence", book.full_text)

    def test_zip_bundle_reads_text_members(self):
        path = self.tmp / "bundle.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("readme.md", "# Title\n\nSome content here.")
            zf.writestr("image.png", b"\x89PNG\r\n\x1a\n\x00\x00")
        book = get_extractor(str(path)).extract(str(path))
        self.assertIn("Some content here.", book.full_text)

    def test_video_is_refused_with_clear_message(self):
        path = _write(self.tmp / "movie.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        with self.assertRaises(UnsupportedFormatError) as ctx:
            get_extractor(str(path)).extract(str(path))
        self.assertIn("ویدیو", str(ctx.exception))

    def test_audio_is_refused(self):
        path = _write(self.tmp / "voice.mp3", b"ID3\x04\x00" + b"\x00" * 64)
        with self.assertRaises(UnsupportedFormatError) as ctx:
            get_extractor(str(path)).extract(str(path))
        self.assertIn("صوتی", str(ctx.exception))

    def test_image_is_refused(self):
        path = _write(self.tmp / "pic.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        with self.assertRaises(UnsupportedFormatError) as ctx:
            get_extractor(str(path)).extract(str(path))
        self.assertIn("تصویر", str(ctx.exception))


class FormatCatalogTests(unittest.TestCase):
    """فهرست کامل پسوندها باید بدون جا افتادن و بدون ادعای اضافی باشد."""

    def setUp(self):
        from trarium.extractors import catalog
        from trarium.extractors.formats import (
            SUPPORTED_EXTENSIONS,
            TRANSLATABLE_EXTENSIONS,
            DISPLAY_FORMAT_GROUPS,
        )

        self.catalog = catalog
        self.supported = set(SUPPORTED_EXTENSIONS)
        self.translatable = set(TRANSLATABLE_EXTENSIONS)
        self.display_groups = DISPLAY_FORMAT_GROUPS

    def test_catalog_has_more_than_six_hundred_formats(self):
        self.assertGreaterEqual(len(self.supported), 600)
        self.assertEqual(len(self.supported), len(self.catalog.ALL_EXTENSIONS))

    def test_every_extension_is_either_translatable_or_refused(self):
        gaps = self.supported - self.translatable - set(self.catalog.NON_TEXT_EXTENSIONS)
        self.assertEqual(gaps, set(), f"این پسوندها دسته‌بندی نشده‌اند: {sorted(gaps)[:20]}")
        # آرشیوهای زیپ‌محور می‌توانند متن داخلشان داشته باشند، پس هم "بدون متن"
        # فهرست می‌شوند و هم ترجمه‌شدنی؛ بقیه نباید هم‌زمان در هر دو باشند.
        allowed_overlap = set(self.catalog.ZIP_CONTAINER_EXTENSIONS)
        unexpected = (self.translatable & set(self.catalog.NON_TEXT_EXTENSIONS)) - allowed_overlap
        self.assertEqual(unexpected, set())

    def test_source_list_is_fully_covered(self):
        raw = set().union(*self.catalog.PRIORITY_EXTENSIONS.values())
        self.assertEqual(raw - self.supported, set())
        self.assertEqual(len(raw), len(self.catalog.ALL_EXTENSIONS))

    def test_display_list_is_short_and_valid(self):
        shown = [
            ext
            for _, examples in self.display_groups
            for ext in examples.split()
            if ext.startswith(".")
        ]
        self.assertLessEqual(len(shown), 80)
        self.assertEqual([ext for ext in shown if ext not in self.supported], [])

    def test_search_reaches_formats_hidden_from_the_short_list(self):
        from trarium.extractors.formats import search_formats

        self.assertIn(".srt", search_formats("srt"))
        self.assertIn(".sas7bdat", search_formats("sas7b"))
        self.assertIn(".kicad_pcb", search_formats("KICAD"))
        self.assertIn(".mp4", search_formats("mp4"))
        self.assertEqual(search_formats("zzzz"), [])

    def test_new_text_formats_are_translatable(self):
        for ext in (".qmd", ".rmd", ".ifc", ".sdf", ".proto", ".eml"):
            self.assertIn(ext, self.translatable, ext)

    def test_quarto_file_translates(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "chapter.qmd"
            path.write_text("# Title\n\nThis is a translatable paragraph.\n\nSecond one.", encoding="utf-8")
            book = get_extractor(str(path)).extract(str(path))
            self.assertIn("translatable paragraph", book.full_text)

    def test_three_d_binary_is_refused_with_clear_message(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "scene.blend"
            path.write_bytes(b"BLENDER" + b"\x00" * 256)
            with self.assertRaises(UnsupportedFormatError) as ctx:
                get_extractor(str(path)).extract(str(path))
            self.assertIn("باینری", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
