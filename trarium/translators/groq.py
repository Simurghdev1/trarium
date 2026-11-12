"""Direct Groq translator using its OpenAI-compatible API."""
from __future__ import annotations
from trarium.translators.openai_compatible import OpenAICompatibleTranslator

GROQ_MODELS = {
    "GPT-OSS 120B": "openai/gpt-oss-120b",
    "GPT-OSS 20B": "openai/gpt-oss-20b",
    "Qwen 3.8 27B": "qwen/qwen3.8-27b",
}

class GroqTranslator(OpenAICompatibleTranslator):
    def __init__(self, api_key=None, model_name="openai/gpt-oss-120b"):
        super().__init__(api_key=api_key, model_name=model_name, base_url="https://api.groq.com/openai/v1", provider_name="Groq")
