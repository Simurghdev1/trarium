"""تقسیم‌کننده متن به بخش‌های مناسب برای API."""
import re

# عنوان‌هایی که صفحه‌شمار PDF هستند (مثل «صفحه ۱۲») و ارزش نگه‌داشتن به‌عنوان
# مرز فصل را ندارند؛ همین صفحات را می‌توان با هم در یک درخواست فرستاد.
_PAGE_LIKE_TITLE = re.compile(r"^\s*صفحه\s*[\d۰-۹]+\s*$")


class TextChunker:
    """متن رو به بخش‌های کوچک‌تر تقسیم می‌کنه تا در محدودیت API جا بشه."""

    def __init__(self, max_chars: int = 15000, overlap: int = 200):
        """
        max_chars: حداکثر کاراکتر هر بخش
        overlap: همپوشانی بین بخش‌ها (برای حفظ زمینه)
        """
        if max_chars <= 0:
            raise ValueError("max_chars باید بزرگ‌تر از صفر باشد")
        if overlap < 0 or overlap >= max_chars:
            raise ValueError("overlap باید بین صفر و max_chars-1 باشد")
        self.max_chars = max_chars
        self.overlap = overlap

    def chunk_by_chapters(self, chapters: list[dict]) -> list[dict]:
        """متن رو بر اساس فصل‌ها تقسیم می‌کنه.

        فصل‌های کوتاه پشت‌سرهم در یک بخش جمع می‌شوند (کاهش تعداد درخواست و
        هزینه)، ولی مرز فصل‌هایی که عنوان واقعی دارند حفظ می‌شود تا سرفصل
        کتاب در خروجی گم نشود. صفحات PDF («صفحه ۱۲») قابل ادغام‌اند.
        """
        chunks: list[dict] = []
        buffer: list[str] = []
        buffer_len = 0
        buffer_title = ""
        mergeable = True

        def flush():
            nonlocal buffer, buffer_len, buffer_title, mergeable
            if buffer:
                chunks.append({
                    "title": buffer_title,
                    "content": "\n\n".join(buffer).strip(),
                })
            buffer, buffer_len, buffer_title, mergeable = [], 0, "", True

        for chapter in chapters:
            text = (chapter.get("content") or "").strip()
            title = (chapter.get("title") or "").strip()
            if not text:
                continue

            # فصل بلند: بخش یا بخش‌های مستقل
            if len(text) > self.max_chars:
                flush()
                for i, sub in enumerate(self.chunk_text(text)):
                    chunks.append({
                        "title": f"{title} (بخش {i + 1})" if title else f"بخش {i + 1}",
                        "content": sub,
                    })
                continue

            is_page_like = bool(_PAGE_LIKE_TITLE.match(title))
            extra = len(text) + (2 if buffer else 0)

            if buffer and (not mergeable or not is_page_like or buffer_len + extra > self.max_chars):
                flush()

            if not buffer:
                buffer_title = title
                mergeable = is_page_like
            buffer.append(text)
            buffer_len += extra

        flush()
        return chunks

    def chunk_text(self, text: str) -> list[str]:
        """متن ساده رو به بخش‌های کوچک‌تر تقسیم می‌کنه — هوشمند (جمله‌محور)."""
        if len(text) <= self.max_chars:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = start + self.max_chars

            if end < len(text):
                # اول تلاش کن آخر جمله بشکنه (. ! ?)
                break_point = -1
                for pattern in [r"\.\s", r"!\s", r"\?\s", r"\.\n", r"!\n", r"\?\n"]:
                    bp = list(re.finditer(pattern, text[start + self.max_chars // 3:end]))
                    if bp:
                        # end() پس از فاصله/خط جدید است، پس مرز درست همان‌جاست
                        break_point = start + self.max_chars // 3 + bp[-1].end()

                # اگه جمله پیدا نشد، خط جدید
                if break_point == -1:
                    break_point = text.rfind("\n", start + self.max_chars // 2, end)

                # اگه خط هم پیدا نشد، فاصله
                if break_point == -1:
                    break_point = text.rfind(" ", start + self.max_chars // 2, end)

                if break_point > start:
                    end = break_point
                else:
                    end = start + self.max_chars

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            # overlap فقط برای context است؛ از برگشتن به نقطه قبلی جلوگیری کن.
            next_start = end - self.overlap
            if next_start <= start:
                next_start = end
            start = next_start

        return chunks
