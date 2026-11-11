"""استخراج متن از فایل‌های PDF با استفاده از PyMuPDF."""

import pymupdf
from trarium.extractors.base import BaseExtractor, ExtractedBook


class PDFExtractor(BaseExtractor):
    """استخراج متن از PDF."""

    def extract(self, file_path: str) -> ExtractedBook:
        path = self.validate_file(file_path)
        doc = pymupdf.open(str(path))

        # استخراج متادیتا
        metadata = doc.metadata or {}
        title = metadata.get("title", "") or path.stem
        author = metadata.get("author", "") or "ناشناس"

        chapters = []
        full_text_parts = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")

            if text.strip():
                full_text_parts.append(text)
                chapters.append({
                    "title": f"صفحه {page_num + 1}",
                    "content": text,
                })

        full_text = "\n\n".join(full_text_parts)
        doc.close()

        return ExtractedBook(
            title=title,
            author=author,
            chapters=chapters,
            full_text=full_text,
            total_chars=len(full_text),
        )
