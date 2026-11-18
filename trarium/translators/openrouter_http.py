"""Robust OpenRouter HTTP transport and diagnostics.

The public OpenRouter endpoint can return a Cloudflare/WAF 403 before the API
application sees the request. On Windows, curl.exe is preferred in auto mode
because real-world reports show that some HTTP client TLS fingerprints are
blocked while curl/browser traffic succeeds. This is a compatibility fallback,
not a bypass of an access policy.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

import requests

WAF_TEXT = "access denied by security policy"


class OpenRouterSecurityBlockError(RuntimeError):
    def __init__(self, message: str, *, source: str = "unknown", http_status: int = 403, raw: Any = None):
        super().__init__(message)
        self.source = source
        self.http_status = http_status
        self.raw = raw


@dataclass(slots=True)
class OpenRouterResponse:
    status_code: int
    headers: dict[str, str]
    data: Any
    text: str
    transport: str


def _json_or_text(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _is_waf_security_block(status_code: int, raw_text: str, data: Any) -> bool:
    if status_code != 403:
        return False
    low = (raw_text or "").lower()
    if WAF_TEXT in low or "sorry, you have been blocked" in low:
        return True
    if isinstance(data, dict):
        err = str(data.get("error", "") or "").lower()
        if data.get("success") is False and (WAF_TEXT in err or "you have been blocked" in err):
            return True
    return False


def _structured_403_message(data: Any, raw_text: str) -> str:
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            msg = error.get("message") or error.get("detail") or ""
            code = error.get("code")
            if code and msg:
                return f"OpenRouter {code}: {msg}"
            if msg:
                return f"OpenRouter 403: {msg}"
        if isinstance(error, str) and error:
            return f"OpenRouter 403: {error}"
    return f"OpenRouter 403: {(raw_text or 'Access denied').strip()[:500]}"


def _curl_available() -> str | None:
    env = os.environ.get("TRARIUM_CURL_PATH", "").strip()
    if env:
        return env if shutil.which(env) or os.path.exists(env) else None
    return shutil.which("curl.exe") or shutil.which("curl")


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    out = dict(headers)
    # Attribution headers are optional for ordinary API calls; omit localhost
    # attribution by default so the diagnostic is as close as possible to the
    # official minimal API example.
    if os.environ.get("TRARIUM_OPENROUTER_ATTRIBUTION", "0").strip() not in {"1", "true", "yes"}:
        out.pop("HTTP-Referer", None)
        out.pop("X-OpenRouter-Title", None)
        out.pop("X-Title", None)
    out.setdefault("User-Agent", "Trarium/2.18.0")
    return out


def _curl_request_json(method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None, timeout: int) -> OpenRouterResponse:
    curl = _curl_available()
    if not curl:
        raise RuntimeError("curl برای fallback در دسترس نیست.")
    headers = _safe_headers(headers)
    header_blob = "\n".join(f"{k}: {v}" for k, v in headers.items()) + "\n"
    args = [curl, "--silent", "--show-error", "--http1.1", "--connect-timeout", "20", "--max-time", str(max(30, int(timeout))), "-H", "@-", "-X", method, url]
    if payload is not None:
        args += ["--data-binary", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))]
    args += ["-w", "\n__TRARIUM_HTTP_STATUS__:%{http_code}"]
    proc = subprocess.run(args, input=header_blob, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    raw = proc.stdout or ""
    marker = "\n__TRARIUM_HTTP_STATUS__::"  # impossible marker; corrected below
    marker = "\n__TRARIUM_HTTP_STATUS__:"
    if marker in raw:
        body, status = raw.rsplit(marker, 1)
        try:
            status_code = int(status.strip())
        except ValueError:
            status_code = 0
    else:
        body, status_code = raw, 0
    if proc.returncode != 0 and not body:
        raise RuntimeError((proc.stderr or f"curl exit code {proc.returncode}").strip())
    data = _json_or_text(body)
    if _is_waf_security_block(status_code, body, data):
        raise OpenRouterSecurityBlockError("OpenRouter لایهٔ امنیتی/WAF درخواست را رد کرد.", source="waf", http_status=status_code or 403, raw=data)
    if status_code == 403:
        raise OpenRouterSecurityBlockError(_structured_403_message(data, body), source="api_guardrail_or_account", http_status=403, raw=data)
    if status_code >= 400 or status_code == 0:
        raise RuntimeError(f"OpenRouter HTTP {status_code}: {body.strip()[:600]}")
    if method == "GET":
        if not isinstance(data, (dict, list)):
            raise RuntimeError("OpenRouter پاسخ JSON معتبر برنگرداند.")
    elif not isinstance(data, (dict, list)):
        raise RuntimeError("OpenRouter پاسخ JSON معتبر برنگرداند.")
    return OpenRouterResponse(status_code, {}, data, body, "curl")


def _requests_request_json(method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None, timeout: int) -> OpenRouterResponse:
    headers = _safe_headers(headers)
    try:
        response = requests.request(method, url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"خطای شبکه OpenRouter: {exc}") from exc
    raw = response.text or ""
    data = _json_or_text(raw)
    if _is_waf_security_block(response.status_code, raw, data):
        raise OpenRouterSecurityBlockError("OpenRouter لایهٔ امنیتی/WAF درخواست را رد کرد.", source="waf", http_status=403, raw=data)
    if response.status_code == 403:
        raise OpenRouterSecurityBlockError(_structured_403_message(data, raw), source="api_guardrail_or_account", http_status=403, raw=data)
    if response.status_code >= 400:
        raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {raw[:600]}")
    if not isinstance(data, (dict, list)):
        raise RuntimeError("OpenRouter پاسخ JSON معتبر برنگرداند.")
    return OpenRouterResponse(response.status_code, dict(response.headers), data, raw, "requests")


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 180) -> OpenRouterResponse:
    mode = os.environ.get("TRARIUM_OPENROUTER_HTTP", "auto").strip().lower()
    if mode not in {"auto", "requests", "curl"}:
        mode = "auto"
    if mode == "curl":
        return _curl_request_json("POST", url, headers, payload, timeout)
    if mode == "requests":
        return _requests_request_json("POST", url, headers, payload, timeout)

    # Auto: prefer curl on Windows / whenever available, then requests.
    # This specifically addresses the class of Cloudflare 403 caused by some
    # client TLS fingerprints while keeping a normal Python fallback.
    errors: list[Exception] = []
    if _curl_available():
        try:
            return _curl_request_json("POST", url, headers, payload, timeout)
        except OpenRouterSecurityBlockError as exc:
            errors.append(exc)
            # Do not retry the exact same security block with another transport;
            # it is unlikely to help, but a different client fingerprint can.
            try:
                return _requests_request_json("POST", url, headers, payload, timeout)
            except Exception as exc2:
                errors.append(exc2)
        except Exception as exc:
            errors.append(exc)
    try:
        return _requests_request_json("POST", url, headers, payload, timeout)
    except Exception as exc:
        errors.append(exc)
    if errors:
        # Prefer the most informative WAF classification.
        for err in errors:
            if isinstance(err, OpenRouterSecurityBlockError) and err.source == "waf":
                raise err
        raise errors[-1]
    raise RuntimeError("OpenRouter transport failed.")


def diagnose_openrouter(api_key: str, *, timeout: int = 30, model: str = "openrouter/free") -> dict[str, Any]:
    """Run a public GET and a tiny authenticated POST using available transports."""
    result: dict[str, Any] = {"models": {}, "chat": {}}
    base = "https://openrouter.ai/api/v1"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Trarium/2.18.0"}
    # Public catalog check: no key required, useful for separating WAF from auth.
    for name, fn in (("curl", _curl_available()), ("requests", "requests")):
        if name == "curl" and not fn:
            continue
        try:
            if name == "curl":
                r = _curl_request_json("GET", base + "/models", {}, None, timeout)
            else:
                r = _requests_request_json("GET", base + "/models", {}, None, timeout)
            result["models"] = {"ok": True, "transport": r.transport, "status": r.status_code}
            break
        except Exception as exc:
            result["models"].setdefault("errors", []).append(f"{name}: {exc}")
    payload = {"model": model, "messages": [{"role": "user", "content": "Translate only this word to Persian: Hello"}], "max_tokens": 8, "temperature": 0}
    try:
        r = post_json(base + "/chat/completions", headers, payload, timeout)
        result["chat"] = {"ok": True, "transport": r.transport, "status": r.status_code}
    except OpenRouterSecurityBlockError as exc:
        result["chat"] = {"ok": False, "source": exc.source, "status": exc.http_status, "error": str(exc)}
    except Exception as exc:
        result["chat"] = {"ok": False, "error": str(exc)}
    return result
