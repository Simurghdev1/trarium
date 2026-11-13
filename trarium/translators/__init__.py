"""Translator factory with lazy imports."""
from __future__ import annotations
from importlib import import_module
from trarium.translators.base import BaseTranslator
_TRANSLATORS = {
    "gemini": ("trarium.translators.gemini", "GeminiTranslator"),
    "openrouter": ("trarium.translators.openrouter", "OpenRouterTranslator"),
    "router9": ("trarium.translators.router9", "Router9Translator"),
    "groq": ("trarium.translators.groq", "GroqTranslator"),
    "openai_compatible": ("trarium.translators.openai_compatible", "OpenAICompatibleTranslator"),
}
def get_translator(name: str = "openrouter", **kwargs) -> BaseTranslator:
    if name not in _TRANSLATORS: raise ValueError(f"ترجمه‌گر پشتیبانی نمی‌شود: {name}")
    module_name, class_name = _TRANSLATORS[name]
    try: cls = getattr(import_module(module_name), class_name)
    except ImportError as exc: raise RuntimeError(f"وابستگی‌های {name} نصب نیستند: {exc}") from exc
    return cls(**kwargs)
