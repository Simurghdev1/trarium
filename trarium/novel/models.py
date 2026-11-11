"""
🌿 تراریوم — مدل‌های داده وب‌نول
"""
from dataclasses import dataclass, field
from pathlib import Path
import json
import time as time_mod


@dataclass
class Chapter:
    """یک فصل از رمان."""
    number: int = 0
    title: str = ""
    text: str = ""
    url: str = ""
    next_url: str = ""
    scraped_at: float = 0.0
    translated: bool = False
    translated_text: str = ""

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def char_count(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "title": self.title,
            "text": self.text,
            "url": self.url,
            "next_url": self.next_url,
            "scraped_at": self.scraped_at,
            "translated": self.translated,
            "translated_text": self.translated_text,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Chapter":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Novel:
    """اطلاعات کلی رمان."""
    title: str = ""
    author: str = ""
    source_url: str = ""
    language: str = "en"
    target_language: str = "fa"
    total_chapters: int = 0
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def scraped_count(self) -> int:
        return len([c for c in self.chapters if c.text])

    @property
    def translated_count(self) -> int:
        return len([c for c in self.chapters if c.translated])

    def add_chapter(self, chapter: Chapter):
        if not chapter.text:
            return
        for i, c in enumerate(self.chapters):
            if c.number == chapter.number:
                self.chapters[i] = chapter
                return
        self.chapters.append(chapter)
        self.chapters.sort(key=lambda x: x.number)

    def get_chapter(self, number: int) -> Chapter | None:
        for c in self.chapters:
            if c.number == number:
                return c
        return None

    def get_untranslated(self) -> list[Chapter]:
        return [c for c in self.chapters if c.text and not c.translated]

    def get_missing(self) -> list[int]:
        """شماره فصل‌هایی که هنوز اسکرپ نشدن."""
        scraped = {c.number for c in self.chapters if c.text}
        if not self.chapters:
            return []
        max_ch = max(c.number for c in self.chapters)
        return [i for i in range(1, max_ch + 1) if i not in scraped]

    def save(self, path: str):
        """ذخیره رمان به JSON."""
        data = {
            "title": self.title,
            "author": self.author,
            "source_url": self.source_url,
            "language": self.language,
            "target_language": self.target_language,
            "total_chapters": self.total_chapters,
            "chapters": [c.to_dict() for c in self.chapters],
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "Novel":
        """بارگذاری رمان از JSON."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        novel = cls(
            title=data.get("title", ""),
            author=data.get("author", ""),
            source_url=data.get("source_url", ""),
            language=data.get("language", "en"),
            target_language=data.get("target_language", "fa"),
            total_chapters=data.get("total_chapters", 0),
        )
        for cd in data.get("chapters", []):
            novel.chapters.append(Chapter.from_dict(cd))
        return novel

    def to_txt(self, path: str, translated: bool = False):
        """خروجی TXT."""
        lines = []
        for c in sorted(self.chapters, key=lambda x: x.number):
            lines.append(f"--- فصل {c.number}: {c.title} ---")
            lines.append("")
            text = c.translated_text if (translated and c.translated) else c.text
            lines.append(text)
            lines.append("")
            lines.append("")
        Path(path).write_text("\n".join(lines), encoding="utf-8")

    def to_epub(self, path: str, translated: bool = False):
        """خروجی EPUB."""
        try:
            from ebooklib import epub
        except ImportError:
            raise RuntimeError("pip install ebooklib")

        book = epub.EpubBook()
        book.set_identifier(f"trarium-{self.title}-{int(time_mod.time())}")
        book.set_title(self.title)
        book.set_language(self.target_language if translated else self.language)

        spine = ["nav"]
        toc = []

        for c in sorted(self.chapters, key=lambda x: x.number):
            text = c.translated_text if (translated and c.translated) else c.text
            title = f"فصل {c.number}: {c.title}" if c.title else f"فصل {c.number}"

            chapter = epub.EpubHtml(
                title=title,
                file_name=f"chapter_{c.number}.xhtml",
                lang=self.target_language if translated else self.language,
            )
            import html as html_mod
            safe_text = html_mod.escape(text).replace(chr(10), "</p><p>")
            chapter.content = f"<h1>{html_mod.escape(title)}</h1><p>{safe_text}</p>"
            book.add_item(chapter)
            spine.append(chapter)
            toc.append(chapter)

        book.toc = toc
        book.spine = spine
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        epub.write_epub(path, book, {})
