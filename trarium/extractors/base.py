"""کلاس پایه برای استخراج‌کننده‌های متن."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
# class Chapter (unused)
class _Chapter:
    """یک فصل از کتاب."""
    title: str
    content: str
    order: int = 0


@dataclass
class ExtractedBook:
    """نتیجه استخراج متن از کتاب."""
    title: str
    author: str
    chapters: list[dict] = field(default_factory=list)
    full_text: str = ""
    total_chars: int = 0


class BaseExtractor(ABC):
    """کلاس پایه برای همه extractorها."""

    @abstractmethod
    def extract(self, file_path: str) -> ExtractedBook:
        """متن کتاب رو از فایل استخراج می‌کند."""
        ...

    def validate_file(self, file_path: str) -> Path:
        """بررسی می‌کند فایل وجود دارد."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"فایل پیدا نشد: {file_path}")
        return path
