"""Streamlit panels for the expansion features.

Kept outside ``streamlit_app`` so the original page logic stays small and these
panels can reuse the same backend services as the CLI and job workers.
"""
from __future__ import annotations

import io
import json
import tempfile
import time
import zipfile
from pathlib import Path

import streamlit as st

from trarium.jobs import get_manager
from trarium.local_state import get_provider_config, mask_secret
from trarium.manga import (OPERATIONS, MangaSettings, availability, enhance,
                           enhance_file)
from trarium.monitor import format_duration, job_snapshot
from trarium.provider_registry import LABELS, PROVIDERS, has_credentials
from trarium.usage import KIND_LLM, KIND_OCR, default_store, estimate_cost
from trarium.websites import translate_site

STATUS_LABELS = {
    "queued": "در صف", "running": "در حال اجرا", "pausing": "در حال توقف",
    "paused": "متوقف", "completed": "کامل شد", "failed": "ناموفق",
    "cancelled": "لغو شد", "cancelling": "در حال لغو",
}
STATUS_ICONS = {
    "queued": "⏳", "running": "▶️", "pausing": "⏸️", "paused": "⏸️",
    "completed": "✅", "failed": "❌", "cancelled": "🚫", "cancelling": "🚫",
}
KIND_LABELS = {
    "book": "کتاب/سند", "pdf": "PDF", "novel": "وب‌ناول", "website": "وب‌سایت",
    "manga": "مانگا",
}


def _manager():
    return get_manager().start()


def _read_bytes(path: str | Path, limit: int = 200 * 1024 * 1024) -> bytes | None:
    file = Path(path)
    if not file.exists() or file.stat().st_size > limit:
        return None
    return file.read_bytes()


def submit_upload_job(ctx: dict, uploaded, kind: str | None = None, config: dict | None = None) -> str:
    """Queue an uploaded file for background processing."""
    suffix = Path(uploaded.name).suffix.lower()
    if kind is None:
        if suffix == ".pdf":
            kind = "pdf"
        elif suffix in {".cbz", ".cbr"}:
            kind = "manga"
        else:
            kind = "book"
    config = dict(config or ctx["job_config"]("pdf" if kind == "pdf" else "txt"))
    # Remember the real file name; the job's own file is always input.<ext>.
    config.setdefault("display_name", uploaded.name)
    chain = config.get("provider_chain") or []
    missing = [item["provider"] for item in chain if not has_credentials(item.get("provider", ""))]
    if missing:
        raise ValueError("برای اجرای پس‌زمینه، کلید این ارائه‌دهنده‌ها را ذخیره کنید: "
                         + "، ".join(LABELS.get(p, p) for p in missing))
    return _manager().submit(
        kind, Path(uploaded.name).stem, config=config, source_bytes=uploaded.getvalue(),
        source_name=uploaded.name, target_lang=ctx["target_lang"],
        provider=ctx["provider"], model=ctx["model"],
    )


# ------------------------------------------------------------------- monitoring
def _job_status_line(job: dict) -> str:
    return f"{STATUS_ICONS.get(job['status'], '')} {STATUS_LABELS.get(job['status'], job['status'])}"


def _draw_job(job_id: str, prefix: str = "main") -> None:
    snap = job_snapshot(job_id, usage=default_store())
    if not snap:
        st.info("این تسک پیدا نشد (ممکن است حذف شده باشد).")
        return
    job = snap["job"]
    st.markdown(f"**{job['title']}** · {KIND_LABELS.get(job['kind'], job['kind'])} · "
                f"{_job_status_line(job)}")
    st.progress(min(1.0, snap["percent"] / 100), text=f"{snap['percent']:.1f}٪ — {snap['stage'] or '—'}")
    if snap.get("eta_seconds") is not None and job["status"] in {"running", "pausing", "queued"}:
        st.caption(f"زمان تقریبی باقی‌مانده: {format_duration(snap['eta_seconds'])} "
                   f"(بر اساس سرعت واقعی بخش‌های تکمیل‌شده)")
    cols = st.columns(4)
    cols[0].metric("توکن ورودی", f"{snap['input_tokens']:,}")
    cols[1].metric("توکن خروجی", f"{snap['output_tokens']:,}")
    cols[2].metric("مجموع توکن", f"{snap['total_tokens']:,}")
    cols[3].metric("درخواست API", f"{snap['api_requests']:,}")
    cols = st.columns(4)
    cols[0].metric("بخش‌ها", f"{snap['completed']}/{snap['total']}")
    cols[1].metric("ناموفق", snap["failed"])
    cols[2].metric("هزینه تخمینی", f"${snap['cost_usd']:.4f}")
    cols[3].metric("زمان سپری‌شده", format_duration(snap["elapsed_seconds"]))
    st.caption(f"ارائه‌دهنده: {snap['provider']} · مدل: {snap['model']} · "
               f"بخش جاری: {job.get('current_unit') or '—'}")
    if snap["error"]:
        st.error(snap["error"])
    if job.get("result_path"):
        data = _read_bytes(job["result_path"])
        if data:
            st.download_button("⬇️ دانلود خروجی", data, job.get("result_name") or Path(job["result_path"]).name,
                               "application/octet-stream", key=f"dl_{job_id}_{prefix}")
        else:
            st.caption("فایل خروجی بزرگ‌تر از حد مجاز است؛ از پوشهٔ خروجی تسک بردارید.")
    with st.expander("📜 رویدادها و خطاها"):
        events = snap["events"]
        if not events:
            st.caption("رویدادی ثبت نشده است.")
        for event in events:
            icon = {"error": "❌", "warning": "⚠️"}.get(event["level"], "•")
            stamp = time.strftime("%H:%M:%S", time.localtime(event["time"]))
            st.caption(f"{icon} {stamp} — {event['message']}")


def render_job_monitor(job_id: str, live: bool = False, prefix: str = "main") -> None:
    if live:
        @st.fragment(run_every="2s")
        def _live_panel():
            _draw_job(job_id, prefix)
        _live_panel()
    else:
        _draw_job(job_id, prefix)


def render_foreground_details(result, provider: str, model: str, target_lang: str) -> None:
    store = default_store()
    today = store.summary("today")
    cost = estimate_cost(provider, model, result.estimated_input_tokens, result.estimated_output_tokens)
    st.markdown("#### 🔎 جزئیات ترجمه")
    cols = st.columns(4)
    cols[0].metric("توکن ورودی", f"{result.estimated_input_tokens:,}")
    cols[1].metric("توکن خروجی", f"{result.estimated_output_tokens:,}")
    cols[2].metric("هزینه تخمینی", f"${cost:.4f}")
    cols[3].metric("زمان", f"{result.elapsed_seconds:.1f} ثانیه")
    st.caption(f"وضعیت: ترجمه کامل شد · زبان: {target_lang} · ارائه‌دهنده: {provider} · مدل: {model} · "
               f"درخواست API: {result.api_requests} · Cache/Checkpoint: {result.cache_hits + result.checkpoint_hits}")
    cols = st.columns(3)
    cols[0].metric("مصرف امروز", f"{today['total_tokens']:,} توکن")
    month = store.summary("month")
    cols[1].metric("مصرف این ماه", f"{month['total_tokens']:,} توکن")
    cols[2].metric("هزینهٔ ماه (تخمینی)", f"${month['cost_usd']:.3f}")
    st.caption("اعداد هزینه تخمینی‌اند؛ سهمیهٔ رسمی فقط وقتی نمایش داده می‌شود که خود ارائه‌دهنده آن را برگرداند.")


# ------------------------------------------------------------------------ queue
def render_queue_tab(ctx: dict) -> None:
    manager = _manager()
    st.markdown("### 🗂️ صف و تسک‌های پس‌زمینه")
    st.caption("هر تسک در سرور اجرا می‌شود: بستن یا رفرش صفحه آن را متوقف نمی‌کند. "
               "وضعیت، مصرف و خروجی در پایگاه‌دادهٔ محلی ذخیره می‌شوند.")

    requested = str(st.query_params.get("job") or "")
    if requested:
        st.markdown("#### 📡 پایش تسک")
        render_job_monitor(requested, live=bool(ctx.get("show_details")), prefix="top")
        st.divider()

    controls = st.columns(3)
    with controls[0]:
        paused = st.toggle("توقف صف", value=manager.queue_paused, key="queue_paused_toggle",
                           help="صف متوقف می‌شود اما تسک در حال اجرا ادامه می‌یابد.")
        if paused != manager.queue_paused:
            manager.pause_queue(paused)
    with controls[1]:
        concurrency = st.slider("پردازش هم‌زمان", 1, 4, manager.max_concurrent, key="queue_concurrency")
        manager.set_max_concurrent(concurrency)
    with controls[2]:
        auto = st.checkbox("به‌روزرسانی خودکار فهرست", value=True, key="queue_autorefresh")

    with st.expander("➕ افزودن تسک جدید", expanded=not manager.store.list_jobs()):
        files = st.file_uploader("فایل‌ها (چند فایل هم‌زمان مجاز است)", type=None,
                                 accept_multiple_files=True, key="queue_uploader")
        q1, q2, q3 = st.columns(3)
        with q1:
            priority = st.slider("اولویت", 1, 10, 5, key="queue_priority",
                                 help="عدد بیشتر زودتر اجرا می‌شود.")
        with q2:
            out_fmt = st.selectbox("فرمت خروجی", ["txt", "epub", "html", "pdf"], key="queue_format")
            st.caption("برای زیرنویس، Word/PowerPoint/Excel، JSON/CSV، نوت‌بوک و فایل کد، خروجی همیشه با فرمت خودِ فایل ساخته می‌شود.")
        with q3:
            kind_override = st.selectbox("نوع تسک", ["تشخیص خودکار", "متن/کتاب", "PDF", "مانگا (CBZ/تصویر)"],
                                         key="queue_kind")
        if st.button("افزودن فایل‌ها به صف", type="primary", key="queue_add_files") and files:
            added = []
            for uploaded in files:
                try:
                    kind = None
                    if kind_override == "PDF":
                        kind = "pdf"
                    elif kind_override == "متن/کتاب":
                        kind = "book"
                    elif kind_override.startswith("مانگا"):
                        kind = "manga"
                    config = ctx["job_config"]("pdf" if kind == "pdf" else out_fmt)
                    detected = kind or (
                        "pdf" if Path(uploaded.name).suffix.lower() == ".pdf"
                        else "manga" if Path(uploaded.name).suffix.lower() in {".cbz", ".cbr"}
                        else "book")
                    job_id = _manager().submit(
                        detected,
                        Path(uploaded.name).stem, config=config, source_bytes=uploaded.getvalue(),
                        source_name=uploaded.name, target_lang=ctx["target_lang"],
                        provider=ctx["provider"], model=ctx["model"], priority=priority,
                    )
                    added.append(job_id)
                except Exception as exc:
                    st.error(f"{uploaded.name}: {exc}")
            if added:
                st.success(f"{len(added)} تسک به صف اضافه شد.")

        st.markdown("**وب‌ناول با محدودهٔ فصل**")
        n1, n2, n3, n4 = st.columns(4)
        with n1:
            novel_url = st.text_input("لینک شروع", key="queue_novel_url")
        with n2:
            novel_count = st.number_input("تعداد فصل برای دریافت", 1, 500, 10, key="queue_novel_count")
        with n3:
            chapter_start = st.number_input("از فصل", 1, 10_000, 1, key="queue_chapter_start")
        with n4:
            chapter_end = st.number_input("تا فصل", 0, 10_000, 0, key="queue_chapter_end",
                                           help="۰ یعنی تا آخرین فصل دریافت‌شده.")
        novel_fmt = st.selectbox("فرمت خروجی وب‌ناول", ["epub", "txt", "html", "pdf"], key="queue_novel_fmt")
        if st.button("افزودن وب‌ناول به صف", key="queue_add_novel"):
            if not novel_url.strip():
                st.warning("لینک فصل شروع را وارد کنید.")
            elif chapter_end and int(chapter_start) > int(chapter_end):
                st.error("فصل شروع نمی‌تواند بعد از فصل پایان باشد.")
            else:
                try:
                    config = ctx["job_config"](novel_fmt, extra={
                        "url": novel_url.strip(), "chapter_count": int(novel_count),
                        "chapter_start": int(chapter_start),
                        "chapter_end": int(chapter_end) or None,
                        "use_browser": False, "novel_title": "Web Novel",
                    })
                    job_id = _manager().submit("novel", "وب‌ناول: " + novel_url.strip()[:60],
                                               config=config, target_lang=ctx["target_lang"],
                                               provider=ctx["provider"], model=ctx["model"],
                                               priority=priority)
                    st.success(f"تسک وب‌ناول در صف قرار گرفت: {job_id}")
                except Exception as exc:
                    st.error(str(exc))

    def _job_list():
        jobs = manager.store.list_jobs(limit=60)
        if not jobs:
            st.info("هنوز تسکی ثبت نشده است.")
            return
        for job in jobs:
            job_id = job["id"]
            with st.container(border=True):
                head = st.columns([4, 2, 2])
                head[0].markdown(f"**{job['title']}**  \n{KIND_LABELS.get(job['kind'], job['kind'])} · "
                                 f"{_job_status_line(job)} · {int((job.get('progress') or 0) * 100)}٪")
                head[1].caption(f"اولویت {job['priority']} · {job['completed_units']}"
                                f"/{job['total_units']} واحد")
                head[2].caption(f"{job['input_tokens'] + job['output_tokens']:,} توکن · "
                                f"${job.get('cost_usd') or 0:.4f}")
                if job.get("error"):
                    st.caption(f"❌ {job['error']}")
                st.progress(min(1.0, float(job.get("progress") or 0)),
                            text=job.get("stage") or "")
                buttons = st.columns(6)
                if job["status"] in {"running", "pausing"}:
                    if buttons[0].button("⏸️ Pause", key=f"pause_{job_id}"):
                        manager.pause(job_id); st.rerun()
                if job["status"] in {"paused", "failed"}:
                    if buttons[1].button("▶️ Resume", key=f"resume_{job_id}"):
                        manager.resume(job_id); st.rerun()
                if job["status"] in {"queued", "running", "paused", "pausing"}:
                    if buttons[2].button("🚫 Cancel", key=f"cancel_{job_id}"):
                        manager.cancel(job_id); st.rerun()
                if job["status"] == "queued":
                    if buttons[3].button("⬆️", key=f"up_{job_id}", help="بالا بردن در صف"):
                        manager.store.move(job_id, -1); st.rerun()
                    if buttons[4].button("⬇️", key=f"down_{job_id}", help="پایین بردن در صف"):
                        manager.store.move(job_id, 1); st.rerun()
                confirm_key = f"confirm_delete_{job_id}"
                if not st.session_state.get(confirm_key):
                    if buttons[5].button("🗑️", key=f"delete_{job_id}", help="حذف تسک"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    st.warning("این تسک و اطلاعات وابسته‌اش حذف شود؟ خروجی هم پاک می‌شود.")
                    yes, no = st.columns(2)
                    if yes.button("بله، حذف کن", type="primary", key=f"confirm_yes_{job_id}"):
                        manager.delete(job_id)
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                    if no.button("انصراف", key=f"confirm_no_{job_id}"):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                with st.expander("📡 جزئیات"):
                    render_job_monitor(job_id, live=False, prefix=f"q{job_id}")

    if auto:
        @st.fragment(run_every="3s")
        def _auto_list():
            _job_list()
        _auto_list()
    else:
        _job_list()


# ---------------------------------------------------------------------- website
def render_website_tab(ctx: dict) -> None:
    st.markdown("### 🌐 ترجمه وب‌سایت")
    st.caption("متن صفحه‌ها استخراج و ترجمه می‌شود و ساختار HTML حفظ می‌ماند. "
               "robots.txt، دامنهٔ یکسان و خطاهای ۴۰۱/۴۰۳/۴۲۹ رعایت می‌شوند.")
    url = st.text_input("نشانی صفحه", placeholder="https://example.com/article", key="site_url")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        max_pages = st.number_input("حداکثر صفحه", 1, 200, 1, key="site_max_pages")
    with c2:
        same_domain = st.checkbox("فقط همین دامنه", value=True, key="site_same_domain")
    with c3:
        use_browser = st.checkbox("مرورگر برای صفحات JS", value=False, key="site_browser",
                                  help="نیازمند نصب Playwright است.")
    with c4:
        out_fmt = st.selectbox("فرمت خروجی", ["html", "txt", "epub"], key="site_format")
    delay = st.slider("تأخیر بین صفحه‌ها (ثانیه)", 0.0, 3.0, 0.4, 0.1, key="site_delay")

    b1, b2 = st.columns(2)
    run_now = b1.button("🚀 ترجمهٔ وب‌سایت", type="primary", key="site_run")
    queue_now = b2.button("🕓 افزودن به صف", key="site_queue")

    if queue_now:
        if not url.strip():
            st.warning("نشانی صفحه را وارد کنید.")
        else:
            try:
                config = ctx["job_config"](out_fmt, extra={
                    "url": url.strip(), "max_pages": int(max_pages),
                    "same_domain": bool(same_domain), "use_browser": bool(use_browser),
                    "delay": float(delay),
                })
                job_id = _manager().submit("website", "وب‌سایت: " + url.strip()[:60],
                                           config=config, target_lang=ctx["target_lang"],
                                           provider=ctx["provider"], model=ctx["model"])
                st.query_params["job"] = job_id
                st.success(f"تسک وب‌سایت در صف قرار گرفت: {job_id}")
            except Exception as exc:
                st.error(str(exc))

    if run_now:
        if not url.strip():
            st.warning("نشانی صفحه را وارد کنید.")
            return
        status = st.status("دریافت و ترجمهٔ صفحه‌ها...", expanded=True)
        progress = status.progress(0.0, text="شروع")

        def on_page(index, page_url, page_status, error=""):
            progress.progress(min(0.99, (index + 1) / max(1, int(max_pages))),
                              text=f"{page_url} — {'انجام شد' if page_status == 'completed' else page_status}")

        try:
            result = translate_site(
                url.strip(), ctx["resolve_translator"](project=url.strip()),
                target_lang=ctx["target_lang"], max_pages=int(max_pages),
                same_domain=bool(same_domain), delay=float(delay),
                allow_browser=bool(use_browser), on_page=on_page,
            )
            progress.progress(1.0, text="پایان")
            status.update(label="ترجمهٔ وب‌سایت کامل شد", state="complete")
        except Exception as exc:
            status.update(label="خطا", state="error")
            st.error(f"❌ {exc}")
            return
        rows = [{"صفحه": p.url, "عنوان": p.title, "وضعیت": STATUS_LABELS.get(p.status, p.status),
                 "بلوک": p.blocks, "کاراکتر": p.chars, "خطا": p.error} for p in result.pages]
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption(f"{result.pages_ok} صفحه موفق · {result.pages_failed} ناموفق · "
                   f"{result.total_chars:,} کاراکتر")
        directory = Path(tempfile.mkdtemp())
        path = result.save(directory, out_fmt)
        data = path.read_bytes()
        st.download_button("⬇️ دانلود خروجی", data, path.name,
                           "application/zip" if path.suffix == ".zip" else "text/plain",
                           type="primary", key="site_download")
        completed = [p for p in result.pages if p.status == "completed"]
        if completed:
            with st.expander("👀 پیش‌نمایش HTML ترجمه‌شده"):
                st.text_area("HTML", completed[0].html[:12000], height=320, key="site_preview")


# ------------------------------------------------------------------------ manga
def render_manga_tab(ctx: dict) -> None:
    st.markdown("### 🖼️ افزایش کیفیت مانگا")
    st.caption("تصاویر اصلی هرگز تغییر نمی‌کنند یا بازنویسی نمی‌شوند؛ خروجی در فایل جدید ساخته می‌شود. "
               "اگر پردازش تصویری شکست بخورد، همان تصویر اصلی در خروجی می‌آید.")
    avail = availability()
    st.caption("در دسترس بودن پردازش‌ها: Pillow ✅ · NumPy ✅ · OpenCV "
               f"{'✅' if avail['opencv'] else '❌ (معادل Pillow)'} · Real-ESRGAN "
               f"{'✅' if avail['realesrgan'] else '❌ (نیاز به نصب و وزن مدل)'} · SwinIR "
               f"{'✅' if avail['swinir'] else '❌ (نیاز به نصب و وزن مدل)'}")
    uploads = st.file_uploader("تصاویر یا فایل ZIP/CBZ", type=None, accept_multiple_files=True,
                               key="manga_uploader")
    m1, m2, m3 = st.columns(3)
    with m1:
        scale = st.radio("رزولوشن", ["auto", "1x", "2x", "4x"], horizontal=True, key="manga_scale")
    with m2:
        max_side = st.number_input("حداکثر بُعد خروجی", 512, 8192, 4096, 256, key="manga_max_side")
    with m3:
        quality = st.slider("کیفیت JPEG/WebP", 60, 100, 92, key="manga_quality")
    default_ops = [name for name in OPERATIONS if name not in {"realesrgan", "swinir"}]
    selected = st.multiselect("عملیات (هر کدام جداگانه فعال/غیرفعال)", list(OPERATIONS),
                              default=default_ops, key="manga_ops",
                              format_func=lambda key: OPERATIONS[key])
    settings = MangaSettings(scale=scale, operations=tuple(selected or default_ops),
                             auto=(scale == "auto"), max_side=int(max_side), jpeg_quality=int(quality))

    if uploads:
        first = uploads[0]
        if st.button("🔍 پیش‌نمایش و مقایسه", key="manga_preview"):
            try:
                from PIL import Image
                image = Image.open(io.BytesIO(first.getvalue()))
                enhanced, report = enhance(image, settings)
                left, right = st.columns(2)
                left.image(image, caption=f"اصلی {image.width}×{image.height}", use_container_width=True)
                right.image(enhanced, caption=f"خروجی {enhanced.width}×{enhanced.height}",
                            use_container_width=True)
                st.caption(" · ".join(f"{s['name']}: {s['detail']}" for s in report["steps"]))
                with st.expander("گزارش پردازش"):
                    st.json(report)
            except Exception as exc:
                st.error(f"❌ {exc}")

        b1, b2 = st.columns(2)
        if b1.button("🎨 افزایش کیفیت و ساخت ZIP", type="primary", key="manga_run"):
            reports = []
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive, tempfile.TemporaryDirectory() as td:
                for uploaded in uploads:
                    name = Path(uploaded.name)
                    if name.suffix.lower() in {".zip", ".cbz"}:
                        try:
                            with zipfile.ZipFile(io.BytesIO(uploaded.getvalue())) as source:
                                for inner in source.namelist():
                                    if inner.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
                                        temp_in = Path(td) / Path(inner).name
                                        temp_in.write_bytes(source.read(inner))
                                        out = Path(td) / f"out_{len(reports)}_{temp_in.name}"
                                        report = enhance_file(temp_in, out, settings)
                                        reports.append(report)
                                        archive.writestr(temp_in.name, out.read_bytes() if out.exists() else temp_in.read_bytes())
                        except Exception as exc:
                            reports.append({"ok": False, "error": str(exc), "source": uploaded.name})
                        continue
                    temp_in = Path(td) / name.name
                    temp_in.write_bytes(uploaded.getvalue())
                    out = Path(td) / f"out_{len(reports)}_{name.name}"
                    report = enhance_file(temp_in, out, settings)
                    reports.append(report)
                    archive.writestr(name.name, out.read_bytes() if out.exists() else temp_in.read_bytes())
                archive.writestr("report.json", json.dumps(reports, ensure_ascii=False, indent=2))
            st.download_button("⬇️ دانلود ZIP خروجی", buffer.getvalue(), "enhanced_manga.zip",
                               "application/zip", type="primary", key="manga_download")
            ok = sum(1 for r in reports if r.get("ok"))
            st.caption(f"{ok}/{len(reports)} تصویر پردازش شد. گزارش کامل داخل ZIP است.")

        if b2.button("🕓 افزودن به صف پس‌زمینه", key="manga_queue"):
            try:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for uploaded in uploads:
                        if Path(uploaded.name).suffix.lower() in {".zip", ".cbz"}:
                            with zipfile.ZipFile(io.BytesIO(uploaded.getvalue())) as source:
                                for inner in source.namelist():
                                    if inner.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")):
                                        archive.writestr(Path(inner).name, source.read(inner))
                        else:
                            archive.writestr(Path(uploaded.name).name, uploaded.getvalue())
                config = ctx["job_config"]("zip", extra={"manga": settings.to_dict()})
                job_id = _manager().submit("manga", "مانگا: " + Path(uploads[0].name).stem,
                                           config=config, source_bytes=buffer.getvalue(),
                                           source_name="manga.zip", target_lang=ctx["target_lang"],
                                           provider=ctx["provider"], model=ctx["model"])
                st.query_params["job"] = job_id
                st.success(f"تسک مانگا در صف قرار گرفت: {job_id}")
            except Exception as exc:
                st.error(str(exc))


# ------------------------------------------------------------------------ usage
def render_usage_tab(ctx: dict) -> None:
    usage = default_store()
    st.markdown("### 📊 مصرف و هزینهٔ ارائه‌دهنده‌ها")
    st.caption("مصرف هر درخواست به‌صورت محلی ثبت می‌شود. اطلاعات «تخمینی» از داده‌های رسمی ارائه‌دهنده جدا هستند؛ "
               "چون API صورتحساب ارائه‌دهنده‌ها عمومی نیست، سهمیهٔ رسمی فقط وقتی نمایش داده می‌شود که خودشان برگردانند.")
    provider_options = ["همه"] + PROVIDERS
    selected = st.selectbox("ارائه‌دهنده", provider_options, key="usage_provider")
    provider = None if selected == "همه" else selected

    for kind, title in ((KIND_LLM, "🧠 LLM API"), (KIND_OCR, "🔍 OCR API")):
        st.markdown(f"#### {title}")
        today = usage.summary("today", provider=provider, kind=kind)
        month = usage.summary("month", provider=provider, kind=kind)
        cols = st.columns(4)
        cols[0].metric("توکن امروز", f"{today['total_tokens']:,}")
        cols[1].metric("درخواست امروز", f"{today['requests']:,}")
        cols[2].metric("توکن این ماه", f"{month['total_tokens']:,}")
        cols[3].metric("هزینهٔ ماه (تخمینی)", f"${month['cost_usd']:.4f}")
        if kind == KIND_LLM:
            series = usage.daily_series(30, provider=provider, kind=kind)
            if series:
                st.bar_chart({"روز": [row["day"] for row in series],
                              "توکن": [row["tokens"] for row in series]}, x="روز", y="توکن")
            months = usage.monthly_series(6, provider=provider, kind=kind)
            if months:
                st.caption("مصرف ماهانه: " + " · ".join(
                    f"{row['month']}: {row['tokens']:,}" for row in months))
        else:
            pages = today["requests"]
            st.caption(f"صفحات/درخواست‌های OCR امروز: {pages:,} · این ماه: {month['requests']:,}")

        with st.expander(f"⚙️ سهمیه و محدودیت {title}"):
            if provider:
                status = usage.limit_status(provider, kind)
                st.caption(f"آخرین به‌روزرسانی: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(status['updated_at']))}")
                if status["exceeded"]:
                    st.error("سهمیهٔ محلی پر شده: " + "، ".join(status["exceeded"]))
                elif status["warning"]:
                    st.warning("نزدیک محدودیت محلی هستید.")
                limits = status["limits"]
                c1, c2, c3, c4 = st.columns(4)
                dr = c1.number_input("سقف درخواست روزانه", 0, 1_000_000, int(limits.get("daily_requests", 0)), key=f"lim_dr_{kind}")
                mr = c2.number_input("سقف درخواست ماهانه", 0, 10_000_000, int(limits.get("monthly_requests", 0)), key=f"lim_mr_{kind}")
                dt = c3.number_input("سقف توکن روزانه", 0, 1_000_000_000, int(limits.get("daily_tokens", 0)), step=1000, key=f"lim_dt_{kind}")
                mt = c4.number_input("سقف توکن ماهانه", 0, 10_000_000_000, int(limits.get("monthly_tokens", 0)), step=1000, key=f"lim_mt_{kind}")
                if st.button("💾 ذخیرهٔ محدودیت", key=f"save_lim_{kind}"):
                    usage.set_limit(provider, kind, daily_requests=int(dr), monthly_requests=int(mr),
                                    daily_tokens=int(dt), monthly_tokens=int(mt))
                    st.success("ذخیره شد.")
                saved = get_provider_config(provider)
                st.caption(f"کلید ذخیره‌شده: {mask_secret(saved['api_key']) or '—'} (هرگز کامل نمایش داده نمی‌شود)")
            else:
                st.caption("برای ویرایش محدودیت، یک ارائه‌دهنده انتخاب کنید.")

    st.divider()
    st.markdown("#### تفکیک بر اساس مدل (این ماه)")
    models = usage.by_model("month", provider=provider)
    st.dataframe([{"ارائه‌دهنده": row["provider"], "مدل": row["model"], "نوع": row["kind"],
                   "ورودی": row["input_tokens"], "خروجی": row["output_tokens"],
                   "درخواست": row["requests"], "هزینه ($)": row["cost_usd"]} for row in models],
                 use_container_width=True, hide_index=True) if models else st.caption("مصرفی ثبت نشده است.")

    st.markdown("#### تفکیک هزینه بر اساس پروژه (این ماه)")
    projects = usage.by_project("month")
    st.dataframe([{"پروژه": row["project"], "توکن": row["total_tokens"],
                   "درخواست": row["requests"], "هزینه ($)": row["cost_usd"]} for row in projects],
                 use_container_width=True, hide_index=True) if projects else st.caption("مصرفی ثبت نشده است.")

    with st.expander("🔁 تاریخچهٔ جابه‌جایی ارائه‌دهنده‌ها"):
        history = usage.failover_history(50)
        if not history:
            st.caption("تا حالا جابه‌جایی ثبت نشده است.")
        for item in history:
            st.caption(f"{time.strftime('%m-%d %H:%M', time.localtime(item['time']))} — "
                       f"{item['from_provider']}/{item['from_model']} → "
                       f"{item['to_provider'] or '—'}/{item['to_model'] or '—'} · {item['reason']}")
