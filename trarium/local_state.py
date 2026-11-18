"""Private local state: one folder for settings, jobs and usage.

Everything lives under ``~/.trarium`` by default. ``TRARIUM_HOME`` overrides
that folder so tests and portable installs never touch the real home.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_LOCK = threading.RLock()


def trarium_home() -> Path:
    override = os.environ.get("TRARIUM_HOME", "").strip()
    base = Path(override).expanduser() if override else (Path.home() / ".trarium")
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure_dir(path: str | Path) -> Path:
    value = Path(path)
    value.mkdir(parents=True, exist_ok=True)
    return value


def settings_path() -> Path:
    return trarium_home() / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    with _LOCK:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict) -> None:
    path = settings_path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)


def _provider_bucket(provider: str, create: bool = False) -> dict:
    provider = (provider or "").strip()
    data = load_settings()
    providers = data.setdefault("providers", {}) if create else data.get("providers", {})
    if not isinstance(providers, dict):
        providers = {}
    bucket = providers.setdefault(provider, {}) if create else providers.get(provider, {})
    return bucket if isinstance(bucket, dict) else {}


def get_provider_config(provider: str) -> dict:
    """Return the saved credentials for one provider (never echoed in the UI)."""
    bucket = _provider_bucket(provider)
    return {
        "api_key": str(bucket.get("api_key") or ""),
        "model": str(bucket.get("model") or ""),
        "base_url": str(bucket.get("base_url") or ""),
    }


def save_provider_config(provider: str, *, api_key: str | None = None,
                         model: str | None = None, base_url: str | None = None) -> None:
    provider = (provider or "").strip()
    if not provider:
        return
    with _LOCK:
        data = load_settings()
        providers = data.setdefault("providers", {})
        bucket = providers.setdefault(provider, {})
        if api_key is not None:
            bucket["api_key"] = str(api_key)
        if model is not None:
            bucket["model"] = str(model)
        if base_url is not None:
            bucket["base_url"] = str(base_url)
        save_settings(data)


def mask_secret(value: str, keep: int = 4) -> str:
    """Show only enough of a key to recognise it."""
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= keep + 2:
        return "•" * len(value)
    return "•" * min(12, max(4, len(value) - keep)) + value[-keep:]


def split_chain_text(text: str) -> list[dict]:
    """Parse the compact ``provider:model`` failover editor.

    One entry per line; blank lines and ``#`` comments are ignored. Models are
    optional, so ``gemini`` is valid too.
    """
    chain: list[dict] = []
    for raw in str(text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        provider, _, model = line.partition(":")
        provider = provider.strip().lower()
        if not provider:
            continue
        chain.append({"provider": provider, "model": model.strip()})
    return chain


def chain_to_text(chain: list[dict]) -> str:
    return "\n".join(
        f"{str(item.get('provider', '')).strip()}:{str(item.get('model', '')).strip()}".rstrip(":")
        for item in chain or [] if item.get("provider")
    )
