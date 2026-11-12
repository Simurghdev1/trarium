from trarium.extractors.base import BaseExtractor


# Imports are kept lazy where they have heavier/external dependencies so that
# TXT/PDF usage does not fail just because an unrelated EPUB dependency is absent.
def _get_extractor_class(ext: str):
    if ext == ".pdf":
        from trarium.extractors.pdf import PDFExtractor
        return PDFExtractor
    if ext == ".epub":
        try:
            from trarium.extractors.epub import EPUBExtractor
        except ImportError as e:
            raise ImportError("EPUB support requires ebooklib: pip install ebooklib>=0.18") from e
        return EPUBExtractor
    if ext == ".txt":
        from trarium.extractors.txt import TXTExtractor
        return TXTExtractor
    return None


def get_extractor(file_path: str, use_ocr: bool = False, ocr_lang: str = "eng", ocr_method: str = "auto") -> BaseExtractor:
    """یک extractor مناسب برای فایل ورودی برمی‌گرداند."""
    from pathlib import Path

    ext = Path(file_path).suffix.lower()

    if use_ocr and ext != ".pdf":
        raise ValueError("OCR فقط برای فایل‌های PDF پشتیبانی می‌شود؛ برای TXT/EPUB گزینه --ocr را بردارید.")

    if ext == ".pdf":
        from trarium.extractors.pdf import PDFExtractor
        return PDFExtractor(use_ocr=use_ocr, ocr_lang=ocr_lang, ocr_method=ocr_method)

    extractor_class = _get_extractor_class(ext)
    if extractor_class is not None:
        return extractor_class()

    raise ValueError(f"فرمت پشتیبانی نمی‌شود: {ext}. فرمت‌های مجاز: .pdf, .epub, .txt")
