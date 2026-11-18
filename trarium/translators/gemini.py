"""Direct Google Gemini REST translator."""
from __future__ import annotations
import math, time, requests
from trarium.translators.base import BaseTranslator
from trarium.utils import clean_translation as _clean_translation

GEMINI_MODELS = {
    "Gemini 3.8 Flash (جدید)": "gemini-3.8-flash",
    "Gemini 3.7 Flash": "gemini-3.7-flash",
    "Gemini 3.6 Flash": "gemini-3.6-flash",
    "Gemini 3.5 Flash": "gemini-3.5-flash",
}
LANG_NAMES = {"fa":"فارسی","en":"English","ar":"العربية","tr":"Türkçe","zh":"中文","ja":"日本語","ko":"한국어","ru":"Русский"}

class GeminiTranslator(BaseTranslator):
    API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    def __init__(self, api_key=None, model_name="gemini-3.8-flash"):
        if not api_key: raise RuntimeError("Google API Key وارد نشده است.")
        self.api_key = api_key
        self.model_name = model_name
        self.last_transport = "requests"
        self.last_usage = {}
    def get_token_limit(self): return 1_048_576
    def translate(self, text, target_lang="fa"):
        lang = LANG_NAMES.get(target_lang, target_lang)
        system = "You are a professional literary translator. Translate completely and faithfully. Output only the translation. Preserve names, numbers, URLs, paragraph order, dialogue structure and meaning. Never summarize, omit, censor or add commentary."
        if target_lang == "fa": system += " Use natural Persian, correct نیم‌فاصله and «…». Avoid em dash and en dash."
        payload = {"systemInstruction":{"parts":[{"text":system}]},"contents":[{"parts":[{"text":f"Translate to {lang}.\n\n{str(text)}"}]}],"generationConfig":{"maxOutputTokens":max(4096,min(30000,math.ceil(len(str(text))*1.15)))}}
        last = None
        for attempt in range(3):
            try:
                url = self.API.format(model=self.model_name)
                r = requests.post(url, headers={"Content-Type":"application/json","x-goog-api-key":self.api_key,"User-Agent":"Trarium/2.17.0"}, json=payload, timeout=180)
                if r.status_code >= 400: raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:600]}")
                data = r.json(); usage = data.get("usageMetadata") or {}
                self.last_usage = {"input_tokens": int(usage.get("promptTokenCount") or 0), "output_tokens": int(usage.get("candidatesTokenCount") or 0)}
                candidates = data.get("candidates") or []
                text_out = ""
                if candidates:
                    for part in (candidates[0].get("content",{}).get("parts") or []):
                        if part.get("text"): text_out += part["text"]
                if not text_out: raise RuntimeError("Gemini پاسخ متنی خالی برگرداند.")
                return _clean_translation(text_out)
            except Exception as exc:
                last = exc
                if attempt < 2: time.sleep(1.5*(2**attempt))
        raise RuntimeError(f"خطا در ترجمه Gemini: {last}")
