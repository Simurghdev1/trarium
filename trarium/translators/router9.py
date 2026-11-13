"""ترجمه متن با 9Router (localhost:20128)."""
from __future__ import annotations

import math
import threading
import time
import requests
from trarium.translators.base import BaseTranslator
from trarium.utils import clean_translation as _clean_translation

LANG_NAMES = {"fa": "فارسی", "en": "English", "ar": "العربية", "tr": "Türkçe", "zh": "中文", "ja": "日本語", "ko": "한국어", "ru": "Русский"}
MODELS_9ROUTER = {
    "Auto (پیشنهادی)": "auto",
    "Gemini 2.5 Flash": "gemini-2.5-flash",
    "Gemini 2.5 Flash (Google)": "google/gemini-2.5-flash",
    "Qwen 3": "qwen3-235b-a22b",
    "iFlow": "iflow",
    "Kiro": "kiro",
}
SYSTEM = "You are a professional literary translator. Translate completely and faithfully. Output only the translation. Preserve names, numbers, URLs, paragraph order and meaning."
_thread_local = threading.local()


def _session():
    s = getattr(_thread_local, "session", None)
    if s is None:
        s = requests.Session()
        _thread_local.session = s
    return s


class Router9Translator(BaseTranslator):
    API_URL = "http://localhost:20128/v1/chat/completions"

    def __init__(self, api_key=None, model_name=None):
        self.api_key = api_key or "9router"
        self.model_name = model_name or "auto"
        self.last_usage = {}

    def get_token_limit(self):
        return 128_000

    def translate(self, text, target_lang="fa"):
        lang = LANG_NAMES.get(target_lang, target_lang)
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": SYSTEM + (" For Persian, use correct نیم‌فاصله and «…». Avoid em dash/en dash." if target_lang == "fa" else "")},
                {"role": "user", "content": f"Translate to {lang}.\n\n{text}"},
            ],
            "max_tokens": max(4096, min(30000, math.ceil(len(str(text)) * 1.15))),
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last = None
        for attempt in range(3):
            try:
                response = _session().post(self.API_URL, headers=headers, json=payload, timeout=180)
                response.raise_for_status()
                data = response.json(); usage = data.get("usage") or {}
                self.last_usage = {"input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0), "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)}
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError("9Router پاسخ خالی برگرداند.")
                content = choices[0].get("message", {}).get("content")
                if not content:
                    raise RuntimeError("9Router متن ترجمه را برنگرداند.")
                return _clean_translation(str(content))
            except Exception as exc:
                last = exc
                if attempt < 2:
                    time.sleep(1.5 * (2 ** attempt))
        raise RuntimeError(f"خطا در ترجمه 9Router: {last}")
