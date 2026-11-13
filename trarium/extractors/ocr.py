"""Optional PDF OCR extractor using the same engine layer as layout mode."""
from __future__ import annotations

import pymupdf
from trarium.extractors.base import BaseExtractor, ExtractedBook
from trarium.pdf_optimized import extract_ocr_blocks


class OCRExtractor(BaseExtractor):
    def __init__(self, ocr_lang: str = "eng", ocr_method: str = "auto"):
        self.ocr_lang = ocr_lang
        self.ocr_method = ocr_method

    def extract(self, file_path: str) -> ExtractedBook:
        path = self.validate_file(file_path)
        doc = pymupdf.open(str(path))
        chapters = []
        parts = []
        try:
            title = (doc.metadata or {}).get("title") or path.stem
            author = (doc.metadata or {}).get("author") or "ناشناس"
            for idx, page in enumerate(doc):
                blocks = extract_ocr_blocks(page, lang=self.ocr_lang, ocr_method=self.ocr_method)
                text = "\n".join(b["text"] for b in blocks).strip()
                if text:
                    chapters.append({"title": f"صفحه {idx + 1}", "content": text})
                    parts.append(text)
        finally:
            doc.close()
        full = "\n\n".join(parts)
        return ExtractedBook(title=title, author=author, chapters=chapters, full_text=full, total_chars=len(full))
