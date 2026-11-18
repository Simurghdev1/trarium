"""Seed a throwaway TRARIUM_HOME with demo jobs/usage for README screenshots.

Run with ``TRARIUM_HOME=/tmp/trarium-shots``. Nothing here touches the real
``~/.trarium`` state or the app code; it only fills the local stores so the new
panels are rendered with realistic content while taking screenshots.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from trarium.jobs.store import JobStore
from trarium.usage import KIND_LLM, KIND_OCR, UsageStore, estimate_cost

home = Path(os.environ.get("TRARIUM_HOME") or "/tmp/trarium-shots")
home.mkdir(parents=True, exist_ok=True)
store = JobStore(home / "jobs.sqlite3")
usage = UsageStore(home / "usage.sqlite3")

# Start from a clean demo ledger every time the seeder runs.
with store._lock, store._connect() as db:
    db.execute("DELETE FROM jobs")
    db.execute("DELETE FROM job_units")
    db.execute("DELETE FROM job_events")
    db.commit()
with usage._lock, usage._connect() as db:
    db.execute("DELETE FROM usage_events")
    db.execute("DELETE FROM failover_log")
    db.commit()

now = time.time()

# ------------------------------------------------------------------- running job
book = store.create_job("book", "قصر رویال", total_units=12, target_lang="fa",
                        provider="openrouter", model="openrouter/free", priority=8)
store.update(book, status="running", progress=0.58, completed_units=7,
             current_unit="فصل ۸ — نامهٔ ناتمام", stage="ترجمهٔ فصل ۸ از ۱۲",
             started_at=now - 640, input_tokens=48120, output_tokens=52410,
             api_requests=14, cost_usd=0.0)
for index in range(12):
    title = f"فصل {index + 1}"
    store.set_unit(book, index, "completed" if index < 7 else "pending", title=title)
store.add_event(book, "تسک شروع شد")
store.add_event(book, "فصل ۷ ترجمه شد (۶٬۹۴۰ توکن)")
store.add_event(book, "Checkpoint ذخیره شد؛ ادامه از فصل ۸", level="info")

# -------------------------------------------------------------------- paused job
manga = store.create_job("manga", "One Piece — جلد ۱۲", total_units=6, target_lang="fa",
                         provider="openrouter", model="openrouter/free", priority=5)
store.update(manga, status="paused", progress=0.5, completed_units=3, total_units=6,
             current_unit="صفحه ۴۱", stage="افزایش کیفیت (۲x)",
             started_at=now - 1200, input_tokens=0, output_tokens=0, api_requests=0)
for index in range(6):
    store.set_unit(manga, index, "completed" if index < 3 else "pending",
                   title=f"صفحه {index * 20 + 1}")
store.add_event(manga, "کاربر تسک را متوقف کرد")

# -------------------------------------------------------------------- queued jobs
pdf = store.create_job("pdf", "مبانی اقتصاد — ویرایش سوم", total_units=48, target_lang="fa",
                       provider="openrouter", model="openrouter/free", priority=7)
store.update(pdf, stage="در انتظار اجرا", progress=0.0)

novel = store.create_job("novel", "وب‌ناول: فصل ۱ تا ۱۰",
                         config={"url": "https://novel.example.com/chapter-1"},
                         total_units=10, target_lang="fa", provider="openrouter",
                         model="openrouter/free", priority=4)
store.update(novel, stage="در انتظار اجرا")

# ------------------------------------------------------------- completed website
site_dir = home / "jobs" / "demo-site"
site_dir.mkdir(parents=True, exist_ok=True)
result = site_dir / "translated.html"
result.write_text("<!doctype html><html lang='fa'><body><h1>نمونهٔ خروجی</h1></body></html>",
                  encoding="utf-8")
site = store.create_job("website", "وب‌سایت: docs.example.com", total_units=4,
                        target_lang="fa", provider="openrouter", model="openrouter/free",
                        config={"url": "https://docs.example.com", "max_pages": 4})
store.update(site, status="completed", progress=1.0, completed_units=4, total_units=4,
             stage="پایان", finished_at=now - 300, started_at=now - 520,
             input_tokens=18420, output_tokens=20110, api_requests=4,
             result_path=str(result), result_name="docs.example.com-fa.html")
store.add_event(site, "۴ صفحه ترجمه شد · خروجی HTML ذخیره شد")

# ----------------------------------------------------------------- usage ledger
rows = [
    ("openrouter", "openrouter/free", KIND_LLM, 48120, 52410, 14, "قصر رویال"),
    ("openrouter", "openrouter/free", KIND_LLM, 18420, 20110, 4, "docs.example.com"),
    ("gemini", "gemini-3.6-flash", KIND_LLM, 31200, 29800, 9, "وب‌ناول: فصل ۱ تا ۱۰"),
    ("gemini", "gemini-3.6-flash", KIND_OCR, 12400, 3100, 6, "مبانی اقتصاد"),
    ("groq", "default", KIND_LLM, 8600, 7400, 3, "One Piece — جلد ۱۲"),
]
for provider, model, kind, tin, tout, requests, project in rows:
    usage.record(provider, model, kind, input_tokens=tin, output_tokens=tout,
                 requests=requests, project=project,
                 cost_usd=estimate_cost(provider, model, tin, tout))

usage.log_failover("gemini", "gemini-3.6-flash", "openrouter", "openrouter/free",
                   "RateLimit 429", job_id=book)
usage.log_failover("openrouter", "openrouter/free", "groq", "default",
                   "Timeout 120s", job_id=novel)

import json

(home / "seed.json").write_text(json.dumps({"running": book, "website": site}), encoding="utf-8")
print("seeded", home)
print("running job id:", book)
print("website job id:", site)
