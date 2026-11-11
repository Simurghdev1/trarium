"""ترجمه متن با Google Gemini."""
import math
import time
from google import genai
from trarium.translators.base import BaseTranslator
from trarium.utils import clean_translation as _clean_translation

LANG_NAMES = {
    "fa": "فارسی", "en": "انگلیسی", "ar": "عربی", "fr": "فرانسوی",
    "de": "آلمانی", "es": "اسپانیایی", "tr": "ترکی", "ja": "ژاپنی",
    "ko": "کره‌ای", "zh": "چینی", "ru": "روسی",
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


class GeminiTranslator(BaseTranslator):
    def __init__(self, api_key=None, model_name="gemini-2.5-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def get_token_limit(self):
        return 1_000_000

    def translate(self, text, target_lang="fa"):
        lang_name = LANG_NAMES.get(target_lang, target_lang)
        user_msg = TRANSLATE_PROMPT.format(lang=lang_name) + "\n\n" + text
        for attempt in range(3):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=user_msg,
                    config={
                        "temperature": 0.3,
                        "max_output_tokens": max(2048, min(20000, math.ceil(len(text) * 0.75))),
                        "system_instruction": _system_prompt(target_lang),
                    },
                )
                output = getattr(response, "text", None)
                if not output:
                    raise RuntimeError("Gemini پاسخ متنی خالی برگرداند")
                return _clean_translation(output.strip())
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"خطا در ترجمه Gemini: {e}")
