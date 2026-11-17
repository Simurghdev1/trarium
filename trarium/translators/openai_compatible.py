"""Generic OpenAI-compatible chat-completions translator."""
from __future__ import annotations
import math, time
import requests
from trarium.translators.base import BaseTranslator
from trarium.utils import clean_translation as _clean_translation
from trarium.prompt_rules import PERSIAN_NATURAL_ORDER

LANG_NAMES = {"fa":"فارسی","en":"English","ar":"العربية","fr":"Français","de":"Deutsch","es":"Español","tr":"Türkçe","ja":"日本語","ko":"한국어","zh":"中文","ru":"Русский"}
SYSTEM_PROMPT = ("You are a professional literary translator. Translate completely and faithfully into the requested target language. "
                 "Output only the translation. Preserve names, numbers, URLs, paragraph order, dialogue structure, and meaning. "
                 "Never summarize, omit, censor, or add commentary.")

class OpenAICompatibleTranslator(BaseTranslator):
    def __init__(self, api_key=None, model_name=None, base_url=None, provider_name="OpenAI-compatible"):
        self.api_key = api_key
        self.model_name = model_name or ""
        self.base_url = (base_url or "").rstrip("/")
        self.provider_name = provider_name
        if not self.api_key: raise RuntimeError(f"API Key برای {provider_name} وارد نشده است.")
        if not self.model_name: raise RuntimeError(f"مدل برای {provider_name} مشخص نشده است.")
        if not self.base_url: raise RuntimeError(f"Base URL برای {provider_name} مشخص نشده است.")
        self.last_transport = "requests"
        self.last_usage = {}

    def get_token_limit(self): return 131072

    def translate(self, text, target_lang="fa"):
        lang = LANG_NAMES.get(target_lang, target_lang)
        system = SYSTEM_PROMPT + (" Use natural Persian, correct نیم‌فاصله and Persian quotation marks «…». Avoid em dash and en dash." + " " + PERSIAN_NATURAL_ORDER if target_lang == "fa" else "")
        hint = getattr(self, "style_hint", "")
        if hint:
            system += " " + hint
        payload = {"model": self.model_name, "messages":[{"role":"system","content":system},{"role":"user","content":f"Translate to {lang}.\n\n{str(text)}"}],"temperature":0.2,"max_tokens":max(4096,min(30000,math.ceil(len(str(text))*1.15)))}
        last = None
        for attempt in range(3):
            try:
                r = requests.post(self.base_url + "/chat/completions", headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json","User-Agent":"Trarium/2.17.0"}, json=payload, timeout=180)
                if r.status_code >= 400: raise RuntimeError(f"{self.provider_name} HTTP {r.status_code}: {r.text[:600]}")
                data = r.json()
                usage = data.get("usage") or {}
                self.last_usage = {"input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0), "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)}
                choices = data.get("choices") or []
                content = choices[0].get("message",{}).get("content") if choices else None
                if not content: raise RuntimeError(f"{self.provider_name} پاسخ متنی خالی برگرداند.")
                return _clean_translation(str(content))
            except Exception as exc:
                last = exc
                if attempt < 2: time.sleep(1.5*(2**attempt))
        raise RuntimeError(f"خطا در ترجمه {self.provider_name}: {last}")


    def test_connection(self):
        """Small authenticated request used by the UI diagnostics."""
        payload = {"model": self.model_name, "messages":[{"role":"user","content":"Reply only with OK"}],"max_tokens":8,"temperature":0}
        r = requests.post(self.base_url + "/chat/completions", headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json","User-Agent":"Trarium/2.17.0"}, json=payload, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"{self.provider_name} HTTP {r.status_code}: {r.text[:600]}")
        data=r.json(); choices=data.get("choices") or []
        content=choices[0].get("message",{}).get("content") if choices else None
        if not content: raise RuntimeError(f"{self.provider_name} پاسخ خالی برگرداند.")
        return str(content)
