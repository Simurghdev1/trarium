"""Persistent translation memory and cache for safe resume and de-duplication."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from pathlib import Path

CACHE_VERSION = "v5"


def normalize_cache_text(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


class TranslationCache:
    def __init__(self, path: str | None = None):
        from trarium.local_state import trarium_home
        self.path = Path(path) if path else trarium_home() / "cache.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS translations (k TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_translations_k ON translations(k)")
            db.commit()

    @staticmethod
    def _key(text: str, target: str, provider: str = "", model: str = "", instructions: str = "") -> str:
        raw = "\x1f".join([
            CACHE_VERSION,
            provider or "",
            model or "",
            target or "",
            instructions or "",
            normalize_cache_text(text),
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, text: str, target: str, provider: str = "", model: str = "", instructions: str = "") -> str | None:
        key = self._key(text, target, provider, model, instructions)
        with self._lock, sqlite3.connect(self.path) as db:
            row = db.execute("SELECT value FROM translations WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, text: str, value: str, target: str, provider: str = "", model: str = "", instructions: str = "") -> None:
        if not str(value or "").strip():
            return
        key = self._key(text, target, provider, model, instructions)
        with self._lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO translations(k,value) VALUES(?,?)", (key, str(value)))
            db.commit()

    def clear(self) -> None:
        with self._lock, sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM translations")
            db.commit()


class CachedTranslator:
    """Drop-in translator wrapper with persistent exact/normalized translation memory."""
    def __init__(self, inner, provider: str = "", model: str = "", cache: TranslationCache | None = None,
                 instructions: str = ""):
        self.inner = inner
        self.provider = provider
        self.model = model
        # Router-style short prompt; keeps cached translations from another style apart.
        self.instructions = instructions or ""
        self.cache = cache or TranslationCache()
        self.hits = 0
        self.misses = 0
        self.last_error = ""
        self.last_usage = {}

    def get_token_limit(self):
        return self.inner.get_token_limit()

    def translate(self, text: str, target_lang: str = "fa", instructions: str = "") -> str:
        style = (self.instructions + "|" + str(instructions or "")).strip("|")
        hit = self.cache.get(text, target_lang, self.provider, self.model, style)
        if hit is not None:
            self.hits += 1
            self.last_usage = {"cache_hit": True, "input_tokens": 0, "output_tokens": 0}
            return hit
        self.misses += 1
        try:
            try:
                value = self.inner.translate(text, target_lang, instructions)
            except TypeError as exc:
                if "instructions" not in str(exc) and "positional argument" not in str(exc):
                    raise
                value = self.inner.translate(text, target_lang)
            self.last_error = ""
            self.last_usage = getattr(self.inner, "last_usage", {}) or {}
        except Exception as exc:
            self.last_error = str(exc)
            raise
        if value and str(value).strip():
            self.cache.put(text, str(value), target_lang, self.provider, self.model, style)
        return str(value or "")
