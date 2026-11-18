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


if __name__ == "__main__":
    unittest.main()
