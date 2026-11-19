"""ترجمه متن با OpenRouter."""
from __future__ import annotations

from trarium import __version__

import math
import threading
import time
from trarium.translators.base import BaseTranslator
from trarium.translators.openrouter_http import OpenRouterSecurityBlockError, post_json
from trarium.utils import clean_translation as _clean_translation, is_permanent_http_error

LANG_NAMES = {
    "fa": "فارسی", "en": "English", "ar": "العربية", "fr": "Français",
    "de": "Deutsch", "es": "Español", "tr": "Türkçe", "ja": "日本語",
    "ko": "한국어", "zh": "中文", "ru": "Русский",
}

# Keep explicit models that are currently listed as free; Auto remains the safest
# long-term choice because OpenRouter selects among currently available free models.
FREE_MODELS = {
    "Auto (پیشنهادی)": "openrouter/free",
    "Nemotron 3 Ultra 550B (free)": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "Gemma 4 31B (free)": "google/gemma-4-31b-it:free",
    "Gemma 4 26B A4B (free)": "google/gemma-4-26b-a4b-it:free",
    "Nemotron 3.5 Lightning (free)": "nvidia/nemotron-3.5-lightning:free",
    "Dots 3 Note Preview (free)": "dots-studio/dots-3-note-preview:free",
    "Cohere North Mini Code (free)": "cohere/north-mini-code:free",
    "Nemotron 3.5 Super VL (free)": "nvidia/nemotron-3.5-super-vl:free",
}


SYSTEM_PROMPT = (
    "You are a professional literary translator. Translate completely and faithfully into the requested target language. "
    "Output only the translation. Preserve names, numbers, URLs, paragraph order, and meaning. "
    "Never summarize, omit, censor, or add commentary."
)
PERSIAN_RULES = " For Persian, use natural Persian punctuation, correct نیم‌فاصله, and Persian quotation marks «…». Avoid em dash and en dash."
TRANSLATE_PROMPT = "Translate to {lang}."
_thread_local = threading.local()


def _max_output_tokens(text: str) -> int:
    return max(4096, min(30000, math.ceil(len(text) * 1.15)))


class OpenRouterTranslator(BaseTranslator):
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key=None, model_name=None):
        self.api_key = api_key
        self.model_name = model_name or FREE_MODELS["Auto (پیشنهادی)"]
        self.last_error = ""
        self.last_error_kind = ""
        self.last_transport = ""
        self.last_usage = {}

    def get_token_limit(self):
        return 128_000

    def _headers(self) -> dict[str, str]:
        # Avoid inventing a public GitHub URL for a local application.
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"Trarium/{__version__}",
        }

    def translate(self, text, target_lang="fa", instructions=""):
        if not self.api_key:
            raise RuntimeError("OpenRouter API Key وارد نشده است.")
        text = str(text)
        lang = LANG_NAMES.get(target_lang, target_lang)
        system = SYSTEM_PROMPT + (PERSIAN_RULES if target_lang == "fa" else "")
        if instructions:
            system += " " + str(instructions).strip()
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": TRANSLATE_PROMPT.format(lang=lang) + "\n\n" + text},
            ],
            "max_tokens": _max_output_tokens(text),
            "temperature": 0.2,
        }
        self.last_error = ""
        self.last_error_kind = ""
        self.last_transport = ""
        self.last_usage = {}
        last_error = None
        for attempt in range(3):
            try:
                response = post_json(self.API_URL, self._headers(), payload, timeout=180)
                self.last_transport = response.transport
                data = response.data
                usage = data.get("usage") if isinstance(data, dict) else None
                self.last_usage = {"input_tokens": int((usage or {}).get("prompt_tokens") or 0), "output_tokens": int((usage or {}).get("completion_tokens") or 0)}
                choices = data.get("choices") if isinstance(data, dict) else None
                choices = choices or []
                if not choices:
                    raise RuntimeError("OpenRouter پاسخ خالی برگرداند.")
                content = choices[0].get("message", {}).get("content") if isinstance(choices[0], dict) else None
                if not content:
                    raise RuntimeError("OpenRouter متن ترجمه را برنگرداند.")
                return _clean_translation(str(content), target_lang)
            except OpenRouterSecurityBlockError as exc:
                self.last_error = str(exc)
                self.last_error_kind = exc.source
                # Security-policy blocks are deterministic for a given request/client.
                # Retrying them 2 more times only wastes time and can trigger more throttling.
                raise
            except Exception as exc:
                last_error = exc
                self.last_error = str(exc)
                self.last_error_kind = "request_error"
                if is_permanent_http_error(exc):
                    break
            if attempt < 2:
                time.sleep(1.5 * (2 ** attempt))
        raise RuntimeError(str(last_error or "خطای نامشخص OpenRouter"))
