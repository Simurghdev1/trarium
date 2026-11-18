"""آمار واقعی پروژه‌ها از تسک‌ها و دفتر مصرف.

هیچ عددی ساخته نمی‌شود: همهٔ مقادیر از جدول تسک‌ها (``jobs.sqlite3``) و
رویدادهای مصرف (``usage.sqlite3``) جمع می‌شوند. اگر داده‌ای نباشد صفر برمی‌گردد.
"""
from __future__ import annotations

import time
from collections import defaultdict

from trarium.jobs.store import JobStore
from trarium.usage import KIND_OCR, default_store

_FINISHED = {"completed", "failed", "cancelled"}


def _job_pages(job: dict) -> int:
    """تعداد صفحه/واحد پردازش‌شدهٔ یک تسک."""
    if job.get("kind") == "pdf":
        return int(job.get("completed_units") or 0)
    return int(job.get("completed_units") or 0)


def dashboard(days: int = 30, *, job_store: JobStore | None = None, usage_store=None) -> dict:
    store = job_store or JobStore()
    usage = usage_store or default_store()
    jobs = store.list_jobs(limit=1000)
    cutoff = time.time() - max(1, int(days)) * 86400
    recent = [job for job in jobs if float(job.get("created_at") or 0) >= cutoff]

    completed = [job for job in jobs if job.get("status") == "completed"]
    failed = [job for job in jobs if job.get("status") == "failed"]
    pages = sum(_job_pages(job) for job in jobs)
    tokens_jobs = sum(int(job.get("input_tokens") or 0) + int(job.get("output_tokens") or 0)
                      for job in jobs)
    cost_jobs = sum(float(job.get("cost_usd") or 0) for job in jobs)

    total_seconds = 0.0
    speed_pages = 0
    for job in completed:
        started = float(job.get("started_at") or 0)
        finished = float(job.get("finished_at") or 0)
        if started and finished and finished > started:
            total_seconds += finished - started
            speed_pages += _job_pages(job)
    pages_per_minute = round(speed_pages / (total_seconds / 60), 2) if total_seconds > 0 else 0.0

    month_usage = usage.summary("month")
    all_usage = usage.summary("all")
    ocr_month = usage.summary("month", kind=KIND_OCR)
    ocr_errors = int(ocr_month.get("errors") or 0)

    per_provider: list[dict] = []
    for row in usage.by_model("month"):
        per_provider.append({
            "provider": row["provider"], "model": row["model"], "kind": row["kind"],
            "tokens": row["total_tokens"], "requests": row["requests"],
            "cost_usd": row["cost_usd"],
        })

    by_day: dict[str, dict] = defaultdict(lambda: {"completed": 0, "failed": 0, "pages": 0})
    for job in jobs:
        finished = float(job.get("finished_at") or 0)
        if not finished:
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(finished))
        bucket = by_day[day]
        if job.get("status") == "completed":
            bucket["completed"] += 1
            bucket["pages"] += _job_pages(job)
        elif job.get("status") == "failed":
            bucket["failed"] += 1

    history = [{
        "id": job["id"], "title": job["title"], "kind": job["kind"],
        "status": job["status"], "units": int(job.get("total_units") or 0),
        "done": int(job.get("completed_units") or 0),
        "tokens": int(job.get("input_tokens") or 0) + int(job.get("output_tokens") or 0),
        "cost_usd": round(float(job.get("cost_usd") or 0), 6),
        "created_at": float(job.get("created_at") or 0),
        "finished_at": float(job.get("finished_at") or 0),
    } for job in jobs[:100]]

    return {
        "days": int(days),
        "books_translated": len(completed),
        "books_failed": len(failed),
        "books_total": len(jobs),
        "books_recent": len(recent),
        "pages_processed": pages,
        "tokens_jobs": tokens_jobs,
        "cost_jobs": round(cost_jobs, 6),
        "tokens_month": int(month_usage["total_tokens"]),
        "cost_month": float(month_usage["cost_usd"]),
        "tokens_all": int(all_usage["total_tokens"]),
        "requests_all": int(all_usage["requests"]),
        "ocr_pages_month": int(ocr_month["requests"]),
        "ocr_errors": ocr_errors,
        "avg_pages_per_minute": pages_per_minute,
        "avg_seconds_per_page": round(total_seconds / speed_pages, 1) if speed_pages else 0.0,
        "providers": per_provider,
        "daily": [{"day": day, **values} for day, values in sorted(by_day.items())],
        "history": history,
        "usage_by_project": usage.by_project("month"),
    }
