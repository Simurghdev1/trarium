"""تقسیم‌کننده متن به بخش‌های مناسب برای API."""
import re


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

    @staticmethod
    def _is_generic_section_title(title: str) -> bool:
        title = (title or "").strip().lower()
        return bool(
            re.fullmatch(r"(?:page|صفحه)\s*\d+", title, re.I)
            or re.fullmatch(r"(?:paragraph|پاراگراف)\s*\d+", title, re.I)
            or re.fullmatch(r"(?:section|بخش)\s*\d+", title, re.I)
        )

    def chunk_by_chapters(self, chapters: list[dict]) -> list[dict]:
        """Pack small generic sections together to reduce API calls without merging named chapters."""
        chunks = []
        current_parts: list[str] = []
        current_len = 0
        current_title = ""
        generic_bundle = True

        def flush():
            nonlocal current_parts, current_len, current_title, generic_bundle
            if current_parts:
                chunks.append({"title": current_title or "متن", "content": "\n\n".join(current_parts).strip()})
            current_parts = []
            current_len = 0
            current_title = ""
            generic_bundle = True

        for chapter in chapters:
            chapter_text = str(chapter.get("content", "") or "").strip()
            chapter_title = str(chapter.get("title", "") or "").strip() or "متن"
            if not chapter_text:
                continue

            is_generic = self._is_generic_section_title(chapter_title)
            if len(chapter_text) > self.max_chars:
                flush()
                for i, sc in enumerate(self.chunk_text(chapter_text)):
                    chunks.append({"title": f"{chapter_title} (بخش {i + 1})", "content": sc})
                continue

            # Generic page/paragraph labels are only bookkeeping; safely pack them together.
            if current_parts and (not generic_bundle or not is_generic or current_len + len(chapter_text) + 2 > self.max_chars):
                flush()

            if not current_parts:
                current_title = chapter_title if not is_generic else "متن"
                generic_bundle = is_generic

            current_parts.append(chapter_text)
            current_len += len(chapter_text) + 2

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
                        break_point = start + self.max_chars // 3 + bp[-1].end()

                # اگه جمله پیدا نشد، خط جدید
                if break_point == -1:
                    break_point = text.rfind("\n", start + self.max_chars // 2, end)

                # اگه خط هم پیدا نشد، فاصله
                if break_point == -1:
                    break_point = text.rfind(" ", start + self.max_chars // 2, end)

                if break_point > start:
                    # break_point already sits just past the delimiter (+1 used to
                    # eat the next word's first letter: «Sentence» -> «S» + «entence»)
                    end = break_point

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            # overlap فقط برای context است؛ از برگشتن به نقطه قبلی جلوگیری کن.
            next_start = end - self.overlap
            if next_start <= start:
                next_start = end
            start = next_start

        return chunks
