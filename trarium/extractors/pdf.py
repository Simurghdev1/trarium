"""Hybrid PDF text extraction: native text first, OCR only for pages without text."""
from __future__ import annotations

import pymupdf
from trarium.extractors.base import BaseExtractor, ExtractedBook


class PDFExtractor(BaseExtractor):
    def __init__(self, use_ocr: bool = True, ocr_lang: str = "eng", ocr_method: str = "auto"):
        self.use_ocr = use_ocr
        self.ocr_lang = ocr_lang
        self.ocr_method = ocr_method
        self.pages_native = 0
        self.pages_ocr = 0

    def extract(self, file_path: str) -> ExtractedBook:
        path = self.validate_file(file_path)
        with pymupdf.open(str(path)) as doc:
            metadata = doc.metadata or {}
            title = metadata.get("title", "") or path.stem
            author = metadata.get("author", "") or "ناشناس"
            chapters = []
            full_parts = []
            for page_num, page in enumerate(doc):
                text = page.get_text("text").strip()
                if text:
                    self.pages_native += 1
                elif self.use_ocr:
                    try:
                        from trarium.pdf_optimized import extract_ocr_blocks
                        blocks = extract_ocr_blocks(page, lang=self.ocr_lang, ocr_method=self.ocr_method)
                        text = "\n".join(b.get("text", "") for b in blocks if b.get("text")).strip()
                        if text:
                            self.pages_ocr += 1
                    except Exception:
                        text = ""
                if text:
                    full_parts.append(text)
                    chapters.append({"title": f"صفحه {page_num + 1}", "content": text, "page": page_num})
            full = "\n\n".join(full_parts)
            return ExtractedBook(title=title, author=author, chapters=chapters, full_text=full, total_chars=len(full))
