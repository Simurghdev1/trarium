import collections
import tempfile
import unittest
import zipfile
from pathlib import Path

from trarium.extractors import catalog, formats, get_extractor
from trarium.extractors.generic import UnsupportedFormatError
from trarium.extractors.watermark import strip_watermarks


class FormatListTests(unittest.TestCase):
    def test_one_list_of_formats(self):
        self.assertEqual(set(formats.SUPPORTED_EXTENSIONS), set(catalog.ALL_EXTENSIONS))
        self.assertTrue(set(formats.TRANSLATABLE_EXTENSIONS) <= set(catalog.ALL_EXTENSIONS))

    def test_search_finds_every_translatable_extension(self):
        """هر پسوندی که ترجمه می‌شود باید در جست‌وجوی UI هم پیدا شود."""
        missing = [
            ext for ext in formats.TRANSLATABLE_EXTENSIONS
            if ext.lstrip(".")[:4] and ext not in formats.search_formats(ext.lstrip("."), limit=1000)
        ]
        self.assertEqual(missing, [], missing[:10])

    def test_reported_extensions_are_searchable(self):
        for ext in (".bash", ".patch", ".text", ".pyi", ".diff", ".rss", ".odg"):
            self.assertIn(ext, formats.SUPPORTED_EXTENSIONS, ext)
            self.assertIn(ext, formats.TRANSLATABLE_EXTENSIONS, ext)
            self.assertIn(ext, formats.search_formats(ext.lstrip(".")), ext)

    def test_zip_containers_are_text_not_archive(self):
        for ext in (".zip", ".htmlz", ".txtz"):
            self.assertIn(ext, formats.TRANSLATABLE_EXTENSIONS, ext)
            self.assertNotIn(ext, formats.ARCHIVE_EXTENSIONS, ext)
            self.assertFalse(formats.is_non_text_extension(ext), ext)
            self.assertFalse(formats.is_archive_extension(ext), ext)

    def test_counts_are_not_misleading(self):
        translatable, total, non_text = formats.format_stats()
        self.assertEqual(translatable, len(formats.TRANSLATABLE_EXTENSIONS))
        self.assertEqual(total, len(formats.SUPPORTED_EXTENSIONS))
        self.assertEqual(translatable + non_text, total)
        self.assertLess(translatable, total)

    def test_display_groups_have_no_duplicates(self):
        shown = [ext for _, examples in formats.DISPLAY_FORMAT_GROUPS for ext in examples.split() if ext.startswith(".")]
        duplicates = [ext for ext, count in collections.Counter(shown).items() if count > 1]
        self.assertEqual(duplicates, [], duplicates)


class EmptyFileTests(unittest.TestCase):
    def test_empty_txt_raises_standard_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "empty.txt"
            path.write_text("   \n\n", encoding="utf-8")
            with self.assertRaises(UnsupportedFormatError):
                get_extractor(str(path)).extract(str(path))


class DocxTextBoxTests(unittest.TestCase):
    def test_text_box_text_is_not_glued_to_outer_text(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "textbox.docx"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr(
                    "word/document.xml",
                    "<w:document><w:body><w:p>"
                    "<w:r><w:t>Outer before</w:t></w:r>"
                    "<w:r><w:pict><w:txbxContent><w:p><w:r><w:t>Inside box</w:t></w:r></w:p>"
                    "</w:txbxContent></w:pict></w:r>"
                    "<w:r><w:t>Outer after</w:t></w:r>"
                    "</w:p></w:body></w:document>",
                )
            book = get_extractor(str(path)).extract(str(path))
            self.assertNotIn("beforeOuter", book.full_text)
            self.assertIn("Outer before", book.full_text)
            self.assertIn("Outer after", book.full_text)
            self.assertIn("Inside box", book.full_text)


class WatermarkTests(unittest.TestCase):
    def test_watermark_lines_are_dropped(self):
        text = "Chapter One\n\nOceanofPDF.com\n\nThe real story starts here.\n\nDownloaded from z-lib.io"
        cleaned = strip_watermarks(text)
        self.assertIn("Chapter One", cleaned)
        self.assertIn("The real story starts here.", cleaned)
        self.assertNotIn("OceanofPDF", cleaned)
        self.assertNotIn("z-lib", cleaned)

    def test_a_sentence_mentioning_a_site_is_kept(self):
        line = "She read the whole letter on gutenberg.org before the train arrived at the station."
        self.assertIn(line, strip_watermarks(line))


if __name__ == "__main__":
    unittest.main()
