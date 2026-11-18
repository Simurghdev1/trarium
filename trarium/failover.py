"""Provider/model failover with quota awareness and a local audit trail.

Three modes are supported and can be combined:

* provider fallback — the next configured provider handles the request;
* model fallback — another model of the same provider is tried;
* smart routing — providers are tried by remaining quota and cost first.

Permanent errors (bad request, auth, unknown model) are never retried in a
loop. An uncertain transport failure is not resent to the same endpoint,
because the request may already have been billed and served.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from trarium.usage import KIND_LLM, UsageStore, default_store

RATE_LIMIT = "rate_limit"
QUOTA = "quota"
AUTH = "auth"
PERMANENT = "permanent"
TRANSIENT = "transient"
NETWORK = "network"


def classify_error(exc: Exception | str) -> str:
    text = str(exc or "").lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429 or any(word in text for word in ("429", "rate limit", "too many requests", "rate_limit")):
        return RATE_LIMIT
    if any(word in text for word in ("quota", "insufficient", "credit", "billing", "exceeded your current")):
        return QUOTA
    if status in (401, 403) or any(word in text for word in ("401", "403", "unauthorized", "invalid api key", "api key", "forbidden", "permission")):
        return AUTH
    if any(word in text for word in ("404", "model not found", "no such model", "does not exist", "unsupported model", "deactivated")):
        return PERMANENT
    if any(word in text for word in ("400", "invalid request", "bad request", "validation", "context length", "payload")):
        return PERMANENT
    if any(word in text for word in ("timeout", "timed out", "connection", "network", "unreachable", "reset by peer", "ssl", "proxy")):
        return NETWORK
    if any(word in text for word in ("500", "502", "503", "504", "server error", "overloaded", "unavailable", "temporarily")):
        return TRANSIENT
    return TRANSIENT


@dataclass
class Endpoint:
    provider: str
    model: str
    translator: object
    label: str = ""
    quality: int = 50
    cooldown_until: float = 0.0
    disabled_reason: str = ""

    def name(self) -> str:
        return self.label or f"{self.provider}:{self.model}".rstrip(":")


@dataclass
class FailoverEvent:
    time: float
    from_endpoint: str
    to_endpoint: str
    reason: str
    kind: str


class FailoverError(RuntimeError):
    """Raised when every endpoint is exhausted or permanently unavailable."""


class SmartFailoverTranslator:
    def __init__(self, endpoints: list, *, retries: int = 2, cost_cap_usd: float | None = None,
                 smart: bool = False, cooldown_seconds: float = 45.0,
                 store: UsageStore | None = None, job_id: str = "", on_event=None):
        if not endpoints:
            raise ValueError("حداقل یک ارائه‌دهنده برای Failover لازم است.")
        self.endpoints: list[Endpoint] = [
            item if isinstance(item, Endpoint) else Endpoint(
                provider=getattr(item, "provider", "unknown"),
                model=getattr(item, "model", "") or getattr(item, "model_name", ""),
                translator=item,
            )
            for item in endpoints
        ]
        self.retries = max(0, int(retries))
        self.cost_cap_usd = max(0.0, float(cost_cap_usd)) if cost_cap_usd else None
        self.smart = bool(smart)
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.store = store or default_store()
        self.job_id = job_id
        self.on_event = on_event
        self.provider = "failover"
        self.model = "chain"
        self.last_provider = ""
        self.last_model = ""
        self.last_usage: dict = {}
        self.last_error = ""
        self.history: list[FailoverEvent] = []
        self.requests = 0

    # ------------------------------------------------------------------ helpers
    def get_token_limit(self) -> int:
        limits = []
        for endpoint in self.endpoints:
            getter = getattr(endpoint.translator, "get_token_limit", None)
            if callable(getter):
                try:
                    limits.append(int(getter()))
                except Exception:
                    pass
        return max(limits or [8000])

    def _cost_cap_reached(self) -> bool:
        if not self.cost_cap_usd:
            return False
        try:
            spent = self.store.summary("month", kind=KIND_LLM)["cost_usd"]
        except Exception:
            return False
        return spent >= self.cost_cap_usd

    def _quota_block(self, endpoint: Endpoint) -> str:
        try:
            status = self.store.limit_status(endpoint.provider, KIND_LLM)
        except Exception:
            return ""
        if not status["ok"]:
            return "سهمیه محلی مصرف شده: " + "، ".join(status["exceeded"])
        return ""

    def _ordered(self) -> list[Endpoint]:
        now = time.monotonic()
        available = [e for e in self.endpoints if e.cooldown_until <= now and not e.disabled_reason]
        cooling = [e for e in self.endpoints if e.cooldown_until > now]
        if self.smart and available:
            def score(endpoint: Endpoint):
                try:
                    month = self.store.summary("month", provider=endpoint.provider, kind=KIND_LLM)
                    used = month["total_tokens"]
                except Exception:
                    used = 0
                # Prefer strong quality, then the provider with less local usage.
                return (-endpoint.quality + min(used / 5_000_000, 4.0))
            available = sorted(available, key=score)
        return available + cooling

    def _cool(self, endpoint: Endpoint, kind: str) -> None:
        long_block = kind in {AUTH, PERMANENT}
        endpoint.cooldown_until = time.monotonic() + (3600 if long_block else self.cooldown_seconds)
        if kind in {AUTH, PERMANENT}:
            endpoint.disabled_reason = "خطای دائمی؛ تا اصلاح تنظیمات غیرفعال است"

    def _record(self, endpoint: Endpoint, target: Endpoint | None, reason: str, kind: str) -> None:
        event = FailoverEvent(time.time(), endpoint.name(), target.name() if target else "",
                              reason[:400], kind)
        self.history.append(event)
        try:
            self.store.log_failover(endpoint.provider, endpoint.model,
                                    target.provider if target else "", target.model if target else "",
                                    f"[{kind}] {reason}", self.job_id)
        except Exception:
            pass
        if callable(self.on_event):
            try:
                self.on_event(event)
            except Exception:
                pass

    # ------------------------------------------------------------------- public
    def translate(self, text: str, target_lang: str = "fa") -> str:
        if self._cost_cap_reached():
            raise FailoverError(f"سقف هزینهٔ تعیین‌شده (${self.cost_cap_usd:.2f}) پر شده است؛ ترجمه متوقف شد.")
        last_error: Exception | None = None
        attempted: set[int] = set()
        ordered = self._ordered()
        for position, endpoint in enumerate(ordered):
            if id(endpoint) in attempted:
                continue
            attempted.add(id(endpoint))
            blocked = self._quota_block(endpoint)
            if blocked:
                self._record(endpoint, ordered[position + 1] if position + 1 < len(ordered) else None,
                             blocked, QUOTA)
                last_error = FailoverError(blocked)
                continue
            next_endpoint = next((e for e in ordered[position + 1:] if id(e) not in attempted), None)
            for attempt in range(self.retries + 1):
                try:
                    output = endpoint.translator.translate(text, target_lang)
                    if not str(output or "").strip():
                        raise RuntimeError("پاسخ خالی از ارائه‌دهنده")
                    self.last_provider = endpoint.provider
                    self.last_model = endpoint.model
                    self.last_usage = getattr(endpoint.translator, "last_usage", {}) or {}
                    self.last_error = ""
                    self.requests += 1
                    endpoint.cooldown_until = 0.0
                    return str(output)
                except Exception as exc:
                    last_error = exc
                    kind = classify_error(exc)
                    self.last_error = str(exc)
                    # Permanent/auth/network errors never loop on the same endpoint.
                    if kind in {PERMANENT, AUTH, NETWORK, QUOTA, RATE_LIMIT}:
                        self._cool(endpoint, kind)
                        self._record(endpoint, next_endpoint, str(exc), kind)
                        break
                    if attempt >= self.retries:
                        self._cool(endpoint, kind)
                        self._record(endpoint, next_endpoint, str(exc), kind)
                        break
                    time.sleep(min(6.0, 0.7 * (2 ** attempt)))
        raise FailoverError("همهٔ ارائه‌دهنده‌های زنجیره ناموفق بودند: " + str(last_error or "خطای نامشخص"))


def build_chain(provider_chain: list[dict], *, retries: int = 2, cost_cap_usd: float | None = None,
                smart: bool = False, project: str = "", job_id: str = "",
                on_usage=None, store: UsageStore | None = None) -> object:
    """Build a metered translator chain from ``[{provider, model}, ...]``.

    A single endpoint is returned as a plain metered translator so existing
    behaviour (and cost) stays exactly the same when failover is off.
    """
    from trarium.provider_registry import build_translator, label
    from trarium.usage import KIND_LLM, UsageTrackingTranslator

    endpoints: list[Endpoint] = []
    seen: set[tuple[str, str]] = set()
    for item in provider_chain:
        provider = str(item.get("provider") or "").strip().lower()
        if not provider:
            continue
        model = str(item.get("model") or "").strip()
        key = (provider, model)
        if key in seen:
            continue
        seen.add(key)
        translator = build_translator(provider, model)
        metered = UsageTrackingTranslator(
            translator, provider=provider,
            model=model or getattr(translator, "model_name", ""),
            kind=KIND_LLM, project=project, job_id=job_id, store=store, on_usage=on_usage,
        )
        endpoints.append(Endpoint(provider=provider, model=metered.model,
                                  translator=metered, label=label(provider), quality=70))
    if not endpoints:
        raise FailoverError("هیچ ارائه‌دهندهٔ قابل استفاده‌ای در زنجیره تنظیم نشده است.")
    if len(endpoints) == 1 and not smart:
        return endpoints[0].translator
    return SmartFailoverTranslator(endpoints, retries=retries, cost_cap_usd=cost_cap_usd,
                                  smart=smart, store=store, job_id=job_id)
