"""Direct Google Gemini REST translator."""
from __future__ import annotations
import math, time, requests
from trarium.translators.base import BaseTranslator
from trarium.utils import clean_translation as _clean_translation

# Model ids verified against GET /v1beta/models with a real key; the previous
# list named models that the API does not serve at all.
GEMINI_MODELS = {
    "Gemini 3.5 Flash (رایگان)": "gemini-3.5-flash",
    "Gemini 3 Flash Preview (رایگان)": "gemini-3-flash-preview",
    "Gemini 2.5 Flash (رایگان)": "gemini-2.5-flash",
    "Gemini 2.5 Flash-Lite (رایگان)": "gemini-2.5-flash-lite",
    "Gemini Flash Latest (رایگان)": "gemini-flash-latest",
    "Gemini 2.5 Pro (ممکن است هزینه داشته باشد)": "gemini-2.5-pro",
}
LANG_NAMES = {"fa":"فارسی","en":"English","ar":"العربية","tr":"Türkçe","zh":"中文","ja":"日本語","ko":"한국어","ru":"Русский"}

# Only models that Google serves on the free tier are allowed by default. A
# paid model is never used silently: the caller has to opt in, which is what the
# UI does with its "allow paid" checkbox.
FREE_GEMINI_MODELS = {
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
}


class PaidModelBlocked(RuntimeError):
    """Raised when a model that may cost money is requested without consent."""


class GeminiTranslator(BaseTranslator):
    API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    def __init__(self, api_key=None, model_name="gemini-3.5-flash", allow_paid=False):
        if not api_key: raise RuntimeError("Google API Key وارد نشده است.")
        if model_name not in FREE_GEMINI_MODELS and not allow_paid:
            raise PaidModelBlocked(
                f"مدل «{model_name}» در فهرست مدل‌های رایگان تأییدشده نیست و ممکن است هزینه داشته باشد. "
                "اگر با هزینه موافقید، اجازهٔ استفاده از مدل پولی را فعال کنید؛ در غیر این صورت یکی از "
                f"مدل‌های رایگان را انتخاب کنید: {', '.join(sorted(FREE_GEMINI_MODELS))}"
            )
        self.allow_paid = bool(allow_paid)
        self.api_key = api_key
        self.model_name = model_name
    def is_free_model(self) -> bool:
        return self.model_name in FREE_GEMINI_MODELS
        self.last_transport = "requests"
        self.last_usage = {}
    def get_token_limit(self): return 1_048_576
    def translate(self, text, target_lang="fa"):
        lang = LANG_NAMES.get(target_lang, target_lang)
        system = "You are a professional literary translator. Translate completely and faithfully. Output only the translation. Preserve names, numbers, URLs, paragraph order, dialogue structure and meaning. Never summarize, omit, censor or add commentary."
        if target_lang == "fa": system += " Use natural Persian, correct نیم‌فاصله and «…». Avoid em dash and en dash."
        prompt = f"{system}\n\nTranslate to {lang}.\n\n{str(text)}"
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
