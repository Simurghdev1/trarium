"""کلاس پایه برای ترجمه‌گرها."""

from abc import ABC, abstractmethod


class BaseTranslator(ABC):
    """کلاس پایه برای همه ترجمه‌گرها."""

    @abstractmethod
    def translate(self, text: str, target_lang: str = "fa") -> str:
        """متن رو به زبان مقصد ترجمه می‌کند."""
        ...

    @abstractmethod
    def get_token_limit(self) -> int:
        """محدودیت توکن هر درخواست."""
        ...
