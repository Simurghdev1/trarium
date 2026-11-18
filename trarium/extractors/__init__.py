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
    """یک extractor مناسب برای فایل ورودی برمی‌گرداند.

    فرمت‌های شناخته‌شده extractor اختصاصی دارند و بقیهٔ فایل‌ها (سند، زیرنویس،
    کد، آرشیو متنی و حتی پسوند ناشناخته) به extractor عمومی می‌روند تا اگر متن
    دارند ترجمه شوند. اگر فایل ذاتاً متن نداشته باشد (ویدیو/صدا/عکس)،
    `UnsupportedFormatError` با پیام روشن بالا می‌رود.
    """
    from pathlib import Path

    from trarium.extractors.formats import ALREADY_NATIVE
    from trarium.extractors.generic import GenericTextExtractor, sniff_container

    path = Path(file_path)
    ext = path.suffix.lower()
    container = sniff_container(path)

    # OCR فقط برای PDF معنا دارد؛ برای بقیهٔ فرمت‌ها بی‌صدا نادیده گرفته می‌شود
    # تا یک فلگ اشتباه باعث خطای بی‌دلیل نشود.
    if ext == ".pdf" or container == "pdf":
        from trarium.extractors.pdf import PDFExtractor
        return PDFExtractor(use_ocr=use_ocr, ocr_lang=ocr_lang, ocr_method=ocr_method)

    if ext in ALREADY_NATIVE:
        extractor_class = _get_extractor_class(ext)
        if extractor_class is not None:
            return extractor_class()

    return GenericTextExtractor()


def supported_formats() -> list[str]:
    """فهرست پسوندهای قابل ترجمه برای نمایش در رابط کاربری."""
    from trarium.extractors.formats import TRANSLATABLE_EXTENSIONS
    return list(TRANSLATABLE_EXTENSIONS)
