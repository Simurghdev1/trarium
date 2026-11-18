"""Background job manager: persistent queue, safe pause/resume, crash recovery.

All processing happens in daemon threads owned by this module, not in the
Streamlit request. A browser reload therefore does not stop a translation, and
a backend restart re-queues jobs that were interrupted.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from trarium.jobs import handlers as handler_module
from trarium.jobs.store import (
    ACTIVE_STATUSES, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED, STATUS_PAUSED,
    STATUS_PAUSING, STATUS_QUEUED, STATUS_RUNNING, JobStore,
)
from trarium.local_state import load_settings, save_settings, trarium_home


class JobPaused(Exception):
    """Raised inside a handler when a safe pause point is reached."""


class JobCancelled(Exception):
    """Raised inside a handler when the user cancelled the job."""


class JobControl:
    def __init__(self):
        self.pause = False
        self.cancel = False


class JobContext:
    """Everything a handler may touch; keeps persistence logic in one place."""

    def __init__(self, manager: "JobManager", job: dict, control: JobControl):
        self.manager = manager
        self.store = manager.store
        self.job = job
        self.job_id = job["id"]
        self.control = control
        self.config = job.get("config") or {}
        self._translator = None

    # ------------------------------------------------------------- lifecycle
    @property
    def directory(self) -> Path:
        path = trarium_home() / "jobs" / self.job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def update(self, **fields) -> None:
        self.store.update(self.job_id, **fields)

    def log(self, message: str, level: str = "info") -> None:
        self.store.add_event(self.job_id, message, level)

    def checkpoint(self) -> None:
        if self.control.cancel:
            raise JobCancelled()
        if self.control.pause:
            raise JobPaused()

    def set_stage(self, stage: str, current_unit: str = "") -> None:
        fields = {"stage": stage}
        if current_unit:
            fields["current_unit"] = current_unit[:300]
        self.update(**fields)

    # ----------------------------------------------------------------- units
    def ensure_units(self, titles: list[str]) -> None:
        if not titles:
            return
        self.store.add_units(self.job_id, [{"idx": i, "title": title} for i, title in enumerate(titles)])
        self.update(total_units=len(titles))
        self._sync_counts()

    def _sync_counts(self) -> None:
        units = self.store.units(self.job_id)
        if not units:
            return
        completed = sum(1 for u in units if u["status"] == "completed")
        failed = sum(1 for u in units if u["status"] == "failed")
        total = len(units)
        self.update(completed_units=completed, failed_units=failed,
                    progress=round((completed + failed) / total, 4),
                    total_units=total)

    def unit_started(self, idx: int, title: str = "") -> None:
        self.store.set_unit(self.job_id, idx, "running", title=title)
        self.update(current_unit=(title or f"بخش {idx + 1}")[:300],
                    stage=self.job.get("stage") or "")

    def unit_done(self, idx: int, *, failed: bool = False, error: str = "", title: str = "",
                  total: int | None = None) -> None:
        self.store.set_unit(self.job_id, idx, "failed" if failed else "completed", error)
        if total and not self.store.units(self.job_id):
            self.update(total_units=total)
        self._sync_counts()
        if failed:
            self.log(f"بخش «{title or idx + 1}» ناموفق بود: {error}", "error")

    def completed_indexes(self) -> set[int]:
        return self.store.completed_indexes(self.job_id)

    def is_done(self, idx: int) -> bool:
        return idx in self.completed_indexes()

    # ------------------------------------------------------------ translator
    def _tokens(self, input_tokens: int, output_tokens: int, requests: int, cost: float, error: bool) -> None:
        if error and not requests:
            return
        self.store.add_usage(self.job_id, input_tokens, output_tokens, requests, cost)

    def make_translator(self):
        if self._translator is not None:
            return self._translator
        from trarium.failover import build_chain
        cfg = self.config
        chain = cfg.get("provider_chain") or []
        if not chain:
            chain = [{"provider": cfg.get("provider") or "openrouter", "model": cfg.get("model") or ""}]
        failover_cfg = cfg.get("failover") or {}
        self._translator = build_chain(
            chain,
            retries=int(failover_cfg.get("retries", 2)),
            cost_cap_usd=failover_cfg.get("cost_cap_usd") or None,
            smart=bool(failover_cfg.get("smart")),
            project=self.job.get("title", ""),
            job_id=self.job_id,
            on_usage=self._tokens,
        )
        return self._translator

    def finish(self, result_path: Path | str, result_name: str = "") -> None:
        path = Path(result_path)
        self.update(result_path=str(path), result_name=result_name or path.name,
                    stage="خروجی آماده شد", progress=1.0)


class JobManager:
    """One dispatcher thread plus a small pool of job worker threads."""

    def __init__(self, store: JobStore | None = None, max_concurrent: int = 1,
                 recover: bool = True):
        self.store = store or JobStore()
        self.max_concurrent = max(1, int(max_concurrent))
        self._handlers: dict[str, callable] = {}
        self._controls: dict[str, JobControl] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._dispatcher: threading.Thread | None = None
        self._queue_paused = bool(load_settings().get("queue_paused", False))
        if recover:
            self._recover()

    # -------------------------------------------------------------- recovery
    def _recover(self) -> None:
        try:
            recovered = self.store.requeue_orphans()
        except Exception:
            recovered = 0
        for job in self.store.list_jobs():
            if job["status"] == "queued" and job.get("stage") == "بازیابی پس از توقف سرور":
                self.store.add_event(job["id"],
                                     "پس از توقف سرور، تسک به صف برگشت و از آخرین واحد ذخیره‌شده ادامه می‌دهد.")

    # --------------------------------------------------------------- handlers
    def register_handler(self, kind: str, handler) -> None:
        self._handlers[kind] = handler

    def handler_for(self, kind: str):
        if kind in self._handlers:
            return self._handlers[kind]
        return getattr(handler_module, f"handle_{kind}", None)

    # ---------------------------------------------------------------- control
    def start(self) -> "JobManager":
        with self._lock:
            if self._dispatcher and self._dispatcher.is_alive():
                return self
            self._stop.clear()
            self._dispatcher = threading.Thread(target=self._dispatch_loop, name="trarium-dispatcher", daemon=True)
            self._dispatcher.start()
        return self

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._lock:
            controls = list(self._controls.values())
            threads = list(self._threads.values())
        for control in controls:
            control.cancel = True
        for thread in threads:
            thread.join(timeout=timeout)
        if self._dispatcher:
            self._dispatcher.join(timeout=timeout)

    @property
    def queue_paused(self) -> bool:
        return self._queue_paused

    def pause_queue(self, paused: bool) -> None:
        self._queue_paused = bool(paused)
        settings = load_settings()
        settings["queue_paused"] = self._queue_paused
        save_settings(settings)

    def set_max_concurrent(self, value: int) -> None:
        self.max_concurrent = max(1, min(8, int(value)))

    def submit(self, kind: str, title: str, *, config: dict | None = None, priority: int = 5,
               source_bytes: bytes | None = None, source_name: str = "",
               units: list[str] | None = None, target_lang: str = "fa",
               provider: str = "", model: str = "") -> str:
        if self.handler_for(kind) is None:
            raise ValueError(f"نوع تسک پشتیبانی نمی‌شود: {kind}")
        job_id = self.store.create_job(kind, title, config=config, priority=priority,
                                       target_lang=target_lang, provider=provider, model=model)
        job_dir = trarium_home() / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        if source_bytes is not None:
            suffix = Path(source_name or "input").suffix.lower()
            path = job_dir / f"input{suffix}"
            path.write_bytes(source_bytes)
            self.store.update(job_id, source_path=str(path))
        if units:
            self.store.add_units(job_id, [{"idx": i, "title": t} for i, t in enumerate(units)])
            self.store.update(job_id, total_units=len(units))
        self.store.add_event(job_id, "در صف قرار گرفت")
        self.start()
        return job_id

    def active_job_ids(self) -> list[str]:
        with self._lock:
            return list(self._controls.keys())

    def pause(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job:
            return
        if job["status"] == STATUS_QUEUED:
            self.store.update(job_id, status=STATUS_PAUSED, stage="متوقف شده")
            self.store.add_event(job_id, "تسک پیش از شروع متوقف شد")
            return
        with self._lock:
            control = self._controls.get(job_id)
        if control and job["status"] in ACTIVE_STATUSES:
            control.pause = True
            self.store.update(job_id, status=STATUS_PAUSING, stage="در حال توقف امن...")
            self.store.add_event(job_id, "درخواست توقف امن ثبت شد؛ پس از واحد جاری متوقف می‌شود")

    def resume(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job or job["status"] not in {STATUS_PAUSED, STATUS_FAILED}:
            return
        self.store.update(job_id, status=STATUS_QUEUED, error="", stage="آمادهٔ ادامه",
                          finished_at=0)
        self.store.add_event(job_id, "از آخرین واحد ذخیره‌شده ادامه می‌دهد")
        self.start()

    def cancel(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job:
            return
        with self._lock:
            control = self._controls.get(job_id)
        if control:
            control.cancel = True
            self.store.update(job_id, status="cancelling", stage="در حال لغو...")
            self.store.add_event(job_id, "درخواست لغو ثبت شد")
        elif job["status"] in {STATUS_QUEUED, STATUS_PAUSED, STATUS_FAILED}:
            self.store.update(job_id, status=STATUS_CANCELLED, finished_at=time.time(),
                              stage="لغو شد", progress=job.get("progress", 0))
            self.store.add_event(job_id, "تسک لغو شد")

    def delete(self, job_id: str, remove_files: bool = True) -> None:
        self.cancel(job_id)
        deadline = time.time() + 3
        while time.time() < deadline:
            with self._lock:
                if job_id not in self._threads:
                    break
            time.sleep(0.1)
        self.store.delete(job_id)
        if remove_files:
            shutil.rmtree(trarium_home() / "jobs" / job_id, ignore_errors=True)

    # ---------------------------------------------------------------- runtime
    def _dispatch_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._queue_paused:
                    with self._lock:
                        active = len(self._controls)
                        slots = self.max_concurrent - active
                    for _ in range(max(0, slots)):
                        job = self.store.next_queued()
                        if not job:
                            break
                        self._launch(job)
            except Exception as exc:  # a broken job must not kill the dispatcher
                try:
                    self.store.add_event("system", f"خطای زمان‌بند: {exc}", "error")
                except Exception:
                    pass
            self._stop.wait(0.4)

    def _launch(self, job: dict) -> None:
        job_id = job["id"]
        with self._lock:
            if job_id in self._controls:
                return
            control = JobControl()
            self._controls[job_id] = control
            thread = threading.Thread(target=self._run_job, args=(job_id, control),
                                      name=f"trarium-job-{job_id}", daemon=True)
            self._threads[job_id] = thread
        self.store.update(job_id, status=STATUS_RUNNING, started_at=job.get("started_at") or time.time(),
                          error="", stage="شروع شد")
        self.store.add_event(job_id, "پردازش آغاز شد")
        thread.start()

    def _run_job(self, job_id: str, control: JobControl) -> None:
        job = self.store.get(job_id)
        try:
            if not job:
                return
            handler = self.handler_for(job["kind"])
            if handler is None:
                raise RuntimeError(f"هندلر برای نوع {job['kind']} وجود ندارد")
            handler(JobContext(self, job, control))
            current = self.store.get(job_id) or {}
            if control.cancel:
                self.store.update(job_id, status=STATUS_CANCELLED, finished_at=time.time(), stage="لغو شد")
            elif control.pause:
                self.store.update(job_id, status=STATUS_PAUSED, stage="متوقف شده (قابل ادامه)")
                self.store.add_event(job_id, "به‌صورت امن متوقف شد؛ با Resume ادامه می‌دهد")
            elif current.get("result_path"):
                self.store.update(job_id, status=STATUS_COMPLETED, finished_at=time.time(),
                                  progress=1.0, stage="کامل شد")
                self.store.add_event(job_id, "تسک با موفقیت کامل شد")
            else:
                raise RuntimeError("هندلر بدون فایل خروجی تمام شد")
        except JobPaused:
            self.store.update(job_id, status=STATUS_PAUSED, stage="متوقف شده (قابل ادامه)")
            self.store.add_event(job_id, "به‌صورت امن متوقف شد؛ با Resume ادامه می‌دهد")
        except JobCancelled:
            self.store.update(job_id, status=STATUS_CANCELLED, finished_at=time.time(),
                              stage="لغو شد")
            self.store.add_event(job_id, "تسک لغو شد", "warning")
        except Exception as exc:
            self.store.update(job_id, status=STATUS_FAILED, finished_at=time.time(),
                              error=str(exc)[:1000], stage="خطا")
            self.store.add_event(job_id, f"خطا: {exc}", "error")
        finally:
            with self._lock:
                self._controls.pop(job_id, None)
                self._threads.pop(job_id, None)
