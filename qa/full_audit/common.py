"""Shared helpers for the full Trarium QA audit (2.19).

Keys are read from the environment only; nothing is written into the repo.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
FILES = Path(os.environ.get("TRARIUM_FILES", APP.parent / "trarium-files"))
OUT = Path(os.environ.get("QA_OUT", APP / "qa-out"))
OUT.mkdir(parents=True, exist_ok=True)

from trarium.short_prompt import build_short_prompt  # noqa: E402

# «روان عادی نوشتن» برای همهٔ اجراها: نه ادبی، نه شاعرانه.
SHORT_PROMPT = build_short_prompt(["ادبی و شاعرانه ننویس", "ترجمهٔ عادی و روان"])
PROVIDER = os.environ.get("QA_PROVIDER", "gemini")
MODEL = os.environ.get("QA_MODEL", "gemini-3.5-flash-lite")


def log_event(stage: str, **data) -> None:
    path = OUT / "qa-events.jsonl"
    payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage, **data}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def result(stage: str, **data) -> None:
    folder = OUT / stage
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "result.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log_event(stage, **data)


def translator():
    from trarium.translators import get_translator

    if PROVIDER == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        return get_translator("gemini", api_key=key, model_name=MODEL)
    key = os.environ.get("OPENROUTER_API_KEY")
    return get_translator("openrouter", api_key=key, model_name=MODEL)


class Throttled:
    """Keeps providers with a low requests-per-minute quota from bursting."""

    def __init__(self, inner, seconds: float):
        self.inner = inner
        self.seconds = max(0.0, float(seconds))
        self.provider = getattr(inner, "provider", PROVIDER)
        self.model = getattr(inner, "model_name", MODEL)
        self.last_usage = {}
        self._lock = __import__("threading").Lock()
        self._next = 0.0

    def get_token_limit(self):
        return self.inner.get_token_limit()

    def translate(self, text, target_lang="fa"):
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.seconds
            if wait:
                time.sleep(wait)
        out = self.inner.translate(text, target_lang)
        self.last_usage = getattr(self.inner, "last_usage", {}) or {}
        return out


def throttled(seconds: float | None = None):
    return Throttled(translator(),
                     os.environ.get("QA_THROTTLE", "4.5") if seconds is None else seconds)


def out_dir(stage: str) -> Path:
    folder = OUT / stage
    folder.mkdir(parents=True, exist_ok=True)
    return folder
