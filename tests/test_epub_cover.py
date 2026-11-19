"""EPUB cover preservation and chapter splitting (no network)."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from ebooklib import epub
from PIL import Image

from trarium.extractors.epub import EPUBExtractor
from trarium.output import _split_sections, save_as_epub
from trarium.processors.pipeline import TranslationPipeline, TranslationResult
from trarium.translators.base import BaseTranslator


def _png(color=(24, 120, 92)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (60, 90), color).save(buffer, "PNG")
    return buffer.getvalue()


def _make_epub(path: Path, cover: bytes | None = _png()) -> None:
    book = epub.EpubBook()
    book.set_identifier("test-cover-book")
    book.set_title("Cover Book")
    book.set_language("en")
    book.add_author("Writer")
    if cover:
        book.set_cover("cover.png", cover)
    chapter = epub.EpubHtml(title="One", file_name="chap_01.xhtml", lang="en")
    chapter.content = ("<html><body><h1>One</h1><p>"
                       + "This is a plain sentence for translation. " * 20
                       + "</p></body></html>")
    book.add_item(chapter)
    book.toc = (chapter,)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book)


class Translator(BaseTranslator):
    def get_token_limit(self):
        return 10000

    def translate(self, text, target_lang="fa"):
        return "این یک متن ترجمه‌شده برای آزمایش جلد کتاب است."


class CoverRoundTripTests(unittest.TestCase):
    def test_extractor_reads_the_source_cover(self):
        cover = _png()
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "book.epub"
            _make_epub(source, cover)
            book = EPUBExtractor().extract(str(source))
        self.assertEqual(book.cover, cover)
        self.assertEqual(book.cover_mime, "image/png")

    def test_translated_epub_keeps_the_cover(self):
        cover = _png()
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "book.epub"
            output = Path(td) / "book_fa.epub"
            _make_epub(source, cover)
            book = EPUBExtractor().extract(str(source))
            pipeline = TranslationPipeline(Translator(), "fa", max_workers=1,
                                           use_checkpoint=False, translation_memory=False)
            result = pipeline.run(book)
            self.assertEqual(result.cover, cover)
            path = save_as_epub(result, str(output))
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                self.assertIn("OEBPS/images/cover.png", names, names)
                self.assertEqual(archive.read("OEBPS/images/cover.png"), cover)
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('properties="cover-image"', opf)
                self.assertIn('<meta name="cover" content="cover-image"/>', opf)
                spine = opf.split("<spine>", 1)[1]
                self.assertTrue(spine.startswith('<itemref idref="coverpage"/>'), spine[:80])
                cover_page = archive.read("OEBPS/cover.xhtml").decode("utf-8")
                self.assertIn("images/cover.png", cover_page)

    def test_epub_without_a_cover_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "plain.epub"
            output = Path(td) / "plain_fa.epub"
            _make_epub(source, cover=None)
            book = EPUBExtractor().extract(str(source))
            self.assertEqual(book.cover, b"")
            result = TranslationPipeline(Translator(), "fa", max_workers=1,
                                         use_checkpoint=False, translation_memory=False).run(book)
            path = save_as_epub(result, str(output))
            with zipfile.ZipFile(path) as archive:
                self.assertNotIn("OEBPS/cover.xhtml", archive.namelist())
                self.assertNotIn("cover-image",
                                 archive.read("OEBPS/content.opf").decode("utf-8"))


class ChapterSplitTests(unittest.TestCase):
    def test_leading_separator_keeps_every_chapter(self):
        text = ("--- Chapter One ---\n\nFirst paragraph.\n\nSecond paragraph.\n\n"
                "--- Chapter Two ---\n\nBody of two.")
        sections = _split_sections(text)
        self.assertEqual([title for title, _ in sections], ["Chapter One", "Chapter Two"])
        self.assertIn("First paragraph.", sections[0][1])
        self.assertIn("Body of two.", sections[1][1])

    def test_webnovel_export_is_not_empty(self):
        # This is the shape handlers.handle_novel builds for a web novel.
        result = TranslationResult(
            title="Arc 7", author="", original_lang="auto", target_lang="fa",
            translated_text="--- Arc 7 Chapter 1 ---\n\nمتن فصل یک.\n\n--- Arc 7 Chapter 2 ---\n\nمتن فصل دو.")
        with tempfile.TemporaryDirectory() as td:
            path = save_as_epub(result, str(Path(td) / "novel.epub"))
            with zipfile.ZipFile(path) as archive:
                chapters = sorted(name for name in archive.namelist()
                                  if name.startswith("OEBPS/chapter_"))
                self.assertEqual(len(chapters), 3)  # two chapters + attribution page
                first = archive.read("OEBPS/chapter_1.xhtml").decode("utf-8")
                self.assertIn("<p>", first)
                self.assertIn("متن فصل یک", first)


if __name__ == "__main__":
    unittest.main()
