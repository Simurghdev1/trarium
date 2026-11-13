"""Local OpenAI-compatible mock provider for real end-to-end tests.

It speaks the same HTTP protocol as the real providers (POST /chat/completions),
counts tokens with tiktoken, and can simulate failure modes. Tests exercise the
real transport, retry and parsing code paths — no API key or network needed.
"""
from __future__ import annotations

import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import tiktoken
from PIL import Image

ENC = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(ENC.encode(text or ""))


class MockState:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = 0
        self.chat_requests = 0
        self.vision_requests = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.image_pixels: list[int] = []
        self.image_bytes: list[int] = []
        self.image_mimes: list[str] = []
        self.prompts: list[str] = []
        self.mode = "ok"
        self.fail_remaining = 0

    def reset(self, mode: str = "ok", fail_remaining: int = 0):
        with self.lock:
            self.requests = 0
            self.chat_requests = 0
            self.vision_requests = 0
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.image_pixels = []
            self.image_bytes = []
            self.image_mimes = []
            self.prompts = []
            self.mode = mode
            self.fail_remaining = fail_remaining


STATE = MockState()


def _translate_text(user_content: str) -> str:
    """Deterministic pseudo-translation that preserves markers and block order."""
    import re

    if "[TRARIUM_BLOCK_" not in user_content and "[BLOCK_" not in user_content:
        return "ترجمه آزمایشی: " + "متن نمونه فارسی. " * 3

    def repl(match: re.Match) -> str:
        return match.group(0)

    markers = list(re.finditer(r"\[(TRARIUM_BLOCK|BLOCK)_(\d+)\]", user_content))
    out = []
    for i, match in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(user_content)
        body = user_content[match.end():end].strip()
        # keep output roughly proportional to input so token stats stay meaningful
        words = max(1, len(body.split()))
        out.append(f"{match.group(0)}\n" + "ترجمه بلاک " + match.group(2) + " " + "واژه " * min(words, 60))
    return "\n\n".join(out)


def _vision_response(prompt: str) -> str:
    import re

    nums = [int(n) for n in re.findall(r"\b(\d+)\b", prompt)]
    if "JSON object" in prompt and "page keys" in prompt:
        pages = nums[:6] or [1]
        return json.dumps({str(p): [{"text": f"Scanned line on page {p}", "bbox": [0.1, 0.1, 0.8, 0.15]}] for p in pages})
    return json.dumps([{"text": "Scanned line one", "bbox": [0.1, 0.1, 0.8, 0.15]},
                       {"text": "Scanned line two", "bbox": [0.1, 0.18, 0.8, 0.23]}])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _send(self, code: int, payload: dict | str):
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"data": [{"id": "mock/model", "object": "model"}]})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "invalid json"}})
            return

        with STATE.lock:
            STATE.requests += 1
            mode = STATE.mode
            if STATE.fail_remaining > 0:
                STATE.fail_remaining -= 1
                self._send(500, {"error": {"message": "simulated upstream failure"}})
                return

        if mode == "waf403":
            self._send(403, {"success": False, "error": "Access denied by security policy."})
            return
        if mode == "http403":
            self._send(403, {"error": {"code": 403, "message": "blocked by guardrail"}})
            return
        if mode == "ratelimit":
            self._send(429, {"error": {"message": "rate limited"}})
            return

        messages = payload.get("messages") or []
        user = messages[-1].get("content") if messages else ""
        is_vision = isinstance(user, list)

        if is_vision:
            texts = [p.get("text", "") for p in user if isinstance(p, dict) and p.get("type") == "text"]
            prompt = "\n".join(texts)
            for part in user:
                if not isinstance(part, dict) or part.get("type") != "image_url":
                    continue
                url = (part.get("image_url") or {}).get("url", "")
                mime = url.split(";")[0].replace("data:", "") if url.startswith("data:") else "unknown"
                try:
                    blob = base64.b64decode(url.split(",", 1)[1])
                    with Image.open(io.BytesIO(blob)) as img:
                        pixels = img.size[0] * img.size[1]
                except Exception:
                    blob, pixels = b"", 0
                with STATE.lock:
                    STATE.image_pixels.append(pixels)
                    STATE.image_bytes.append(len(blob))
                    STATE.image_mimes.append(mime)
            with STATE.lock:
                STATE.vision_requests += 1
                STATE.prompts.append(prompt[:2000])
                STATE.prompt_tokens += count_tokens(prompt) + sum(p // 750 for p in STATE.image_pixels[-1:])
            content = _vision_response(prompt)
        else:
            prompt = str(user or "")
            with STATE.lock:
                STATE.chat_requests += 1
                STATE.prompts.append(prompt[:4000])
                STATE.prompt_tokens += count_tokens(prompt)
            if mode == "empty":
                content = ""
            elif mode == "garbage":
                content = "<<<not a translation>>>"
            else:
                content = _translate_text(prompt)

        if mode == "slow":
            import time as _t
            _t.sleep(2)

        with STATE.lock:
            STATE.completion_tokens += count_tokens(content)
            pt = count_tokens(prompt)
            ct = count_tokens(content)

        self._send(200, {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": payload.get("model", "mock/model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
        })


class MockServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()
