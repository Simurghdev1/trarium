"""Provider failover scheduler. Dynamic failover is safer than fixed round-robin."""
from __future__ import annotations
import time

class FallbackTranslator:
    def __init__(self, translators: list[tuple[str, object]], cooldown_seconds: float = 30.0):
        self.translators = [(name, tr) for name, tr in translators if tr is not None]
        if not self.translators:
            raise ValueError("حداقل یک ارائه‌دهنده لازم است.")
        self.cooldown_seconds = cooldown_seconds
        self.cooldowns: dict[str, float] = {}
        self.last_provider = ""
        self.last_error = ""
        self.last_usage = {}
        self.failures = 0
        self.provider = "fallback:" + "+".join(name for name, _ in self.translators)
        self.model = "dynamic"

    def get_token_limit(self):
        limits = [getattr(tr, "get_token_limit", lambda: 8000)() for _, tr in self.translators]
        return max(limits or [8000])

    def _ordered(self):
        now = time.monotonic()
        # Cooling providers are skipped; retrying them is what made a dead
        # provider stall every request until its cooldown expired.
        return [(n, t) for n, t in self.translators if self.cooldowns.get(n, 0) <= now]

    def translate(self, text, target_lang="fa", instructions=""):
        last = None
        for name, translator in self._ordered():
            try:
                try:
                    value = translator.translate(text, target_lang, instructions)
                except TypeError as exc:
                    if "instructions" not in str(exc) and "positional argument" not in str(exc):
                        raise
                    value = translator.translate(text, target_lang)
                if not str(value or "").strip():
                    raise RuntimeError("پاسخ خالی بود")
                self.last_provider = name
                self.last_usage = getattr(translator, "last_usage", {}) or {}
                self.last_error = ""
                self.cooldowns.pop(name, None)
                return str(value)
            except Exception as exc:
                last = exc
                self.failures += 1
                self.last_error = str(exc)
                self.cooldowns[name] = time.monotonic() + self.cooldown_seconds
        raise RuntimeError("همهٔ ارائه‌دهنده‌های فعال ناموفق شدند: " + str(last or "خطای نامشخص"))
