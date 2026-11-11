"""ترجمه متن با 9Router (localhost:20128)."""
import math
import threading
import time
import requests
from trarium.translators.base import BaseTranslator
from trarium.utils import clean_translation as _clean_translation

LANG_NAMES = {
    "fa": "فارسی", "en": "انگلیسی", "ar": "عربی", "fr": "فرانسوی",
    "de": "آلمانی", "es": "اسپانیایی", "tr": "ترکی", "ja": "ژاپنی",
    "ko": "کره‌ای", "zh": "چینی", "ru": "روسی",
}
MODELS_9ROUTER = {
    "Auto (پیشنهادی)": "auto",
    "Gemini 2.5 Flash": "gemini-2.5-flash",
    "Gemini 2.5 Flash (Google)": "google/gemini-2.5-flash",
    "Qwen 3 (رایگان)": "qwen3-235b-a22b",
    "iFlow (رایگان)": "iflow",
    "Kiro AI (رایگان)": "kiro",
}
SYSTEM_PROMPT = (
    "You are a professional literary translator. Translate completely and faithfully into the requested target language. "
    "Output only the translation, with no commentary, metadata, safety notes, or reasoning. "
    "Preserve names, numbers, URLs, paragraph order, and meaning."
)
PERSIAN_RULES = " For Persian: use correct Persian نیم‌فاصله and quotation marks «…», and never use em dash or en dash."

def _system_prompt(target_lang):
    return SYSTEM_PROMPT + (PERSIAN_RULES if target_lang == "fa" else "")

TRANSLATE_PROMPT = "Translate to {lang}. Output only the translation."
_thread_local = threading.local()


def _get_http_session():
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def _max_output_tokens(text: str) -> int:
    return max(2048, min(20000, math.ceil(len(text) * 0.75)))


class Router9Translator(BaseTranslator):
    API_URL = "http://localhost:20128/v1/chat/completions"

    def __init__(self, api_key=None, model_name=None):
        self.api_key = api_key or "9router"
        self.model_name = model_name or MODELS_9ROUTER["Auto (پیشنهادی)"]

    def get_token_limit(self):
        return 128_000

    def translate(self, text, target_lang="fa"):
        lang_name = LANG_NAMES.get(target_lang, target_lang)
        user_msg = TRANSLATE_PROMPT.format(lang=lang_name) + "\n\n" + text
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": _system_prompt(target_lang)},
                {"role": "user", "content": user_msg},
            ],
            "max_tokens": _max_output_tokens(text),
            "temperature": 0.3,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                response = _get_http_session().post(self.API_URL, headers=headers, json=payload, timeout=120)
                response.raise_for_status()
                data = response.json()
                raw = data["choices"][0]["message"]["content"]
                return _clean_translation(raw)
            except requests.exceptions.HTTPError:
                try:
                    error_msg = response.json().get("error", {}).get("message", response.text[:200])
                except Exception:
                    error_msg = response.text[:200]
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"خطا در ترجمه 9Router ({response.status_code}): {error_msg}")
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"خطا در ترجمه 9Router: {e}")
