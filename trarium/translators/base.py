"""کلاس پایه برای ترجمه‌گرها."""

from abc import ABC, abstractmethod


class BaseTranslator(ABC):
    """کلاس پایه برای همه ترجمه‌گرها."""

    @abstractmethod
    def translate(self, text: str, target_lang: str = "fa", instructions: str = "") -> str:
        """متن رو به زبان مقصد ترجمه می‌کند.

        ``instructions`` یک دستور کوتاه (پرامپت کوتاه، فرهنگ واژه یا نگهداری
        Placeholder) است و باید در پیام system برود، نه داخل خود متن؛ وگرنه
        بعضی مدل‌ها آن را ترجمه می‌کنند و در خروجی می‌نویسند.
        """
        ...

    @abstractmethod
    def get_token_limit(self) -> int:
        """محدودیت توکن هر درخواست."""
        ...
