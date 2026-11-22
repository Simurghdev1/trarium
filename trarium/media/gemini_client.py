"""کلاینت کوچک REST برای Gemini با چند کلید و جابه‌جایی خودکار.

قانون: کلید اول تا وقتی سهمیه‌اش تمام نشده استفاده می‌شود، بعد کلید دوم و…
خطای «در دقیقه» (RPM) با صبر به اندازهٔ retryDelay حل می‌شود؛ خطای سهمیهٔ روزانه
یا RESOURCE_EXHAUSTED بدون retryDelay کلید را تا آخر این اجرا کنار می‌گذارد.
کلیدها هیچ‌جا چاپ یا ذخیره نمی‌شوند.
"""
from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path

import requests

API = "https://generativelanguage.googleapis.com"


class GeminiError(RuntimeError):
    pass


class QuotaExhausted(GeminiError):
    pass


def keys_from_env() -> list[str]:
    keys: list[str] = []
    for name in ("TRARIUM_GEMINI_KEYS", "GEMINI_API_KEYS"):
        keys += [k.strip() for k in re.split(r"[,\s]+", os.environ.get(name, "")) if k.strip()]
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY_2"):
        if os.environ.get(name):
            keys.append(os.environ[name].strip())
    return list(dict.fromkeys(keys))


def classify_429(body: str) -> tuple[str, float]:
    """('minute', ثانیهٔ صبر) یا ('exhausted', 0)."""
    delay = 0.0
    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body or "")
    if match:
        delay = float(match.group(1))
    lowered = (body or "").lower()
    if "perday" in lowered or "per_day" in lowered or "daily" in lowered or "limit: 0" in lowered:
        return "exhausted", 0.0
    if "perminute" in lowered or "per_minute" in lowered or (delay and delay <= 90):
        return "minute", min(max(delay, 5.0), 90.0)
    return "exhausted", 0.0


class GeminiClient:
    def __init__(self, keys: list[str] | None = None, max_wait: float = 240.0, log=None):
        self.keys = [k for k in (keys if keys is not None else keys_from_env()) if k]
        if not self.keys:
            raise GeminiError("کلید Gemini تنظیم نشده (TRARIUM_GEMINI_KEYS یا GEMINI_API_KEY).")
        self.index = 0
        self.max_wait = max_wait
        self.calls = 0
        self.log = log or (lambda msg: None)

    @property
    def key(self) -> str:
        return self.keys[self.index]

    def _next_key(self, reason: str) -> None:
        if self.index + 1 >= len(self.keys):
            raise QuotaExhausted(f"سهمیهٔ همهٔ کلیدهای Gemini تمام شد ({reason}).")
        self.index += 1
        self.log(f"سهمیهٔ کلید {self.index} تمام شد؛ کلید {self.index + 1} استفاده می‌شود.")

    def request(self, method: str, path: str, *, json_body=None, data=None, headers=None,
                timeout: int = 300, raw: bool = False):
        waited = 0.0
        while True:
            hdr = {"x-goog-api-key": self.key, "User-Agent": "Trarium-media/1.0"}
            hdr.update(headers or {})
            url = path if path.startswith("http") else API + path
            self.calls += 1
            resp = requests.request(method, url, headers=hdr, json=json_body, data=data, timeout=timeout)
            if resp.status_code == 429 or (resp.status_code == 403 and "quota" in resp.text.lower()):
                kind, delay = classify_429(resp.text)
                if kind == "minute" and waited + delay <= self.max_wait:
                    self.log(f"محدودیت دقیقه‌ای Gemini؛ {delay:.0f} ثانیه صبر.")
                    time.sleep(delay)
                    waited += delay
                    continue
                self._next_key("429")
                waited = 0.0
                continue
            if resp.status_code in (400, 401, 403) and "API key" in resp.text and self.index + 1 < len(self.keys):
                self._next_key("کلید نامعتبر")
                continue
            if resp.status_code >= 500 and waited < 30:
                time.sleep(4)
                waited += 4
                continue
            if resp.status_code >= 400:
                raise GeminiError(f"Gemini HTTP {resp.status_code}: {resp.text[:400]}")
            return resp if raw else resp.json()

    # ------------------------------------------------------------ helpers
    def generate(self, model: str, contents: list, system: str = "", config: dict | None = None,
                 timeout: int = 300) -> dict:
        body: dict = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if config:
            body["generationConfig"] = config
        return self.request("POST", f"/v1beta/models/{model}:generateContent", json_body=body, timeout=timeout)

    def audio_part(self, path: str | Path, mime: str) -> dict:
        """فایل کوچک inline، بزرگ‌تر از ۱۴ مگابایت با Files API."""
        path = Path(path)
        size = path.stat().st_size
        if size <= 14 * 1024 * 1024:
            return {"inlineData": {"mimeType": mime, "data": base64.b64encode(path.read_bytes()).decode()}}
        start = self.request("POST", "/upload/v1beta/files", raw=True, headers={
            "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size), "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json"}, json_body={"file": {"display_name": path.name}})
        upload_url = start.headers.get("x-goog-upload-url") or start.headers.get("X-Goog-Upload-URL")
        if not upload_url:
            raise GeminiError("آپلود فایل به Gemini شروع نشد.")
        info = self.request("POST", upload_url, data=path.read_bytes(), timeout=900, headers={
            "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"})
        file = info.get("file") or {}
        name = file.get("name", "")
        for _ in range(60):
            if file.get("state") in (None, "ACTIVE"):
                break
            time.sleep(2)
            file = self.request("GET", f"/v1beta/{name}")
        return {"fileData": {"mimeType": mime, "fileUri": file.get("uri")}}

    def ephemeral_token(self, minutes: int = 30) -> str:
        """توکن یک‌بارمصرف برای Live API (کلید اصلی هرگز وارد WebSocket نمی‌شود)."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        data = self.request("POST", "/v1beta/auth_tokens", json_body={
            "uses": 1, "expireTime": (now + timedelta(minutes=minutes)).strftime(fmt),
            "newSessionExpireTime": (now + timedelta(minutes=2)).strftime(fmt)})
        return data["name"]


def response_text(data: dict) -> str:
    out = []
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            if part.get("text") and not part.get("thought"):
                out.append(part["text"])
    return "".join(out)


def response_audio(data: dict) -> tuple[bytes, str]:
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"]), inline.get("mimeType", "")
    return b"", ""
