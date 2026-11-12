"""Data models for scraped/translated web novels."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from pathlib import Path


@dataclass
class Chapter:
    number: int = 0
    title: str = ""
    text: str = ""
    url: str = ""
    next_url: str = ""
    scraped_at: float = field(default_factory=time.time)
    translated: bool = False
    translated_text: str = ""

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def to_dict(self):
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Novel:
    title: str = ""
    author: str = ""
    source_url: str = ""
    language: str = "en"
    target_language: str = "fa"
    total_chapters: int = 0
    chapters: list[Chapter] = field(default_factory=list)

    def add_chapter(self, chapter: Chapter):
        if not chapter.text:
            return
        existing = next((i for i, c in enumerate(self.chapters) if c.url == chapter.url or (chapter.number and c.number == chapter.number)), None)
        if existing is None:
            self.chapters.append(chapter)
        else:
            self.chapters[existing] = chapter
        self.chapters.sort(key=lambda c: (c.number if c.number else 10**9, c.url))

    @property
    def scraped_count(self):
        return sum(bool(c.text) for c in self.chapters)

    @property
    def translated_count(self):
        return sum(bool(c.translated) for c in self.chapters)

    def to_txt(self, path: str, translated: bool = False):
        parts = []
        for c in sorted(self.chapters, key=lambda x: (x.number if x.number else 10**9, x.url)):
            text = c.translated_text if translated and c.translated else c.text
            parts.append(f"--- فصل {c.number or ''}: {c.title} ---\n\n{text.strip()}")
        Path(path).write_text("\n\n".join(parts) + "\n", encoding="utf-8")

    def save(self, path: str):
        data = {"title": self.title, "author": self.author, "source_url": self.source_url,
                "language": self.language, "target_language": self.target_language,
                "total_chapters": self.total_chapters, "chapters": [c.to_dict() for c in self.chapters]}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        novel = cls(**{k: data.get(k, getattr(cls, k, None)) for k in ("title", "author", "source_url", "language", "target_language", "total_chapters")})
        novel.chapters = [Chapter.from_dict(x) for x in data.get("chapters", [])]
        return novel
