"""Tests for the seven expansion features (jobs, queue, usage, failover, manga, web)."""
from __future__ import annotations

import json
import threading
import zipfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from trarium.failover import FailoverError, SmartFailoverTranslator, Endpoint, classify_error
from trarium.jobs import JobCancelled, JobManager, JobPaused, JobStore
from trarium.jobs.manager import JobContext
from trarium.jobs.store import STATUS_COMPLETED, STATUS_PAUSED
from trarium.local_state import split_chain_text, trarium_home
from trarium.manga import MangaSettings, enhance, enhance_file
from trarium.monitor import job_snapshot
from trarium.usage import UsageStore, UsageTrackingTranslator
from trarium.websites import WebsiteError, extract_blocks, rewrite_html, translate_site


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TRARIUM_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    yield home


class FakeTranslator:
    provider = "fake"
    model_name = "fake-1"
    model = "fake-1"

    def __init__(self):
        self.calls: list[str] = []
        self.last_usage = {"input_tokens": 7, "output_tokens": 5}

    def get_token_limit(self):
        return 8000

    def translate(self, text, target_lang="fa"):
        text = str(text)
        self.calls.append(text)
        # Emulate a model: keep batching markers intact, prefix real lines.
        lines = [line if line.startswith("[[TRARIUM_SEG") or not line.strip() else "TR: " + line
                 for line in text.splitlines()]
        return "\n".join(lines)


# ----------------------------------------------------------------------------- usage
def test_usage_ledger_records_tokens_cost_and_limits():
    store = UsageStore()
    store.record("gemini", "gemini-3.8-flash", "llm", 1_000_000, 1_000_000, 2, "book-a")
    store.record("gemini", "gemini-3.8-flash", "llm", 500, 500, 1, "book-a")
    today = store.summary("today", provider="gemini")
    assert today["input_tokens"] == 1_000_500
    assert today["output_tokens"] == 1_000_500
    assert today["requests"] == 3
    assert today["cost_usd"] == pytest.approx(0.50025, abs=1e-6)
    assert store.by_project("month")[0]["project"] == "book-a"
    assert store.daily_series(3)[-1]["tokens"] == 2_001_000

    store.set_limit("gemini", "llm", daily_tokens=1_000_000)
    status = store.limit_status("gemini", "llm")
    assert not status["ok"] and status["exceeded"]

    store.log_failover("gemini", "m", "groq", "q", "429", "job-1")
    assert store.failover_history()[0]["to_provider"] == "groq"


def test_usage_tracking_translator_meters_a_real_call():
    store = UsageStore()
    fake = FakeTranslator()
    metered = UsageTrackingTranslator(fake, "openrouter", "free", project="p", job_id="j")
    assert metered.translate("hello", "fa") == "TR: hello"
    summary = store.summary("today", job_id="j")
    assert summary["requests"] == 1
    assert summary["input_tokens"] == 7 and summary["output_tokens"] == 5


# -------------------------------------------------------------------------- failover
class Boom:
    def __init__(self, error):
        self.error = error
        self.calls = 0
        self.last_usage = {}

    def translate(self, text, target_lang="fa"):
        self.calls += 1
        raise RuntimeError(self.error)


def _endpoint(provider, translator, model="m"):
    return Endpoint(provider=provider, model=model, translator=translator)


def test_classify_error_kinds():
    assert classify_error(RuntimeError("Gemini HTTP 429: rate limit")) == "rate_limit"
    assert classify_error(RuntimeError("HTTP 401 unauthorized")) == "auth"
    assert classify_error(RuntimeError("400 invalid request")) == "permanent"
    assert classify_error(RuntimeError("connection reset by peer")) == "network"


def test_failover_moves_to_next_provider_and_logs():
    store = UsageStore()
    bad = Boom("Gemini HTTP 429: too many requests")
    good = FakeTranslator()
    chain = SmartFailoverTranslator([_endpoint("gemini", bad), _endpoint("groq", good)],
                                    retries=2, store=store)
    assert chain.translate("hello").startswith("TR:")
    assert bad.calls == 1  # rate-limited endpoint is not hammered
    assert chain.last_provider == "groq"
    history = store.failover_history()
    assert history and history[0]["to_provider"] == "groq"


def test_permanent_and_network_errors_are_not_retried():
    permanent = Boom("400 invalid request payload")
    chain = SmartFailoverTranslator([_endpoint("a", permanent), _endpoint("b", FakeTranslator())],
                                    retries=4, store=UsageStore())
    chain.translate("x")
    assert permanent.calls == 1

    network = Boom("connection timed out")
    chain = SmartFailoverTranslator([_endpoint("a", network), _endpoint("b", FakeTranslator())],
                                   retries=4, store=UsageStore())
    chain.translate("x")
    assert network.calls == 1  # uncertain request is not sent twice


def test_failover_respects_local_quota_and_cost_cap():
    store = UsageStore()
    store.set_limit("gemini", "llm", daily_requests=1)
    store.record("gemini", "m", "llm", 10, 10, 1)
    gemini = FakeTranslator()
    groq = FakeTranslator()
    chain = SmartFailoverTranslator([_endpoint("gemini", gemini), _endpoint("groq", groq)],
                                    store=store)
    chain.translate("hello")
    assert gemini.calls == [] and groq.calls

    store.record("groq", "m", "llm", 1_000_000, 1_000_000, 1)
    capped = SmartFailoverTranslator([_endpoint("groq", FakeTranslator())],
                                    cost_cap_usd=0.0001, store=store)
    with pytest.raises(FailoverError):
        capped.translate("x")


def test_failover_chain_parsing():
    chain = split_chain_text("openrouter:model/a\ngemini\n# comment\n\ngroq:llama")
    assert chain == [{"provider": "openrouter", "model": "model/a"},
                     {"provider": "gemini", "model": ""},
                     {"provider": "groq", "model": "llama"}]


# ------------------------------------------------------------------------------ jobs
def test_job_store_priority_order_and_units():
    store = JobStore()
    low = store.create_job("book", "low", priority=2)
    high = store.create_job("book", "high", priority=9)
    assert store.next_queued()["id"] == high
    store.update(high, status="paused")
    assert store.next_queued()["id"] == low
    store.add_units(low, [{"title": "a"}, {"title": "b"}])
    store.set_unit(low, 0, "completed")
    assert store.completed_indexes(low) == {0}
    store.move(low, 1)
    assert store.stats()["total"] == 2


def test_manager_runs_pauses_and_resumes_a_job():
    store = JobStore()
    manager = JobManager(store=store, recover=False)
    manager.set_max_concurrent(1)
    seen: list[int] = []
    release = threading.Event()

    def handler(ctx: JobContext):
        ctx.ensure_units(["u1", "u2", "u3"])
        for idx in range(3):
            ctx.checkpoint()
            ctx.unit_started(idx)
            release.wait(3)
            seen.append(idx)
            ctx.unit_done(idx)
            ctx.update(stage="کار")
        ctx.finish(ctx.directory / "out.txt")

    manager.register_handler("fake", handler)
    job_id = manager.submit("fake", "job", units=["u1", "u2", "u3"])
    release.set()
    deadline = time.time() + 8
    while time.time() < deadline and store.get(job_id)["status"] != STATUS_COMPLETED:
        time.sleep(0.05)
    assert store.get(job_id)["status"] == STATUS_COMPLETED
    assert seen == [0, 1, 2]
    snapshot = job_snapshot(job_id, store=store, usage=UsageStore())
    assert snapshot["percent"] == 100.0
    manager.shutdown()

    # Resume path: paused job goes back to the queue and completes.
    store2 = JobStore()
    manager2 = JobManager(store=store2, recover=False)
    gate = threading.Event()

    def slow(ctx: JobContext):
        ctx.ensure_units(["a", "b"])
        for idx in range(2):
            ctx.checkpoint()
            gate.wait(3)
            ctx.unit_done(idx)
        ctx.finish(ctx.directory / "out.txt")

    manager2.register_handler("slow", slow)
    job2 = manager2.submit("slow", "pausable", units=["a", "b"])
    deadline = time.time() + 5
    while time.time() < deadline and store2.get(job2)["status"] != "running":
        time.sleep(0.05)
    manager2.pause(job2)
    gate.set()
    deadline = time.time() + 5
    while time.time() < deadline and store2.get(job2)["status"] != STATUS_PAUSED:
        time.sleep(0.05)
    assert store2.get(job2)["status"] == STATUS_PAUSED
    manager2.resume(job2)
    deadline = time.time() + 6
    while time.time() < deadline and store2.get(job2)["status"] != STATUS_COMPLETED:
        time.sleep(0.05)
    assert store2.get(job2)["status"] == STATUS_COMPLETED
    manager2.shutdown()


def test_manager_cancel_recovery_and_queue_pause():
    store = JobStore()
    manager = JobManager(store=store, recover=False)
    started = threading.Event()

    def hanging(ctx: JobContext):
        ctx.ensure_units(["one", "two"])
        started.set()
        while True:
            ctx.checkpoint()
            time.sleep(0.05)

    manager.register_handler("hang", hanging)
    job_id = manager.submit("hang", "hang", units=["one", "two"])
    assert started.wait(5)
    manager.cancel(job_id)
    deadline = time.time() + 5
    while time.time() < deadline and store.get(job_id)["status"] != "cancelled":
        time.sleep(0.05)
    assert store.get(job_id)["status"] == "cancelled"
    manager.delete(job_id, remove_files=True)
    assert store.get(job_id) is None
    manager.shutdown()

    # Crash recovery: a job left "running" is queued again.
    store2 = JobStore()
    orphan = store2.create_job("book", "orphan")
    store2.update(orphan, status="running")
    manager2 = JobManager(store=store2)
    recovered = store2.get(orphan)
    assert recovered["status"] == "queued"
    assert "بازیابی" in recovered["stage"]
    manager2.pause_queue(True)
    assert JobManager(store=store2, recover=False).queue_paused is True
    manager2.pause_queue(False)
    manager2.shutdown()


def test_pause_and_cancel_exceptions_from_context():
    store = JobStore()
    manager = JobManager(store=store, recover=False)
    job_id = store.create_job("book", "ctx")
    job = store.get(job_id)
    control = type("C", (), {"pause": False, "cancel": False})()
    ctx = JobContext(manager, job, control)
    control.pause = True
    with pytest.raises(JobPaused):
        ctx.checkpoint()
    control.pause = False
    control.cancel = True
    with pytest.raises(JobCancelled):
        ctx.checkpoint()
    manager.shutdown()


def test_book_job_runs_end_to_end_with_metering(monkeypatch):
    from trarium.jobs import handlers

    fake = FakeTranslator()
    monkeypatch.setattr("trarium.failover.build_chain", lambda *a, **k: fake)
    store = JobStore()
    manager = JobManager(store=store, recover=False)
    manager.set_max_concurrent(1)
    text = ("Hello world. " * 80 + "\n\n") * 3
    job_id = manager.submit("book", "sample", config={"chunk_size": 2000, "workers": 1},
                            source_bytes=text.encode("utf-8"), source_name="sample.txt")
    deadline = time.time() + 20
    while time.time() < deadline and store.get(job_id)["status"] not in {"completed", "failed"}:
        time.sleep(0.05)
    job = store.get(job_id)
    assert job["status"] == "completed", job.get("error")
    assert Path(job["result_path"]).exists()
    assert job["input_tokens"] > 0
    assert "ترجمه شده توسط تراریوم" in Path(job["result_path"]).read_text(encoding="utf-8")
    assert fake.calls
    manager.shutdown()


# ----------------------------------------------------------------------------- manga
def _sample_image(path: Path, size=(320, 420), noise=False):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([40, 60, size[0] - 40, 160], fill="black")
    draw.line([30, 300, size[0] - 30, 320], fill="black", width=4)
    if noise:
        import numpy as np
        array = np.asarray(image).astype("int16")
        array += np.random.default_rng(3).normal(0, 12, array.shape).astype("int16")
        image = Image.fromarray(array.clip(0, 255).astype("uint8"))
    image.save(path)
    return path


def test_manga_2x_scales_and_never_overwrites_the_original(tmp_path):
    source = _sample_image(tmp_path / "page.png")
    original = source.read_bytes()
    settings = MangaSettings(scale="2x", operations=("denoise", "deblur", "contrast", "deskew"))
    report = enhance_file(source, tmp_path / "out" / "page.png", settings)
    assert report["ok"], report
    assert source.read_bytes() == original
    with Image.open(tmp_path / "out" / "page.png") as enhanced:
        assert enhanced.width == 640 and enhanced.height == 840
    assert any(step["name"] == "scale" for step in report["steps"])


def test_manga_auto_keeps_clean_high_resolution_page_untouched(tmp_path):
    image = Image.new("RGB", (2000, 3000), "white")
    draw = ImageDraw.Draw(image)
    for y in range(100, 2800, 80):
        draw.rectangle([100, y, 1900, y + 30], fill="black")
    _, report = enhance(image, MangaSettings(scale="auto"))
    assert report["ok"]
    assert all(step["name"] not in {"scale", "denoise"} for step in report["steps"])
    assert report["enhanced"] == [2000, 3000]


def test_manga_failure_returns_the_original_page(tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    report = enhance_file(broken, tmp_path / "out.png", MangaSettings(scale="2x"))
    assert report["ok"] is False
    assert not (tmp_path / "out.png").exists()


# --------------------------------------------------------------------------- website
PAGE = """<html><head><title>Demo Book</title></head><body>
<h1>Chapter One</h1>
<p>Hello world from Trarium.</p>
<p>Hello world from Trarium.</p>
<p>Second paragraph with <em>inline</em> text.</p>
<a href="/about.html">About</a>
</body></html>"""
ABOUT = """<html><head><title>About</title></head><body><h1>About us</h1>
<p>This is the about page.</p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        routes = {
            "/robots.txt": "User-agent: *\nDisallow: /private\n",
            "/": PAGE,
            "/about.html": ABOUT,
            "/private": "<html><body><p>Secret</p></body></html>",
        }
        body = routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args):
        pass


@pytest.fixture
def site_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(2)


def test_website_extract_and_rewrite_preserve_structure():
    soup, blocks, title = extract_blocks(PAGE)
    assert title == "Demo Book"
    assert len(blocks) == 3  # duplicate paragraph collapsed
    translations = {block.lower(): "translated-" + block[:6] for block in blocks}
    html = rewrite_html(soup, translations, "fa")
    assert "<h1" in html and "<p" in html
    assert 'dir="rtl"' in html
    assert blocks[0] in PAGE


def test_website_translation_single_page_dedupes_and_writes_html(site_server, tmp_path):
    fake = FakeTranslator()
    result = translate_site(site_server + "/", fake, "fa", max_pages=1, delay=0)
    assert result.pages_ok == 1
    assert "TR:" in result.pages[0].html
    assert result.pages[0].blocks == 3
    # Batched prompt keeps the markers, so all three segments arrive in one call.
    assert len(fake.calls) == 1
    path = result.save(tmp_path, "html")
    assert path.suffix == ".html" and "TR:" in path.read_text(encoding="utf-8")


def test_website_crawls_internal_pages_and_respects_robots(site_server):
    result = translate_site(site_server + "/", FakeTranslator(), "fa", max_pages=3, delay=0)
    assert result.pages_ok == 2
    urls = {p.url for p in result.pages}
    assert site_server + "/about.html" in urls
    with pytest.raises(WebsiteError):
        translate_site(site_server + "/private", FakeTranslator(), "fa", delay=0)


def test_website_reports_failed_page_without_stopping():
    result = translate_site("http://127.0.0.1:1/missing", FakeTranslator(), "fa", delay=0)
    assert result.pages_failed == 1
    assert result.pages[0].error


# ------------------------------------------------------------------- job handlers
def test_queue_runs_multiple_projects_by_priority():
    store = JobStore()
    manager = JobManager(store=store, recover=False)
    manager.set_max_concurrent(1)
    order: list[str] = []

    def handler(ctx: JobContext):
        ctx.ensure_units(["only"])
        order.append(ctx.job["title"])
        time.sleep(0.1)
        ctx.unit_done(0)
        ctx.finish(ctx.directory / "out.txt")

    manager.register_handler("tick", handler)
    ids = [manager.submit("tick", name, priority=prio, units=["only"])
           for name, prio in [("low", 1), ("high", 9), ("mid", 5)]]
    deadline = time.time() + 10
    while time.time() < deadline and any(store.get(i)["status"] != STATUS_COMPLETED for i in ids):
        time.sleep(0.05)
    assert order == ["high", "mid", "low"]
    assert all(store.get(i)["status"] == STATUS_COMPLETED for i in ids)
    manager.shutdown()


def test_manga_job_builds_a_zip_and_keeps_the_source(tmp_path, monkeypatch):
    image = _sample_image(tmp_path / "page.png", noise=True)
    buffer = __import__("io").BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.write(image, "page.png")
    source_bytes = buffer.getvalue()

    store = JobStore()
    manager = JobManager(store=store, recover=False)
    manager.set_max_concurrent(1)
    job_id = manager.submit("manga", "manga job",
                            config={"manga": {"scale": "2x", "operations": ["denoise", "contrast"]}},
                            source_bytes=source_bytes, source_name="pages.zip")
    deadline = time.time() + 20
    while time.time() < deadline and store.get(job_id)["status"] not in {"completed", "failed"}:
        time.sleep(0.05)
    job = store.get(job_id)
    assert job["status"] == "completed", job.get("error")
    assert Path(job["source_path"]).read_bytes() == source_bytes
    with zipfile.ZipFile(job["result_path"]) as archive:
        names = archive.namelist()
        assert "page.png" in names and "report.json" in names
        with Image.open(__import__("io").BytesIO(archive.read("page.png"))) as enhanced:
            assert enhanced.width == 640
    manager.shutdown()


def test_website_job_saves_resumable_pages(site_server, monkeypatch):
    monkeypatch.setattr("trarium.failover.build_chain", lambda *a, **k: FakeTranslator())
    store = JobStore()
    manager = JobManager(store=store, recover=False)
    manager.set_max_concurrent(1)
    job_id = manager.submit("website", "site job", config={
        "url": site_server + "/", "max_pages": 2, "delay": 0, "output_format": "html",
        "provider_chain": [{"provider": "fake", "model": "m"}],
    })
    deadline = time.time() + 20
    while time.time() < deadline and store.get(job_id)["status"] not in {"completed", "failed"}:
        time.sleep(0.05)
    job = store.get(job_id)
    assert job["status"] == "completed", job.get("error")
    assert Path(job["result_path"]).exists()
    assert job["completed_units"] == 2
    assert (trarium_home() / "jobs" / job_id / "pages").exists()
    manager.shutdown()
