"""P0 security and robustness fixes (2026-11-19)."""
from __future__ import annotations

import http.server
import re
import threading
from pathlib import Path

import pytest

from trarium import netguard
from trarium.netguard import BlockedURLError, assert_public_url

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://127.0.0.1:8501/_stcore/health",
    "http://localhost/",
    "http://LOCALHOST./admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://10.0.0.5/",
    "http://172.16.3.4/",
    "http://192.168.1.1/",
    "http://100.64.0.1/",
    "http://0.0.0.0/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://2130706433/",          # 127.0.0.1 as a decimal integer
    "http://user:pass@example.com/",
    "file:///etc/passwd",
    "gopher://example.com/",
])
def test_internal_targets_blocked(url, monkeypatch):
    monkeypatch.delenv("TRARIUM_ALLOW_PRIVATE_URLS", raising=False)
    with pytest.raises(BlockedURLError):
        assert_public_url(url)


def test_public_literal_allowed(monkeypatch):
    monkeypatch.delenv("TRARIUM_ALLOW_PRIVATE_URLS", raising=False)
    assert assert_public_url("http://93.184.215.14/") == "http://93.184.215.14/"


def test_dns_name_resolving_to_private_blocked(monkeypatch):
    monkeypatch.delenv("TRARIUM_ALLOW_PRIVATE_URLS", raising=False)
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))])
    with pytest.raises(BlockedURLError):
        assert_public_url("http://innocent.example/")


def _serve(handler_cls):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class _Secret(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # noqa: N802
        type(self).hits += 1
        body = b"<html><body><p>INTERNAL SECRET</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_website_fetch_never_reaches_localhost(monkeypatch):
    monkeypatch.delenv("TRARIUM_ALLOW_PRIVATE_URLS", raising=False)
    from trarium.websites import WebsiteError, fetch_html, translate_site
    server = _serve(_Secret)
    _Secret.hits = 0
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        with pytest.raises(WebsiteError):
            fetch_html(url)
        with pytest.raises(WebsiteError):
            translate_site(url, translator=object())
        assert _Secret.hits == 0
    finally:
        server.shutdown()


def test_redirect_to_internal_is_refused(monkeypatch):
    """A public page that 302s to 169.254.169.254 must not be followed."""
    monkeypatch.delenv("TRARIUM_ALLOW_PRIVATE_URLS", raising=False)
    calls = []

    class Resp:
        status_code = 302
        is_redirect = True
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}

    def fake_get(url, **kw):
        calls.append(url)
        assert kw.get("allow_redirects") is False
        return Resp()

    monkeypatch.setattr(netguard.requests, "get", fake_get)
    with pytest.raises(BlockedURLError):
        netguard.safe_get("http://93.184.215.14/")
    assert calls == ["http://93.184.215.14/"]


def test_novel_scraper_blocks_internal(monkeypatch):
    monkeypatch.delenv("TRARIUM_ALLOW_PRIVATE_URLS", raising=False)
    from trarium.novel.scraper import fetch_page, _download_image
    with pytest.raises(BlockedURLError):
        fetch_page("http://127.0.0.1:1/")
    assert _download_image("http://169.254.169.254/x.png") is None


def test_private_opt_in_for_local_dev(monkeypatch):
    monkeypatch.setenv("TRARIUM_ALLOW_PRIVATE_URLS", "1")
    assert assert_public_url("http://127.0.0.1:9/") == "http://127.0.0.1:9/"


def test_streamlit_never_prefills_server_keys():
    """Server API keys must never be sent to the browser as a field value."""
    src = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    leaks = re.findall(r"value\s*=\s*os\.environ(?:\.get)?\(\s*[\"'][A-Z0-9_]*(?:KEY|TOKEN|SECRET)[A-Z0-9_]*", src)
    assert leaks == []
    assert "_secret_input(" in src


def test_pipeline_retries_failed_unit_sequentially(tmp_path, monkeypatch):
    """A unit that fails during the parallel pass gets a calm second try."""
    monkeypatch.setenv("TRARIUM_HOME", str(tmp_path))
    from trarium.extractors.base import ExtractedBook
    from trarium.processors.pipeline import TranslationPipeline

    class Flaky:
        provider = "fake"; model = "fake"
        def __init__(self):
            self.calls = 0
        def translate(self, text, target_lang="fa", *a, **k):
            self.calls += 1
            if self.calls <= 3:          # the whole first pass (3 attempts) fails
                raise RuntimeError("429 rate limited")
            return "ترجمهٔ فارسی"

    tr = Flaky()
    try:
        pipe = TranslationPipeline(tr, target_lang="fa", max_workers=1, translation_memory=False, use_checkpoint=False)
    except TypeError:
        pipe = TranslationPipeline(tr, target_lang="fa")
        pipe.use_cache = False; pipe.use_checkpoint = False
    pipe.retry_pause = 0.0
    book = ExtractedBook(title="t", author="", chapters=[{"title": "c1", "content": "Hello world, this is a test."}])
    result = pipe.run(book)
    assert result.failed_chunks == 0
    assert "فارسی" in result.translated_text


def test_close_cut_ink_extends_over_half_covered_line():
    import pymupdf
    from trarium.pdf_optimized import _close_cut_ink
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    # A thick black "line of text" from y=60 to y=80.
    page.draw_rect(pymupdf.Rect(40, 60, 260, 80), color=None, fill=(0, 0, 0))
    # OCR box that stops in the middle of it.
    box = pymupdf.Rect(40, 30, 260, 66)
    grown = _close_cut_ink(page, box)
    assert grown.y1 >= 80
    # A box with clean paper below is untouched.
    clean = pymupdf.Rect(40, 100, 260, 120)
    assert _close_cut_ink(page, clean) == clean
