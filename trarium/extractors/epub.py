"""استخراج متن از فایل‌های EPUB."""

import mimetypes
import re
from html import unescape

import ebooklib
from ebooklib import epub
from trarium.extractors.base import BaseExtractor, ExtractedBook
from trarium.extractors.watermark import strip_watermarks


class EPUBExtractor(BaseExtractor):
    """استخراج متن از EPUB."""

    def extract(self, file_path: str) -> ExtractedBook:
        path = self.validate_file(file_path)
        book = epub.read_epub(str(path))

        # متادیتا
        try:
            t = book.get_metadata("DC", "title")
            title = t[0][0] if t else path.stem
        except Exception:
            title = path.stem
        try:
            a = book.get_metadata("DC", "creator")
            author = a[0][0] if a else "ناشناس"
        except Exception:
            author = "ناشناس"

        chapters = []
        full_text_parts = []

        # ترتیب مطالعه را از spine بگیر، نه ترتیب فیزیکی فایل‌ها.
        spine_items = []
        for item_id, _linear in book.spine:
            item = book.get_item_with_id(item_id)
            if item is not None:
                spine_items.append(item)

        for item in spine_items:
            # ebooklib 0.21 ثابت را در ebooklib نگه می‌دارد، نه epub.
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                content = item.get_content().decode("utf-8", errors="replace")
                # B20: Better HTML handling — preserve line breaks
                content = re.sub(r"<br\s*/?>", "\n", content)
                content = re.sub(r"<p[^>]*>", "\n", content, flags=re.I)
                content = re.sub(r"</p>", "\n", content, flags=re.I)
                text = re.sub(r"<[^>]+>", " ", content)
                text = unescape(text)
                text = re.sub(r"[ \t]+", " ", text)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                # خطوط واترمارک (مثل OceanofPDF.com) نه ترجمه می‌شوند نه در کتاب می‌مانند.
                text = strip_watermarks(text)
                if text:
                    chapters.append({"title": item.get_name(), "content": text})
                    full_text_parts.append(text)

        full_text = "\n\n".join(full_text_parts)
        cover, cover_name = _cover_bytes(book)

        return ExtractedBook(
            title=title,
            author=author,
            chapters=chapters,
            full_text=full_text,
            total_chars=len(full_text),
            cover=cover,
            cover_mime=(mimetypes.guess_type(cover_name)[0] or "image/jpeg") if cover else "",
        )


def _cover_bytes(book) -> tuple[bytes, str]:
    """جلد کتاب را از خود فایل EPUB پیدا می‌کند (هم EPUB3 و هم متای EPUB2).

    قبلاً هیچ extractor جلد را برنمی‌داشت، پس جلد کتاب در خروجی ترجمه گم
    می‌شد؛ کتاب‌خوان‌ها صفحهٔ اول را به‌عنوان جلد نشان می‌دادند.
    """
    item_types = {
        getattr(ebooklib, "ITEM_COVER", -1),
        getattr(ebooklib, "ITEM_IMAGE", -2),
    }
    candidates = []
    try:
        for item in book.get_items():
            try:
                is_cover = item.get_type() == getattr(ebooklib, "ITEM_COVER", -1)
            except Exception:
                is_cover = False
            if is_cover:
                candidates.append(item)
    except Exception:
        pass
    if not candidates:
        try:
            for value in book.get_metadata("OPF", "cover") or []:
                item_id = str(value[0]) if value else ""
                item = book.get_item_with_id(item_id) if item_id else None
                if item is not None:
                    candidates.append(item)
        except Exception:
            pass
    if not candidates:
        try:
            images = [item for item in book.get_items() if item.get_type() in item_types
                      and item.get_type() != getattr(ebooklib, "ITEM_COVER", -1)]
            images.sort(key=lambda item: ("cover" not in item.get_name().lower(), len(item.get_name())))
            if images:
                candidates.append(images[0])
        except Exception:
            pass
    for item in candidates:
        try:
            data = item.get_content() or b""
        except Exception:
            data = b""
        if data:
            return data, item.get_name()
    return b"", ""
