"""Persistent background jobs for Trarium."""
from __future__ import annotations

import threading

from trarium.jobs.manager import JobCancelled, JobContext, JobManager, JobPaused
from trarium.jobs.store import JobStore

_MANAGER: JobManager | None = None
_LOCK = threading.Lock()


def get_manager() -> JobManager:
    """Process-wide manager; created lazily so imports stay cheap."""
    global _MANAGER
    with _LOCK:
        if _MANAGER is None:
            _MANAGER = JobManager()
        return _MANAGER


def set_manager(manager: JobManager | None) -> None:
    global _MANAGER
    with _LOCK:
        _MANAGER = manager


__all__ = ["JobStore", "JobManager", "JobContext", "JobPaused", "JobCancelled",
           "get_manager", "set_manager"]
