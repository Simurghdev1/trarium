"""Smart Resume: وضعیت هر مرحلهٔ پردازش را دائمی ذخیره می‌کند.

هر پروژه یک «دفتر مراحل» دارد (ورودی، تعمیر، OCR، ترجمه، خروجی) با وضعیت،
درصد و جزئیات. بعد از ری‌استارت یا کرش، همان‌جا که کار متوقف شده ادامه می‌یابد
و مرحله‌های تمام‌شده دوباره اجرا نمی‌شوند.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

from trarium.local_state import trarium_home

STAGES = ("ingest", "repair", "ocr", "translate", "output")
STAGE_LABELS = {
    "ingest": "خواندن فایل",
    "repair": "تعمیر و بررسی کیفیت",
    "ocr": "OCR",
    "translate": "ترجمه",
    "output": "ساخت خروجی",
}
DONE = "completed"
RUNNING = "running"
FAILED = "failed"
PENDING = "pending"


def project_key(source_path: str | Path, target_lang: str = "fa",
                provider: str = "", model: str = "") -> str:
    """کلید پایدار پروژه: مسیر و اندازه و زبان، نه زمان اجرا."""
    path = Path(str(source_path))
    try:
        size = path.stat().st_size if path.exists() else 0
    except OSError:
        size = 0
    raw = "|".join([path.name, str(size), target_lang or "", provider or "", model or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class ResumeLedger:
    """SQLite-backed stage ledger; safe to create on every run."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else (trarium_home() / "resume.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def _init(self) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS projects ("
                " key TEXT PRIMARY KEY, title TEXT DEFAULT '', source_path TEXT DEFAULT '',"
                " target_lang TEXT DEFAULT 'fa', provider TEXT DEFAULT '', model TEXT DEFAULT '',"
                " status TEXT DEFAULT 'running', created_at REAL NOT NULL, updated_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS stages ("
                " project_key TEXT NOT NULL, stage TEXT NOT NULL, status TEXT DEFAULT 'pending',"
                " progress REAL DEFAULT 0, detail TEXT DEFAULT '', error TEXT DEFAULT '',"
                " started_at REAL DEFAULT 0, finished_at REAL DEFAULT 0,"
                " PRIMARY KEY(project_key, stage))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS units ("
                " project_key TEXT NOT NULL, stage TEXT NOT NULL, unit TEXT NOT NULL,"
                " status TEXT DEFAULT 'pending', payload TEXT DEFAULT '',"
                " updated_at REAL DEFAULT 0, PRIMARY KEY(project_key, stage, unit))"
            )
            db.commit()

    # ---------------------------------------------------------------- project
    def ensure(self, key: str, *, title: str = "", source_path: str = "",
               target_lang: str = "fa", provider: str = "", model: str = "") -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO projects(key,title,source_path,target_lang,provider,model,status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,'running',?,?)"
                " ON CONFLICT(key) DO UPDATE SET updated_at=excluded.updated_at,"
                " title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE projects.title END",
                (key, title, source_path, target_lang, provider, model, now, now),
            )
            db.execute(
                "UPDATE projects SET source_path=CASE WHEN ?<>'' THEN ? ELSE source_path END,"
                " target_lang=?, provider=?, model=? WHERE key=?",
                (source_path, source_path, target_lang, provider, model, key),
            )
            for stage in STAGES:
                db.execute("INSERT OR IGNORE INTO stages(project_key,stage) VALUES(?,?)", (key, stage))
            db.commit()

    def set_status(self, key: str, status: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("UPDATE projects SET status=?, updated_at=? WHERE key=?",
                       (status, time.time(), key))
            db.commit()

    # ----------------------------------------------------------------- stage
    def start(self, key: str, stage: str, detail: str = "") -> None:
        self.ensure(key)
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO stages(project_key,stage,status,progress,detail,started_at)"
                " VALUES(?,?,'running',0,?,?)"
                " ON CONFLICT(project_key,stage) DO UPDATE SET status='running',"
                " detail=excluded.detail, error='', started_at=CASE WHEN stages.started_at=0"
                " THEN excluded.started_at ELSE stages.started_at END",
                (key, stage, detail, now),
            )
            db.execute("UPDATE projects SET status='running', updated_at=? WHERE key=?", (now, key))
            db.commit()

    def complete(self, key: str, stage: str, detail: str = "", progress: float = 1.0) -> None:
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO stages(project_key,stage,status,progress,detail,started_at,finished_at)"
                " VALUES(?,?,'completed',?,?,?,?)"
                " ON CONFLICT(project_key,stage) DO UPDATE SET status='completed',"
                " progress=excluded.progress, detail=excluded.detail, error='',"
                " finished_at=excluded.finished_at,"
                " started_at=CASE WHEN stages.started_at=0 THEN excluded.started_at ELSE stages.started_at END",
                (key, stage, max(0.0, min(1.0, float(progress))), detail, now, now),
            )
            db.commit()

    def fail(self, key: str, stage: str, error: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO stages(project_key,stage,status,error) VALUES(?,?,'failed',?)"
                " ON CONFLICT(project_key,stage) DO UPDATE SET status='failed', error=excluded.error",
                (key, stage, str(error)[:1000]),
            )
            db.execute("UPDATE projects SET status='failed', updated_at=? WHERE key=?",
                       (time.time(), key))
            db.commit()

    def stage(self, key: str, stage: str) -> dict | None:
        with self._lock, self._connect() as db:
            cursor = db.execute("SELECT * FROM stages WHERE project_key=? AND stage=?",
                                (key, stage))
            columns = [item[0] for item in cursor.description]
            row = cursor.fetchone()
        return dict(zip(columns, row)) if row else None

    def is_done(self, key: str, stage: str) -> bool:
        row = self.stage(key, stage)
        return bool(row and row["status"] == DONE)

    def whose_turn(self, key: str) -> str | None:
        """اولین مرحله‌ای که هنوز کامل نشده است."""
        for stage in STAGES:
            row = self.stage(key, stage)
            if not row or row["status"] != DONE:
                return stage
        return None

    def progress(self, key: str) -> float:
        rows = self.stages(key)
        if not rows:
            return 0.0
        return round(sum(1.0 for row in rows if row["status"] == DONE) / len(STAGES), 4)

    def stages(self, key: str) -> list[dict]:
        with self._lock, self._connect() as db:
            cursor = db.execute("SELECT * FROM stages WHERE project_key=? ORDER BY rowid", (key,))
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def snapshot(self, key: str) -> dict:
        with self._lock, self._connect() as db:
            cursor = db.execute("SELECT * FROM projects WHERE key=?", (key,))
            columns = [item[0] for item in cursor.description]
            row = cursor.fetchone()
            project = dict(zip(columns, row)) if row else {}
        stages = self.stages(key)
        next_stage = self.whose_turn(key)
        return {
            "key": key, "project": project, "stages": stages,
            "progress": self.progress(key),
            "next_stage": next_stage,
            "next_stage_label": STAGE_LABELS.get(next_stage or "", ""),
            "resumable": bool(project) and bool(next_stage) and project.get("status") != "completed",
            "resume_from": self.last_completed(key),
        }

    def last_completed(self, key: str) -> str | None:
        done = [row["stage"] for row in self.stages(key) if row["status"] == DONE]
        return done[-1] if done else None

    def list_projects(self, limit: int = 50) -> list[dict]:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC LIMIT ?", (max(1, int(limit)),))
            columns = [item[0] for item in cursor.description]
            projects = [dict(zip(columns, row)) for row in cursor.fetchall()]
        for project in projects:
            project["progress"] = self.progress(project["key"])
            project["next_stage"] = self.whose_turn(project["key"])
            project["next_stage_label"] = STAGE_LABELS.get(project["next_stage"] or "", "")
        return projects

    def clear(self, key: str | None = None) -> None:
        with self._lock, self._connect() as db:
            if key:
                db.execute("DELETE FROM units WHERE project_key=?", (key,))
                db.execute("DELETE FROM stages WHERE project_key=?", (key,))
                db.execute("DELETE FROM projects WHERE key=?", (key,))
            else:
                db.execute("DELETE FROM units")
                db.execute("DELETE FROM stages")
                db.execute("DELETE FROM projects")
            db.commit()

    # ------------------------------------------------------------------ units
    def mark_unit(self, key: str, stage: str, unit: str, status: str, payload: str = "") -> None:
        self.ensure(key)
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO units(project_key,stage,unit,status,payload,updated_at)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(project_key,stage,unit)"
                " DO UPDATE SET status=excluded.status, payload=excluded.payload,"
                " updated_at=excluded.updated_at",
                (key, stage, str(unit)[:300], status, str(payload)[:4000], time.time()),
            )
            db.commit()

    def unit_status(self, key: str, stage: str, unit: str) -> str | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT status FROM units WHERE project_key=? AND stage=? AND unit=?",
                             (key, stage, str(unit)[:300])).fetchone()
        return str(row[0]) if row else None

    def done_units(self, key: str, stage: str) -> set[str]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT unit FROM units WHERE project_key=? AND stage=?"
                              " AND status='completed'", (key, stage)).fetchall()
        return {str(row[0]) for row in rows}


def stage_table(ledger: ResumeLedger, key: str) -> list[dict]:
    """ردیف‌های آمادهٔ نمایش برای جدول مراحل."""
    rows = []
    for row in ledger.stages(key):
        rows.append({
            "مرحله": STAGE_LABELS.get(row["stage"], row["stage"]),
            "وضعیت": {"completed": "✅ کامل", "running": "▶️ در حال اجرا",
                      "failed": "❌ ناموفق", "pending": "⏳ در انتظار"}.get(row["status"], row["status"]),
            "درصد": int(round(float(row.get("progress") or 0) * 100)),
            "جزئیات": row.get("error") or row.get("detail") or "",
        })
    return rows
