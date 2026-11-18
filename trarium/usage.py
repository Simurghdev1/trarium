"""Provider usage ledger, local quotas, cost estimates and failover history.

Every request made through :class:`UsageTrackingTranslator` lands in a local
SQLite DB. Values reported by the provider are kept separate from local
estimates so the UI never presents a guess as an official quota.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from trarium.local_state import trarium_home
from trarium.utils import estimate_tokens

# USD per 1,000,000 tokens: (input, output). These are public list prices that
# change often, so every cost shown is explicitly "estimated" in the UI.
PRICING: dict[str, dict[str, tuple[float, float]]] = {
    "openrouter": {"openrouter/free": (0.0, 0.0), "default": (0.0, 0.0)},
    "gemini": {
        "gemini-3.8-flash": (0.10, 0.40),
        "gemini-3.7-flash": (0.10, 0.40),
        "gemini-3.6-flash": (0.08, 0.30),
        "gemini-3.5-flash": (0.08, 0.30),
        "default": (0.10, 0.40),
    },
    "groq": {"default": (0.05, 0.08)},
    "router9": {"default": (0.0, 0.0)},
    "openai_compatible": {"default": (0.0, 0.0)},
}

KIND_LLM = "llm"
KIND_OCR = "ocr"


def price_for(provider: str, model: str) -> tuple[float, float]:
    table = PRICING.get(provider or "", {})
    return table.get(model or "", table.get("default", (0.0, 0.0)))


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = price_for(provider, model)
    return round((max(0, int(input_tokens or 0)) * price_in + max(0, int(output_tokens or 0)) * price_out) / 1_000_000, 6)


@dataclass
class UsageRecord:
    provider: str
    model: str
    kind: str
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    cost_usd: float = 0.0
    estimated: bool = True


class UsageStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else (trarium_home() / "usage.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def _init(self) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS usage_events ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
                " provider TEXT NOT NULL, model TEXT NOT NULL, kind TEXT NOT NULL,"
                " project TEXT DEFAULT '', job_id TEXT DEFAULT '',"
                " input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,"
                " requests INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0,"
                " estimated INTEGER DEFAULT 1, error TEXT DEFAULT '')"
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_events(provider, kind)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS limits ("
                " provider TEXT NOT NULL, kind TEXT NOT NULL,"
                " daily_requests INTEGER DEFAULT 0, monthly_requests INTEGER DEFAULT 0,"
                " daily_tokens INTEGER DEFAULT 0, monthly_tokens INTEGER DEFAULT 0,"
                " PRIMARY KEY(provider, kind))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS failover_log ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
                " from_provider TEXT, from_model TEXT, to_provider TEXT, to_model TEXT,"
                " reason TEXT, job_id TEXT DEFAULT '')"
            )
            db.commit()

    # ------------------------------------------------------------------ writes
    def record(self, provider: str, model: str = "", kind: str = KIND_LLM,
               input_tokens: int = 0, output_tokens: int = 0, requests: int = 1,
               project: str = "", job_id: str = "", cost_usd: float | None = None,
               estimated: bool = True, error: str = "") -> None:
        provider = str(provider or "unknown")
        model = str(model or "")
        in_tok = max(0, int(input_tokens or 0))
        out_tok = max(0, int(output_tokens or 0))
        cost = estimate_cost(provider, model, in_tok, out_tok) if cost_usd is None else float(cost_usd)
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO usage_events(ts,provider,model,kind,project,job_id,input_tokens,"
                "output_tokens,requests,cost_usd,estimated,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), provider, model, kind, project or "", job_id or "",
                 in_tok, out_tok, max(0, int(requests or 0)), cost, 1 if estimated else 0, error or ""),
            )
            db.commit()

    def log_failover(self, from_provider: str, from_model: str, to_provider: str,
                     to_model: str, reason: str, job_id: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO failover_log(ts,from_provider,from_model,to_provider,to_model,reason,job_id)"
                " VALUES(?,?,?,?,?,?,?)",
                (time.time(), from_provider, from_model, to_provider, to_model, str(reason)[:500], job_id or ""),
            )
            db.commit()

    def set_limit(self, provider: str, kind: str = KIND_LLM, *,
                  daily_requests: int | None = None, monthly_requests: int | None = None,
                  daily_tokens: int | None = None, monthly_tokens: int | None = None) -> None:
        provider = str(provider or "")
        kind = kind or KIND_LLM
        with self._lock, self._connect() as db:
            row = db.execute("SELECT daily_requests,monthly_requests,daily_tokens,monthly_tokens"
                             " FROM limits WHERE provider=? AND kind=?", (provider, kind)).fetchone()
            current = list(row) if row else [0, 0, 0, 0]
            values = [
                current[0] if daily_requests is None else max(0, int(daily_requests)),
                current[1] if monthly_requests is None else max(0, int(monthly_requests)),
                current[2] if daily_tokens is None else max(0, int(daily_tokens)),
                current[3] if monthly_tokens is None else max(0, int(monthly_tokens)),
            ]
            db.execute("INSERT OR REPLACE INTO limits(provider,kind,daily_requests,monthly_requests,"
                       "daily_tokens,monthly_tokens) VALUES(?,?,?,?,?,?)",
                       (provider, kind, *values))
            db.commit()

    def clear(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM usage_events")
            db.execute("DELETE FROM failover_log")
            db.commit()

    # ------------------------------------------------------------------- reads
    @staticmethod
    def _since(period: str) -> float:
        now = datetime.now()
        if period in {"today", "day", "daily"}:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period in {"month", "monthly"}:
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            return 0.0
        return start.timestamp()

    def summary(self, period: str = "all", provider: str | None = None,
                kind: str | None = None, project: str | None = None, job_id: str | None = None) -> dict:
        clauses = ["ts >= ?"]
        params: list[object] = [self._since(period)]
        for column, value in (("provider", provider), ("kind", kind), ("project", project), ("job_id", job_id)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = " AND ".join(clauses)
        with self._lock, self._connect() as db:
            row = db.execute(
                f"SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),"
                f" COALESCE(SUM(requests),0), COALESCE(SUM(cost_usd),0), COUNT(*),"
                f" COALESCE(SUM(estimated),0), COALESCE(SUM(error != ''),0)"
                f" FROM usage_events WHERE {where}", params,
            ).fetchone()
        in_tok, out_tok, requests, cost, events, estimated_events, errors = row
        return {
            "input_tokens": int(in_tok), "output_tokens": int(out_tok),
            "total_tokens": int(in_tok) + int(out_tok), "requests": int(requests),
            "cost_usd": round(float(cost), 6), "events": int(events),
            "estimated_events": int(estimated_events), "errors": int(errors),
            "period": period, "official": False,
        }

    def daily_series(self, days: int = 30, provider: str | None = None,
                     kind: str | None = None) -> list[dict]:
        start = (datetime.now() - timedelta(days=max(1, int(days)) - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        clauses = ["ts >= ?"]
        params: list[object] = [start]
        if provider:
            clauses.append("provider=?")
            params.append(provider)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT date(ts,'unixepoch','localtime') AS day,"
                " COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),"
                " COALESCE(SUM(requests),0), COALESCE(SUM(cost_usd),0)"
                f" FROM usage_events WHERE {' AND '.join(clauses)} GROUP BY day ORDER BY day",
                params,
            ).fetchall()
        return [{"day": r[0], "input_tokens": int(r[1]), "output_tokens": int(r[2]),
                 "tokens": int(r[1]) + int(r[2]), "requests": int(r[3]),
                 "cost_usd": round(float(r[4]), 6)} for r in rows]

    def monthly_series(self, months: int = 6, provider: str | None = None,
                       kind: str | None = None) -> list[dict]:
        clauses, params = [], []
        if provider:
            clauses.append("provider=?")
            params.append(provider)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT strftime('%Y-%m', ts,'unixepoch','localtime') AS month,"
                " COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),"
                " COALESCE(SUM(requests),0), COALESCE(SUM(cost_usd),0)"
                f" FROM usage_events{where} GROUP BY month ORDER BY month DESC LIMIT ?",
                [*params, max(1, int(months))],
            ).fetchall()
        return [{"month": r[0], "input_tokens": int(r[1]), "output_tokens": int(r[2]),
                 "tokens": int(r[1]) + int(r[2]), "requests": int(r[3]),
                 "cost_usd": round(float(r[4]), 6)} for r in reversed(rows)]

    def by_model(self, period: str = "month", provider: str | None = None) -> list[dict]:
        clauses = ["ts >= ?"]
        params: list[object] = [self._since(period)]
        if provider:
            clauses.append("provider=?")
            params.append(provider)
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT provider, model, kind, COALESCE(SUM(input_tokens),0),"
                " COALESCE(SUM(output_tokens),0), COALESCE(SUM(requests),0), COALESCE(SUM(cost_usd),0)"
                f" FROM usage_events WHERE {' AND '.join(clauses)}"
                " GROUP BY provider, model, kind ORDER BY 5 DESC, 4 DESC", params,
            ).fetchall()
        return [{"provider": r[0], "model": r[1], "kind": r[2], "input_tokens": int(r[3]),
                 "output_tokens": int(r[4]), "total_tokens": int(r[3]) + int(r[4]),
                 "requests": int(r[5]), "cost_usd": round(float(r[6]), 6)} for r in rows]

    def by_project(self, period: str = "month") -> list[dict]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT project, COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),"
                " COALESCE(SUM(requests),0), COALESCE(SUM(cost_usd),0)"
                " FROM usage_events WHERE ts >= ? GROUP BY project"
                " ORDER BY COALESCE(SUM(cost_usd),0) DESC, COALESCE(SUM(input_tokens),0) DESC",
                (self._since(period),),
            ).fetchall()
        return [{"project": r[0] or "(بدون پروژه)", "input_tokens": int(r[1]),
                 "output_tokens": int(r[2]), "total_tokens": int(r[1]) + int(r[2]),
                 "requests": int(r[3]), "cost_usd": round(float(r[4]), 6)} for r in rows]

    def get_limits(self) -> dict[tuple[str, str], dict]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT provider,kind,daily_requests,monthly_requests,"
                              "daily_tokens,monthly_tokens FROM limits").fetchall()
        return {(r[0], r[1]): {"daily_requests": int(r[2]), "monthly_requests": int(r[3]),
                               "daily_tokens": int(r[4]), "monthly_tokens": int(r[5])} for r in rows}

    def limit_status(self, provider: str, kind: str = KIND_LLM) -> dict:
        limit = self.get_limits().get((provider, kind), {})
        today = self.summary("today", provider=provider, kind=kind)
        month = self.summary("month", provider=provider, kind=kind)
        checks = [
            ("daily_requests", today["requests"], limit.get("daily_requests", 0), "درخواست روزانه"),
            ("monthly_requests", month["requests"], limit.get("monthly_requests", 0), "درخواست ماهانه"),
            ("daily_tokens", today["total_tokens"], limit.get("daily_tokens", 0), "توکن روزانه"),
            ("monthly_tokens", month["total_tokens"], limit.get("monthly_tokens", 0), "توکن ماهانه"),
        ]
        exceeded, ratio = [], 0.0
        for _key, used, cap, label in checks:
            if cap and cap > 0:
                ratio = max(ratio, used / cap)
                if used >= cap:
                    exceeded.append(f"{label} ({used:,}/{cap:,})")
        return {
            "provider": provider, "kind": kind, "today": today, "month": month,
            "limits": limit, "exceeded": exceeded, "ok": not exceeded,
            "ratio": round(ratio, 3),
            "warning": bool(ratio >= 0.8) and not exceeded,
            "updated_at": time.time(),
        }

    def failover_history(self, limit: int = 50) -> list[dict]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT ts,from_provider,from_model,to_provider,to_model,reason,job_id"
                " FROM failover_log ORDER BY id DESC LIMIT ?", (max(1, int(limit)),),
            ).fetchall()
        return [{"time": r[0], "from_provider": r[1], "from_model": r[2],
                 "to_provider": r[3], "to_model": r[4], "reason": r[5], "job_id": r[6]} for r in rows]


_DEFAULT: UsageStore | None = None
_DEFAULT_LOCK = threading.Lock()


def default_store() -> UsageStore:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = UsageStore()
        return _DEFAULT


def record(*args, **kwargs) -> None:
    default_store().record(*args, **kwargs)


class UsageTrackingTranslator:
    """Transparent translator wrapper that meters every request.

    It never changes the prompt, the output or the provider behaviour; it only
    copies real usage numbers into the ledger and into the job counters.
    """

    def __init__(self, inner, provider: str = "", model: str = "", kind: str = KIND_LLM,
                 project: str = "", job_id: str = "", store: UsageStore | None = None,
                 on_usage=None):
        self.inner = inner
        self.provider = provider or getattr(inner, "provider", "") or inner.__class__.__name__
        self.model = model or getattr(inner, "model_name", "") or getattr(inner, "model", "")
        self.kind = kind
        self.project = project
        self.job_id = job_id
        self.store = store or default_store()
        self.on_usage = on_usage
        self.last_usage: dict = {}
        self.last_provider = self.provider
        self.last_model = self.model
        self.last_error = ""
        self.calls = 0

    def get_token_limit(self):
        getter = getattr(self.inner, "get_token_limit", None)
        return getter() if callable(getter) else 8000

    def translate(self, text: str, target_lang: str = "fa") -> str:
        started = time.monotonic()
        try:
            output = self.inner.translate(text, target_lang)
        except Exception as exc:
            self.calls += 1
            self.last_error = str(exc)
            self.last_usage = {}
            in_tok = estimate_tokens(text)
            self.store.record(self.provider, self.model, self.kind, in_tok, 0, 1,
                              self.project, self.job_id, error=str(exc)[:300])
            self._notify(in_tok, 0, 1, error=True)
            raise
        usage = getattr(self.inner, "last_usage", {}) or {}
        actual_in = int(usage.get("input_tokens") or 0)
        actual_out = int(usage.get("output_tokens") or 0)
        in_tok = actual_in or estimate_tokens(text)
        out_tok = actual_out or estimate_tokens(str(output))
        estimated = not (actual_in and actual_out)
        self.calls += 1
        self.last_usage = {"input_tokens": in_tok, "output_tokens": out_tok,
                           "elapsed_seconds": round(time.monotonic() - started, 3),
                           "estimated": estimated}
        self.store.record(self.provider, self.model, self.kind, in_tok, out_tok, 1,
                          self.project, self.job_id, estimated=estimated)
        self._notify(in_tok, out_tok, 1)
        return output

    def _notify(self, in_tok: int, out_tok: int, requests: int, error: bool = False) -> None:
        if not callable(self.on_usage):
            return
        try:
            self.on_usage(in_tok, out_tok, requests, estimate_cost(self.provider, self.model, in_tok, out_tok), error)
        except Exception:
            # Metering must never break a translation.
            pass


def record_ocr(pages: int = 0, requests: int = 1, provider: str = "local",
               model: str = "", project: str = "", job_id: str = "",
               estimated_input: int = 0, estimated_output: int = 0) -> None:
    """Record OCR work using local page counts (no invented official quota)."""
    if pages <= 0 and requests <= 0:
        return
    try:
        default_store().record(provider or "local", model or "ocr", KIND_OCR,
                               max(0, int(estimated_input)), max(0, int(estimated_output)),
                               max(1, int(requests)), project, job_id, cost_usd=0.0, estimated=True)
    except Exception:
        pass
