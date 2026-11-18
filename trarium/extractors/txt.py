"""استخراج متن از فایل‌های متنی ساده."""

from trarium.extractors.base import BaseExtractor, ExtractedBook
from trarium.extractors.generic import UnsupportedFormatError


class TXTExtractor(BaseExtractor):
    """استخراج متن از فایل TXT."""

    def extract(self, file_path: str) -> ExtractedBook:
        path = self.validate_file(file_path)

        # تلاش برای خواندن با encoding‌های مختلف
        text = ""
        for encoding in ["utf-8", "utf-8-sig", "cp1256", "cp1252"]:
            try:
                text = path.read_text(encoding=encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if not text.strip():
            raise UnsupportedFormatError(
                f"«{path.name}» خالی است یا متن قابل خواندنی ندارد؛ "
                "فایل متنی بدون محتوا قابل ترجمه نیست."
            )

        # تقسیم بر اساس خطوط خالی (پاراگراف‌ها)
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        # اگه پاراگرافی پیدا نشد، خط به خط تقسیم کن
        if not paragraphs:
            paragraphs = [line.strip() for line in text.split("\n") if line.strip()]

        chapters = []
        full_text_parts = []

        for i, para in enumerate(paragraphs):
            full_text_parts.append(para)
            # تشخیص فصل (اگه خط کوتاه باشه و شبیه عنوان باشه)
            lines = para.split("\n")
            if len(lines[0]) < 60 and any(
                kw in lines[0].lower()
                for kw in ["chapter", "فصل", "بخش", "part", "section"]
            ):
                title = lines[0]
            else:
                title = f"پاراگراف {i + 1}"

            chapters.append({
                "title": title,
                "content": para,
            })

        full_text = "\n\n".join(full_text_parts)

        return ExtractedBook(
            title=path.stem,
            author="ناشناس",
            chapters=chapters,
            full_text=full_text,
            total_chars=len(full_text),
        )
