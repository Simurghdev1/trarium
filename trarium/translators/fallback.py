"""ترجمه‌گری که با تمام‌شدن سهمیه یا خطای یک مدل، خودکار به مدل بعدی می‌رود.

قانون: پرووایدر یا مدلی که خطا داد (سهمیه، ۴۲۹، کلید نامعتبر) کنار گذاشته
می‌شود و ترجمه با بهترین گزینهٔ بعدی ادامه پیدا می‌کند؛ ترجمه هرگز با متن
نیمه‌انگلیسی رها نمی‌شود.
"""
from __future__ import annotations

import threading

from trarium.translators.base import BaseTranslator

# A candidate that answers with one of these will not answer better a second
# time, so it is dropped for the rest of the run.
FATAL_MARKERS = (
    "429",
    "quota",
    "resource_exhausted",
    "rate limit",
    "rate_limit",
    "credit",
    "insufficient",
    "401",
    "403",
    "404",
    "invalid api key",
    "unauthorized",
    "model not found",
    "no endpoints found",
    "daily limit",
    "limit exceeded",
)


class AllProvidersFailed(RuntimeError):
    """Every candidate refused the request."""


def _is_fatal(exc: Exception) -> bool:
    message = f"{exc}".lower()
    return any(marker in message for marker in FATAL_MARKERS)


class FallbackTranslator(BaseTranslator):
    """Try each (provider, model) in order, skipping the ones that ran out."""

    def __init__(self, candidates, labels: list[str] | None = None):
        if not candidates:
            raise ValueError("حداقل یک مترجم لازم است.")
        self.candidates = list(candidates)
        self.labels = list(labels or [f"{i + 1}" for i in range(len(self.candidates))])
        self.dead: dict[int, str] = {}
        self.calls: dict[int, int] = {}
        self.switches: list[tuple[str, str, str]] = []
        self._lock = threading.Lock()

    # -- reporting ---------------------------------------------------------
    @property
    def model_name(self) -> str:
        alive = [self.labels[i] for i in range(len(self.candidates)) if i not in self.dead]
        return alive[0] if alive else "همهٔ مدل‌ها تمام شد"

    @property
    def provider(self) -> str:
        return "fallback"

    def status(self) -> dict:
        return {
            "labels": self.labels,
            "dead": {self.labels[i]: why for i, why in self.dead.items()},
            "calls": {self.labels[i]: n for i, n in self.calls.items()},
            "switches": list(self.switches),
        }

    # -- translation -------------------------------------------------------
    def get_token_limit(self) -> int:
        return min(
            (t.get_token_limit() for t in self.candidates if hasattr(t, "get_token_limit")),
            default=4096,
        )

    def translate(self, text: str, target_lang: str = "fa") -> str:
        last_error: Exception | None = None
        for index, candidate in enumerate(self.candidates):
            if index in self.dead:
                continue
            try:
                out = candidate.translate(text, target_lang)
                with self._lock:
                    self.calls[index] = self.calls.get(index, 0) + 1
                if out and out.strip():
                    self.last_usage = getattr(candidate, "last_usage", {}) or {}
                    return out
                raise RuntimeError("پاسخ خالی از مدل")
            except Exception as exc:  # noqa: BLE001 - the whole point is to move on
                last_error = exc
                if _is_fatal(exc):
                    with self._lock:
                        self.dead[index] = str(exc)[:200]
                        self.switches.append((self.labels[index], str(exc)[:80], "حذف شد"))
                    continue
                # A transient failure still deserves the next candidate.
                with self._lock:
                    self.switches.append((self.labels[index], str(exc)[:80], "رد شد"))
                continue
        raise AllProvidersFailed(
            "همهٔ پرووایدرها/مدل‌ها خطا دادند. آخرین خطا: " + (str(last_error) if last_error else "نامشخص")
        )
