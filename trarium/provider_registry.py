"""Single place that builds a translator from a provider name and model.

The Streamlit page, the background job manager and the failover chain all use
this factory, so credentials and model defaults cannot drift apart.
"""
from __future__ import annotations

import os

from trarium.local_state import get_provider_config, save_provider_config
from trarium.translators import get_translator
from trarium.translators.gemini import GEMINI_MODELS
from trarium.translators.groq import GROQ_MODELS
from trarium.translators.openrouter import FREE_MODELS
from trarium.translators.router9 import MODELS_9ROUTER

PROVIDERS = ["openrouter", "gemini", "groq", "router9", "openai_compatible"]
LABELS = {
    "openrouter": "OpenRouter",
    "gemini": "Google Gemini",
    "groq": "Groq",
    "router9": "9Router",
    "openai_compatible": "OpenAI-compatible",
}
MODEL_CHOICES = {
    "openrouter": FREE_MODELS,
    "gemini": GEMINI_MODELS,
    "groq": GROQ_MODELS,
    "router9": MODELS_9ROUTER,
    "openai_compatible": {},
}


def label(provider: str) -> str:
    return LABELS.get(provider, provider)


def default_model(provider: str) -> str:
    choices = MODEL_CHOICES.get(provider) or {}
    if not choices:
        return ""
    return list(choices.values())[0]


ENV_KEYS = {
    "openrouter": ("OPENROUTER_API_KEY",),
    "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "groq": ("GROQ_API_KEY",),
    "openai_compatible": ("TRARIUM_API_KEY",),
}


def env_key(provider: str) -> str:
    """Read a key from the environment so CLI/background runs match the UI."""
    for name in ENV_KEYS.get(provider, ()):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def has_credentials(provider: str) -> bool:
    if provider == "router9":
        return True
    saved = get_provider_config(provider)
    if provider == "openai_compatible":
        key = saved["api_key"] or env_key(provider)
        base = saved["base_url"] or os.environ.get("TRARIUM_BASE_URL", "").strip()
        model = saved["model"] or os.environ.get("TRARIUM_MODEL", "").strip()
        return bool(key and base and model)
    return bool(saved["api_key"] or env_key(provider))


def build_translator(provider: str, model: str = "", *, api_key: str | None = None,
                     base_url: str | None = None, provider_name: str | None = None):
    """Build a translator, falling back to the locally saved credentials."""
    provider = (provider or "openrouter").strip().lower()
    saved = get_provider_config(provider)
    key = saved["api_key"] if api_key is None else api_key
    if not key:
        key = env_key(provider)
    base_url = base_url or os.environ.get("TRARIUM_BASE_URL", "").strip() or None
    provider_name = provider_name or "OpenAI-compatible"
    chosen_model = (model or saved["model"] or default_model(provider)).strip()
    if provider == "router9":
        return get_translator("router9", api_key=key or "9router", model_name=chosen_model or "auto")
    if provider == "openai_compatible":
        return get_translator(
            "openai_compatible", api_key=key, model_name=chosen_model,
            base_url=(saved["base_url"] or (base_url or "")),
            provider_name=provider_name,
        )
    return get_translator(provider, api_key=key, model_name=chosen_model)


def remember(provider: str, *, api_key: str | None = None, model: str | None = None,
             base_url: str | None = None) -> None:
    save_provider_config(provider, api_key=api_key, model=model, base_url=base_url)


def chain_from_settings() -> list[dict]:
    from trarium.local_state import load_settings, split_chain_text
    chain = load_settings().get("failover_chain")
    if isinstance(chain, list) and chain:
        return [item for item in chain if isinstance(item, dict) and item.get("provider")]
    return split_chain_text(str(load_settings().get("failover_chain_text") or ""))
