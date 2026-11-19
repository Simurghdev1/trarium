"""Optional content-complexity routing: fast model for very simple dialogue, strong model otherwise."""
from __future__ import annotations

import re


def classify_text(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return "empty"
    sentences = re.split(r"(?<=[.!?؟])\s+", value)
    avg_len = sum(len(s) for s in sentences) / max(1, len(sentences))
    quoted = len(re.findall(r"[«“\"].{1,200}?[»”\"]", value))
    dialogue_marks = value.count("—") + value.count("–") + quoted
    dialogue_ratio = dialogue_marks / max(1, len(value) / 100)
    complex_punct = len(re.findall(r"[;:()]", value))
    if len(value) <= 900 and avg_len <= 150 and dialogue_ratio >= 1.5 and complex_punct <= 12:
        return "simple_dialogue"
    return "complex"


class SmartRoutingTranslator:
    def __init__(self, strong, fast):
        if strong is None or fast is None:
            raise ValueError("Smart routing به دو مترجم نیاز دارد.")
        self.strong = strong
        self.fast = fast
        self.provider = "smart-routing"
        self.model = "dynamic"
        self.last_provider = ""
        self.last_usage = {}

    def get_token_limit(self):
        return max(self.strong.get_token_limit(), self.fast.get_token_limit())

    def translate(self, text, target_lang="fa", instructions=""):
        kind = classify_text(text)
        preferred = self.fast if kind == "simple_dialogue" else self.strong
        alternate = self.strong if preferred is self.fast else self.fast
        try:
            try:
                out = preferred.translate(text, target_lang, instructions)
            except TypeError as exc:
                if "instructions" not in str(exc) and "positional argument" not in str(exc):
                    raise
                out = preferred.translate(text, target_lang)
            self.last_provider = "fast" if preferred is self.fast else "strong"
            self.last_usage = getattr(preferred, "last_usage", {}) or {}
            return out
        except Exception:
            try:
                out = alternate.translate(text, target_lang, instructions)
            except TypeError as exc:
                if "instructions" not in str(exc) and "positional argument" not in str(exc):
                    raise
                out = alternate.translate(text, target_lang)
            self.last_provider = "strong" if alternate is self.strong else "fast"
            self.last_usage = getattr(alternate, "last_usage", {}) or {}
            return out
