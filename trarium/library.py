"""کتابخانهٔ دائمی تراریوم: کتاب‌ها، پلی‌لیست‌ها و تنظیمات هر کتاب.

همه‌چیز در SQLite کنار بقیهٔ وضعیت محلی (``~/.trarium``) ذخیره می‌شود؛ پس رفرش
صفحه یا ری‌استارت چیزی را از بین نمی‌برد و هیچ سرور جداگانه‌ای لازم نیست.
افزودن کتاب فقط فایل را کپی و ثبت می‌کند: هیچ درخواست API و هیچ ترجمه‌ای
شروع نمی‌شود.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import threading
import time
import uuid
import zipfile
from pathlib import Path

from trarium.local_state import trarium_home

STATUS_READY = "ready"
STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUSES = (STATUS_READY, STATUS_QUEUED, STATUS_PROCESSING, STATUS_PAUSED, STATUS_COMPLETED, STATUS_FAILED)
STATUS_LABELS = {
    STATUS_READY: "آماده", STATUS_QUEUED: "در صف", STATUS_PROCESSING: "در حال پردازش",
    STATUS_PAUSED: "متوقف", STATUS_COMPLETED: "کامل‌شده", STATUS_FAILED: "ناموفق",
}
STATUS_ICONS = {
    STATUS_READY: "🟢", STATUS_QUEUED: "⏳", STATUS_PROCESSING: "▶️",
    STATUS_PAUSED: "⏸️", STATUS_COMPLETED: "✅", STATUS_FAILED: "❌",
}
_JOB_MAP = {
    "queued": STATUS_QUEUED, "running": STATUS_PROCESSING, "pausing": STATUS_PROCESSING,
    "paused": STATUS_PAUSED, "completed": STATUS_COMPLETED, "failed": STATUS_FAILED,
    "cancelled": STATUS_READY,
}
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
_SORT_KEYS = {
    "added_desc": "added_at DESC", "added_asc": "added_at ASC",
    "title_asc": "title COLLATE NOCASE ASC", "title_desc": "title COLLATE NOCASE DESC",
    "status": "status ASC, title COLLATE NOCASE ASC",
}

_FIELDS = {"title", "author", "category", "status", "job_id", "settings", "cover",
           "source_path", "last_opened"}


class Library:
    def __init__(self, root: str | Path | None = None):
        base = Path(root) if root else (trarium_home() / "library")
        base.mkdir(parents=True, exist_ok=True)
        (base / "files").mkdir(exist_ok=True)
        (base / "covers").mkdir(exist_ok=True)
        self.root = base
        self.path = base / "library.sqlite3"
        self._lock = threading.RLock()
        self._init()

    # ------------------------------------------------------------------ setup
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS books ("
                " id TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT DEFAULT '',"
                " filename TEXT DEFAULT '', ext TEXT DEFAULT '', size INTEGER DEFAULT 0,"
                " sha256 TEXT DEFAULT '', path TEXT DEFAULT '', cover TEXT DEFAULT '',"
                " category TEXT DEFAULT '', status TEXT DEFAULT 'ready',"
                " job_id TEXT DEFAULT '', settings TEXT DEFAULT '{}',"
                " added_at REAL NOT NULL, updated_at REAL NOT NULL, last_opened REAL DEFAULT 0)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS playlists ("
                " id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,"
                " created_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS playlist_items ("
                " playlist_id TEXT NOT NULL, book_id TEXT NOT NULL,"
                " added_at REAL NOT NULL, PRIMARY KEY(playlist_id, book_id))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_books_added ON books(added_at)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_books_sha ON books(sha256)")
            db.commit()

    # ------------------------------------------------------------------ books
    def add_book(self, source: str | Path | bytes, name: str = "", *, title: str = "",
                 author: str = "", category: str = "", copy: bool = True) -> dict:
        """کتاب را ثبت می‌کند؛ هیچ پردازشی شروع نمی‌شود."""
        if isinstance(source, bytes):
            data = source
            filename = name or "book.bin"
        else:
            path = Path(source)
            data = path.read_bytes()
            filename = name or path.name
        if not data:
            raise ValueError("فایل خالی است.")
        filename = Path(filename).name
        ext = Path(filename).suffix.lower()
        digest = hashlib.sha256(data).hexdigest()
        existing = self._by_sha(digest)
        if existing:
            return existing
        book_id = uuid.uuid4().hex[:16]
        stored = self.root / "files" / f"{book_id}{ext}"
        stored.write_bytes(data)
        cover_rel = ""
        try:
            cover_bytes = _extract_cover(data, ext, filename)
            if cover_bytes:
                cover_path = self.root / "covers" / f"{book_id}.img"
                cover_path.write_bytes(cover_bytes)
                cover_rel = str(cover_path)
        except Exception:
            cover_rel = ""
        meta_title = title or Path(filename).stem
        meta_author = author
        if not title:
            try:
                from trarium.extractors import get_extractor
                book = get_extractor(str(stored), use_ocr=False).extract(str(stored))
                extracted = (book.title or "").strip()
                # PDF بدون متادیتا عنوانش را از نام فایلِ کپی‌شده (شناسهٔ هگز)
                # برمی‌گرداند؛ در این حالت نام فایل اصلی درست است.
                if extracted and extracted not in {stored.stem, book_id}:
                    meta_title = extracted[:200]
                meta_author = author or (book.author or "")
            except Exception:
                pass
        now = time.time()
        settings = {"target_lang": "fa"}
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO books(id,title,author,filename,ext,size,sha256,path,cover,category,"
                "status,job_id,settings,added_at,updated_at,last_opened)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,'ready','',?,?,?,0)",
                (book_id, meta_title, meta_author, filename, ext, len(data), digest, str(stored),
                 cover_rel, category, json.dumps(settings, ensure_ascii=False), now, now),
            )
            db.commit()
        return self.get_book(book_id) or {}

    def _by_sha(self, digest: str) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM books WHERE sha256=?", (digest,)).fetchone()
        return self._decode(row) if row else None

    def get_book(self, book_id: str) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        return self._decode(row) if row else None

    def list_books(self, search: str = "", sort: str = "added_desc", status: str | None = None,
                   category: str | None = None, playlist: str | None = None) -> list[dict]:
        clauses, params = [], []
        if search.strip():
            like = f"%{search.strip()}%"
            clauses.append("(books.title LIKE ? OR books.author LIKE ? OR books.filename LIKE ?"
                           " OR books.category LIKE ?)")
            params.extend([like, like, like, like])
        if status:
            clauses.append("books.status=?")
            params.append(status)
        if category:
            clauses.append("books.category=?")
            params.append(category)
        order = _SORT_KEYS.get(sort, _SORT_KEYS["added_desc"])
        sql = "SELECT books.* FROM books"
        if playlist:
            sql += " JOIN playlist_items ON playlist_items.book_id = books.id"
            clauses.append("playlist_items.playlist_id=?")
            params.append(playlist)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {order}"
        with self._lock, self._connect() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._decode(row) for row in rows]

    def update_book(self, book_id: str, **fields) -> None:
        clean = {key: value for key, value in fields.items() if key in _FIELDS}
        if not clean:
            return
        if "settings" in clean and not isinstance(clean["settings"], str):
            clean["settings"] = json.dumps(clean["settings"], ensure_ascii=False)
        clean["updated_at"] = time.time()
        assignments = ", ".join(f"{key}=?" for key in clean)
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE books SET {assignments} WHERE id=?", [*clean.values(), book_id])
            db.commit()

    def set_status(self, book_id: str, status: str, job_id: str = "") -> None:
        if status not in STATUSES:
            raise ValueError(f"وضعیت نامعتبر: {status}")
        fields = {"status": status}
        if job_id:
            fields["job_id"] = job_id
        self.update_book(book_id, **fields)

    def set_settings(self, book_id: str, settings: dict) -> dict:
        book = self.get_book(book_id)
        if not book:
            raise KeyError("کتاب پیدا نشد.")
        merged = dict(book.get("settings") or {})
        merged.update(settings or {})
        self.update_book(book_id, settings=merged)
        return merged

    def delete_book(self, book_id: str, remove_file: bool = False) -> bool:
        """حذف از کتابخانه؛ فایل اصلی فقط با درخواست صریح پاک می‌شود."""
        book = self.get_book(book_id)
        if not book:
            return False
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM playlist_items WHERE book_id=?", (book_id,))
            db.execute("DELETE FROM books WHERE id=?", (book_id,))
            db.commit()
        if book.get("cover"):
            try:
                Path(book["cover"]).unlink(missing_ok=True)
            except OSError:
                pass
        if remove_file and book.get("path"):
            try:
                Path(book["path"]).unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def touch(self, book_id: str) -> None:
        self.update_book(book_id, last_opened=time.time())

    def categories(self) -> list[str]:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT DISTINCT category FROM books WHERE category<>''"
                              " ORDER BY category").fetchall()
        return [str(row[0]) for row in rows]

    def refresh_from_jobs(self) -> int:
        """وضعیت کتاب‌ها را با تسک‌های پس‌زمینه هم‌گام می‌کند (فقط خواندن)."""
        from trarium.jobs.store import JobStore
        store = JobStore()
        changed = 0
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT id,job_id,status FROM books WHERE job_id<>''").fetchall()
        for row in rows:
            job = store.get(str(row["job_id"]))
            if not job:
                continue
            status = _JOB_MAP.get(str(job.get("status")), None)
            if status and status != row["status"]:
                self.update_book(str(row["id"]), status=status)
                changed += 1
        return changed

    # -------------------------------------------------------------- playlists
    def create_playlist(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("نام پلی‌لیست خالی است.")
        playlist_id = uuid.uuid4().hex[:12]
        with self._lock, self._connect() as db:
            try:
                db.execute("INSERT INTO playlists(id,name,created_at) VALUES(?,?,?)",
                           (playlist_id, name, time.time()))
            except sqlite3.IntegrityError:
                row = db.execute("SELECT id FROM playlists WHERE name=?", (name,)).fetchone()
                return self.playlist(str(row[0])) if row else {}
            db.commit()
        return self.playlist(playlist_id) or {}

    def playlist(self, playlist_id: str) -> dict | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT playlists.*,"
                             " (SELECT COUNT(*) FROM playlist_items WHERE playlist_id=playlists.id)"
                             " AS count FROM playlists WHERE id=?", (playlist_id,)).fetchone()
        return dict(row) if row else None

    def list_playlists(self) -> list[dict]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT playlists.*, (SELECT COUNT(*) FROM playlist_items WHERE"
                " playlist_id=playlists.id) AS count FROM playlists ORDER BY name").fetchall()
        return [dict(row) for row in rows]

    def add_to_playlist(self, playlist_id: str, book_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT OR IGNORE INTO playlist_items(playlist_id,book_id,added_at)"
                       " VALUES(?,?,?)", (playlist_id, book_id, time.time()))
            db.commit()

    def remove_from_playlist(self, playlist_id: str, book_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM playlist_items WHERE playlist_id=? AND book_id=?",
                       (playlist_id, book_id))
            db.commit()

    def delete_playlist(self, playlist_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM playlist_items WHERE playlist_id=?", (playlist_id,))
            db.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
            db.commit()

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        with self._lock, self._connect() as db:
            rows = db.execute("SELECT status, COUNT(*) FROM books GROUP BY status").fetchall()
            totals = db.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM books").fetchone()
            playlists = db.execute("SELECT COUNT(*) FROM playlists").fetchone()
        counts = {str(row[0]): int(row[1]) for row in rows}
        return {
            "total": int(totals[0]), "bytes": int(totals[1]), "counts": counts,
            "playlists": int(playlists[0]),
            "completed": counts.get(STATUS_COMPLETED, 0),
            "active": counts.get(STATUS_PROCESSING, 0) + counts.get(STATUS_QUEUED, 0),
            "failed": counts.get(STATUS_FAILED, 0),
        }

    # ---------------------------------------------------------------- helpers
    def _decode(self, row) -> dict:
        data = dict(row)
        try:
            data["settings"] = json.loads(data.get("settings") or "{}")
        except ValueError:
            data["settings"] = {}
        cover = str(data.get("cover") or "")
        data["cover_exists"] = bool(cover) and Path(cover).exists()
        data["status_label"] = STATUS_LABELS.get(data.get("status"), data.get("status"))
        data["status_icon"] = STATUS_ICONS.get(data.get("status"), "•")
        data["size_mb"] = round(float(data.get("size") or 0) / (1024 * 1024), 2)
        return data


def _extract_cover(data: bytes, ext: str, filename: str) -> bytes | None:
    """جلد را از خود فایل می‌سازد؛ هیچ تصویر ساختگی اضافه نمی‌شود."""
    if ext in _IMAGE_EXTENSIONS:
        return data
    if ext == ".pdf":
        try:
            import pymupdf
            document = pymupdf.open(stream=data, filetype="pdf")
            if not len(document):
                return None
            pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(0.6, 0.6))
            payload = pixmap.tobytes("png")
            document.close()
            return payload
        except Exception:
            return None
    if ext == ".epub":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                candidates = [name for name in archive.namelist()
                              if name.lower().endswith(_IMAGE_EXTENSIONS)]
                candidates.sort(key=lambda name: ("cover" not in name.lower(), len(name)))
                if candidates:
                    return archive.read(candidates[0])
        except Exception:
            return None
    return None


def book_title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    return re.sub(r"[_\-]+", " ", stem).strip() or stem
