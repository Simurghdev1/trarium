"""OCR extractor."""
from pathlib import Path
from trarium.extractors.base import ExtractedBook, BaseExtractor


class OCRExtractor(BaseExtractor):
    """PDF اسکن‌شده رو با OCR متن‌خوان می‌کنه."""

    def __init__(self, ocr_lang: str = "fas+eng"):
        self.ocr_lang = ocr_lang

    def extract(self, file_path: str) -> ExtractedBook:
        try:
            import pytesseract
            from PIL import Image
            import pymupdf
        except ImportError:
            raise ImportError("پکیج‌های لازم نصب نیستن: pip install pytesseract Pillow pymupdf")

        doc = pymupdf.open(file_path)
        try:
            chapters = []
            lang = self.ocr_lang

            for i, page in enumerate(doc):
                # فقط صفحاتی که متن قابل استخراج ندارند OCR می‌شوند.
                text = page.get_text("text").strip()

                if not text:
                    pix = page.get_pixmap(dpi=300)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    try:
                        text = pytesseract.image_to_string(img, lang=lang, config="--psm 3")
                    finally:
                        img.close()

                if text.strip():
                    chapters.append({
                        "title": f"صفحه {i + 1}",
                        "content": text.strip(),
                    })

            title = Path(file_path).stem
            full_text = "\n\n".join(ch["content"] for ch in chapters)
            return ExtractedBook(
                title=title,
                author="ناشناس",
                chapters=chapters,
                full_text=full_text,
                total_chars=len(full_text),
            )
        finally:
            doc.close()
