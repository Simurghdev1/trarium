"""Persistent checkpoints so interrupted jobs resume without resending completed units."""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path


def stable_job_id(source_fingerprint: str, target_lang: str, provider: str = "", model: str = "") -> str:
    raw = "|".join([source_fingerprint, target_lang, provider, model])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class CheckpointStore:
    def __init__(self, path: str | None = None):
        self.path = Path(path or (Path.home() / ".trarium" / "checkpoints.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, source TEXT, meta TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS units (job_id TEXT, unit_key TEXT, value TEXT NOT NULL, PRIMARY KEY(job_id, unit_key))")
            db.commit()

    def load(self, job_id: str) -> dict[str, str]:
        with self._lock, sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT unit_key,value FROM units WHERE job_id=?", (job_id,)).fetchall()
        return {str(k): str(v) for k, v in rows}

    def put(self, job_id: str, unit_key: str, value: str) -> None:
        if not value:
            return
        with self._lock, sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO units(job_id,unit_key,value) VALUES(?,?,?)", (job_id, unit_key, value))
            db.commit()

    def clear(self, job_id: str | None = None) -> None:
        with self._lock, sqlite3.connect(self.path) as db:
            if job_id:
                db.execute("DELETE FROM units WHERE job_id=?", (job_id,))
                db.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
            else:
                db.execute("DELETE FROM units")
                db.execute("DELETE FROM jobs")
            db.commit()
