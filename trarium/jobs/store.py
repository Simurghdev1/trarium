"""Durable job and queue storage for background translation work."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from trarium.local_state import trarium_home

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_PAUSING = "pausing"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
ACTIVE_STATUSES = (STATUS_RUNNING, STATUS_PAUSING)

_FIELDS = {
    "kind", "title", "status", "priority", "position", "started_at", "finished_at",
    "total_units", "completed_units", "failed_units", "current_unit", "stage",
    "progress", "target_lang", "provider", "model", "error", "config", "source_path",
    "input_tokens", "output_tokens", "api_requests", "cost_usd", "result_path",
    "result_name", "updated_at",
}


class JobStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else (trarium_home() / "jobs.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', priority INTEGER DEFAULT 5,
                    position INTEGER DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    started_at REAL DEFAULT 0, finished_at REAL DEFAULT 0,
                    total_units INTEGER DEFAULT 0, completed_units INTEGER DEFAULT 0,
                    failed_units INTEGER DEFAULT 0, current_unit TEXT DEFAULT '',
                    stage TEXT DEFAULT '', progress REAL DEFAULT 0,
                    target_lang TEXT DEFAULT 'fa', provider TEXT DEFAULT '', model TEXT DEFAULT '',
                    error TEXT DEFAULT '', config TEXT DEFAULT '{}', source_path TEXT DEFAULT '',
                    input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
                    api_requests INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0,
                    result_path TEXT DEFAULT '', result_name TEXT DEFAULT '')"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS job_units (
                    job_id TEXT NOT NULL, idx INTEGER NOT NULL, title TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending', error TEXT DEFAULT '',
                    updated_at REAL DEFAULT 0, PRIMARY KEY(job_id, idx))"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    ts REAL NOT NULL, level TEXT DEFAULT 'info', message TEXT DEFAULT '')"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, priority, position)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_job_units ON job_units(job_id, status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_job_events ON job_events(job_id, id)")
            db.commit()

    # ------------------------------------------------------------------- jobs
    def create_job(self, kind: str, title: str, *, config: dict | None = None, priority: int = 5,
                   total_units: int = 0, target_lang: str = "fa", provider: str = "",
                   model: str = "", source_path: str = "") -> str:
        job_id = uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock, self._connect() as db:
            row = db.execute("SELECT COALESCE(MAX(position),0) FROM jobs").fetchone()
            position = int(row[0]) + 1
            db.execute(
                """INSERT INTO jobs(id,kind,title,status,priority,position,created_at,updated_at,
                       total_units,target_lang,provider,model,config,source_path)
                   VALUES(?,?,?,'queued',?,?,?,?,?,?,?,?,?,?)""",
                (job_id, kind, title or kind, max(1, min(10, int(priority))), position, now, now,
                 max(0, int(total_units)), target_lang, provider, model,
                 json.dumps(config or {}, ensure_ascii=False), source_path or ""),
            )
            db.commit()
        self.add_event(job_id, "تسک ساخته شد")
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._decode(row) if row else None

    def list_jobs(self, status: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM jobs"
        params: list[object] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY CASE WHEN status IN ('running','pausing') THEN 0 ELSE 1 END, priority DESC, position ASC, created_at ASC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock, self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._decode(row) for row in rows]

    def update(self, job_id: str, **fields) -> None:
        clean = {k: v for k, v in fields.items() if k in _FIELDS}
        if not clean:
            return
        clean["updated_at"] = time.time()
        if "config" in clean and not isinstance(clean["config"], str):
            clean["config"] = json.dumps(clean["config"], ensure_ascii=False)
        assignments = ", ".join(f"{key}=?" for key in clean)
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE jobs SET {assignments} WHERE id=?", [*clean.values(), job_id])
            db.commit()

    def add_usage(self, job_id: str, input_tokens: int = 0, output_tokens: int = 0,
                  requests: int = 0, cost_usd: float = 0.0) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE jobs SET input_tokens=input_tokens+?, output_tokens=output_tokens+?,"
                " api_requests=api_requests+?, cost_usd=cost_usd+?, updated_at=? WHERE id=?",
                (max(0, int(input_tokens)), max(0, int(output_tokens)), max(0, int(requests)),
                 max(0.0, float(cost_usd)), time.time(), job_id),
            )
            db.commit()

    def delete(self, job_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM job_units WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM job_events WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            db.commit()

    def next_queued(self) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued'"
                " ORDER BY priority DESC, position ASC, created_at ASC LIMIT 1"
            ).fetchone()
        return self._decode(row) if row else None

    def move(self, job_id: str, delta: int) -> None:
        """Move a queued job up/down by swapping its queue position."""
        with self._lock, self._connect() as db:
            row = db.execute("SELECT id,position,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] != STATUS_QUEUED:
                return
            if delta < 0:
                other = db.execute(
                    "SELECT id,position FROM jobs WHERE status='queued' AND position<?"
                    " ORDER BY position DESC LIMIT 1", (row["position"],)).fetchone()
            else:
                other = db.execute(
                    "SELECT id,position FROM jobs WHERE status='queued' AND position>?"
                    " ORDER BY position ASC LIMIT 1", (row["position"],)).fetchone()
            if not other:
                return
            now = time.time()
            db.execute("UPDATE jobs SET position=?, updated_at=? WHERE id=?", (other["position"], now, row["id"]))
            db.execute("UPDATE jobs SET position=?, updated_at=? WHERE id=?", (row["position"], now, other["id"]))
            db.commit()

    def requeue_orphans(self) -> int:
        """Jobs left running by a crash/restart go back to the queue."""
        now = time.time()
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "UPDATE jobs SET status='queued', stage='بازیابی پس از توقف سرور', updated_at=?"
                " WHERE status IN ('running','pausing')", (now,))
            db.execute(
                "UPDATE job_units SET status='pending', error='' WHERE status='running'")
            db.commit()
            return int(cursor.rowcount or 0)

    def stats(self) -> dict:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()
            queue_row = db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()
        counts = {str(r[0]): int(r[1]) for r in rows}
        return {"counts": counts, "queued": int(queue_row[0]), "total": sum(counts.values())}

    # ------------------------------------------------------------------ units
    def add_units(self, job_id: str, units: list[dict]) -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            for index, unit in enumerate(units):
                db.execute(
                    "INSERT OR IGNORE INTO job_units(job_id,idx,title,status,updated_at) VALUES(?,?,?,'pending',?)",
                    (job_id, int(unit.get("idx", index)), str(unit.get("title", ""))[:300], now),
                )
            db.commit()

    def set_unit(self, job_id: str, idx: int, status: str, error: str = "",
                 title: str | None = None) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO job_units(job_id,idx,status,error,updated_at,title) VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(job_id,idx) DO UPDATE SET status=excluded.status,"
                " error=excluded.error, updated_at=excluded.updated_at,"
                " title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE job_units.title END",
                (job_id, int(idx), status, str(error)[:500], time.time(), str(title or "")[:300]),
            )
            db.commit()

    def units(self, job_id: str) -> list[dict]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT * FROM job_units WHERE job_id=? ORDER BY idx", (job_id,)).fetchall()
        return [dict(row) for row in rows]

    def completed_indexes(self, job_id: str) -> set[int]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT idx FROM job_units WHERE job_id=? AND status='completed'", (job_id,)).fetchall()
        return {int(r[0]) for r in rows}

    # ----------------------------------------------------------------- events
    def add_event(self, job_id: str, message: str, level: str = "info") -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO job_events(job_id,ts,level,message) VALUES(?,?,?,?)",
                       (job_id, time.time(), level, str(message)[:2000]))
            db.commit()

    def events(self, job_id: str, limit: int = 80) -> list[dict]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT ts,level,message FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT ?",
                (job_id, max(1, int(limit))),
            ).fetchall()
        return [{"time": r[0], "level": r[1], "message": r[2]} for r in reversed(rows)]

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _decode(row) -> dict:
        data = dict(row)
        try:
            data["config"] = json.loads(data.get("config") or "{}")
        except ValueError:
            data["config"] = {}
        return data
