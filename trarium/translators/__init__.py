from trarium.translators.base import BaseTranslator
from trarium.translators.gemini import GeminiTranslator
from trarium.translators.openrouter import OpenRouterTranslator
from trarium.translators.router9 import Router9Translator

TRANSLATORS = {
    "gemini": GeminiTranslator,
    "openrouter": OpenRouterTranslator,
    "router9": Router9Translator,
}


def get_translator(name: str = "gemini", **kwargs) -> BaseTranslator:
    """ترجمه‌گر مورد نظر را برمی‌گرداند."""
    if name not in TRANSLATORS:
        raise ValueError(f"ترجمه‌گر پشتیبانی نمی‌شود: {name}")
    return TRANSLATORS[name](**kwargs)
