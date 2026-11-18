"""Read-only snapshots for the live translation monitor.

Everything here comes from the durable job/usage databases, so a page reload
shows exactly the same numbers instead of starting from zero.
"""
from __future__ import annotations

import time

from trarium.jobs.store import JobStore
from trarium.usage import UsageStore, default_store


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds != seconds:
        return "—"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} ثانیه"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} دقیقه و {rest} ثانیه"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ساعت و {minutes} دقیقه"


def _eta(job: dict, units: list[dict]) -> float | None:
    total = int(job.get("total_units") or 0)
    done = int(job.get("completed_units") or 0) + int(job.get("failed_units") or 0)
    if total <= 0 or done <= 0 or done >= total:
        return 0.0 if total and done >= total else None
    stamps = sorted(float(u.get("updated_at") or 0) for u in units
                    if u.get("status") in {"completed", "failed"} and u.get("updated_at"))
    rate = None
    if len(stamps) >= 2:
        recent = stamps[-11:]
        gaps = [b - a for a, b in zip(recent, recent[1:]) if b > a]
        if gaps:
            rate = sum(gaps) / len(gaps)
    if rate is None:
        started = float(job.get("started_at") or 0)
        if started > 0:
            rate = max(0.01, (time.time() - started) / done)
    if rate is None:
        return None
    return rate * (total - done)


def job_snapshot(job_id: str, store: JobStore | None = None,
                 usage: UsageStore | None = None) -> dict | None:
    store = store or JobStore()
    usage = usage or default_store()
    job = store.get(job_id)
    if not job:
        return None
    units = store.units(job_id)
    total = int(job.get("total_units") or len(units) or 0)
    completed = int(job.get("completed_units") or 0)
    failed = int(job.get("failed_units") or 0)
    if units:
        completed = max(completed, sum(1 for u in units if u["status"] == "completed"))
        failed = max(failed, sum(1 for u in units if u["status"] == "failed"))
    percent = (job.get("progress") or 0) * 100
    if total:
        percent = max(percent, (completed + failed) / total * 100)
    meter = usage.summary("all", job_id=job_id)
    elapsed = 0.0
    if job.get("started_at"):
        end = float(job.get("finished_at") or time.time())
        elapsed = max(0.0, end - float(job["started_at"]))
    return {
        "job": job,
        "units": units,
        "total": total, "completed": completed, "failed": failed,
        "remaining": max(0, total - completed - failed),
        "percent": round(min(100.0, max(0.0, percent)), 1),
        "eta_seconds": _eta({**job, "completed_units": completed, "failed_units": failed}, units),
        "elapsed_seconds": elapsed,
        "input_tokens": max(int(job.get("input_tokens") or 0), meter["input_tokens"]),
        "output_tokens": max(int(job.get("output_tokens") or 0), meter["output_tokens"]),
        "total_tokens": max(int(job.get("input_tokens") or 0), meter["input_tokens"])
                        + max(int(job.get("output_tokens") or 0), meter["output_tokens"]),
        "api_requests": max(int(job.get("api_requests") or 0), meter["requests"]),
        "cost_usd": meter["cost_usd"],
        "provider": job.get("provider") or "—",
        "model": job.get("model") or "—",
        "stage": job.get("stage") or "",
        "error": job.get("error") or "",
        "events": store.events(job_id, 40),
        "status": job.get("status"),
        "result_name": job.get("result_name") or "",
    }


def active_jobs(store: JobStore | None = None) -> list[dict]:
    store = store or JobStore()
    active = [j for j in store.list_jobs() if j["status"] in {"running", "pausing", "queued"}]
    return [job_snapshot(j["id"], store) for j in active]


def provider_usage(provider: str, *, usage: UsageStore | None = None) -> dict:
    usage = usage or default_store()
    return {
        "llm": {"today": usage.summary("today", provider=provider, kind="llm"),
                "month": usage.summary("month", provider=provider, kind="llm"),
                "all": usage.summary("all", provider=provider, kind="llm"),
                "limit": usage.limit_status(provider, "llm")},
        "ocr": {"today": usage.summary("today", provider=provider, kind="ocr"),
                "month": usage.summary("month", provider=provider, kind="ocr"),
                "all": usage.summary("all", provider=provider, kind="ocr"),
                "limit": usage.limit_status(provider, "ocr")},
    }
