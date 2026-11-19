"""پنل‌های Streamlit برای کتابخانه، تعمیر، تبدیل و آمار.

این پنل‌ها همان سرویس‌های ماژول‌های ``library``، ``repair``، ``convert`` و
``analytics`` را صدا می‌زنند؛ هیچ منطق ترجمه‌ای این‌جا تکرار نشده است.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import streamlit as st

from trarium import templates
from trarium.analytics import dashboard
from trarium.convert import FORMAT_LABELS, SUPPORTED_TARGETS, convert_file
from trarium.jobs import get_manager
from trarium.library import Library, STATUS_ICONS, STATUS_LABELS
from trarium.monitor import format_duration, job_snapshot
from trarium.provider_registry import LABELS, PROVIDERS, has_credentials
from trarium.repair import analyze_book, repair_pdf
from trarium.resume import ResumeLedger, STAGE_LABELS, stage_table

LANGUAGES = {"فارسی": "fa", "English": "en", "العربية": "ar", "Türkçe": "tr",
             "中文": "zh", "日本語": "ja", "한국어": "ko", "Русский": "ru"}
OUTPUT_FORMATS = ["txt", "epub", "html", "pdf"]


def _manager():
    return get_manager().start()


def _library() -> Library:
    return Library()


def _save_upload(uploaded, suffix: str | None = None) -> Path:
    suffix = suffix or Path(uploaded.name).suffix
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.write(uploaded.getvalue())
    handle.close()
    return Path(handle.name)


# ------------------------------------------------------------------- library
def render_library_tab(ctx: dict) -> None:
    library = _library()
    try:
        library.refresh_from_jobs()
    except Exception:
        pass
    stats = library.stats()
    st.markdown("### 📚 کتابخانه و پلی‌لیست")
    st.caption("کتاب‌ها و تنظیماتشان دائمی ذخیره می‌شوند: رفرش صفحه یا ری‌استارت چیزی را پاک نمی‌کند. "
               "افزودن کتاب هیچ درخواست API و هیچ ترجمه‌ای شروع نمی‌کند؛ ترجمه فقط با دکمهٔ خودتان آغاز می‌شود.")

    metrics = st.columns(4)
    metrics[0].metric("کتاب‌ها", stats["total"])
    metrics[1].metric("کامل‌شده", stats["completed"])
    metrics[2].metric("در جریان", stats["active"])
    metrics[3].metric("پلی‌لیست", stats["playlists"])

    with st.expander("➕ افزودن کتاب به کتابخانه", expanded=stats["total"] == 0):
        uploads = st.file_uploader("فایل کتاب (چند فایل هم‌زمان مجاز است)", type=None,
                                   accept_multiple_files=True, key="library_upload")
        add_cols = st.columns(3)
        with add_cols[0]:
            category = st.text_input("دسته‌بندی (اختیاری)", key="library_category",
                                     placeholder="مثلاً مانگا، رمان، دانشگاهی")
        playlists = library.list_playlists()
        playlist_names = {item["name"]: item["id"] for item in playlists}
        with add_cols[1]:
            target_playlist = st.selectbox("افزودن به پلی‌لیست", ["—"] + list(playlist_names),
                                           key="library_target_playlist")
        with add_cols[2]:
            st.caption("افزودن به کتابخانه هیچ پردازشی شروع نمی‌کند.")
        if st.button("افزودن به کتابخانه", type="primary", key="library_add_books") and uploads:
            added = 0
            for uploaded in uploads:
                try:
                    book = library.add_book(uploaded.getvalue(), name=uploaded.name,
                                            category=category.strip())
                    if target_playlist != "—" and playlist_names.get(target_playlist):
                        library.add_to_playlist(playlist_names[target_playlist], book["id"])
                    added += 1
                except Exception as exc:
                    st.error(f"{uploaded.name}: {exc}")
            if added:
                st.success(f"{added} کتاب به کتابخانه اضافه شد.")
                st.rerun()

    playlist_cols = st.columns([2, 2, 1])
    playlists = library.list_playlists()
    names = ["همه"] + [f"{item['name']} ({item['count']})" for item in playlists]
    mapping = {"همه": None, **{f"{item['name']} ({item['count']})": item["id"] for item in playlists}}
    with playlist_cols[0]:
        active_name = st.selectbox("پلی‌لیست", names, key="library_active_playlist")
    active_id = mapping.get(active_name)
    with playlist_cols[1]:
        new_name = st.text_input("پلی‌لیست جدید", key="library_new_playlist",
                                 placeholder="نام پلی‌لیست")
        if st.button("ساخت پلی‌لیست", key="library_create_playlist") and new_name.strip():
            try:
                library.create_playlist(new_name.strip())
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    with playlist_cols[2]:
        if active_id and playlists:
            if st.button("🗑️ حذف پلی‌لیست", key="library_delete_playlist"):
                library.delete_playlist(active_id)
                st.rerun()

    filters = st.columns([2, 2, 2])
    with filters[0]:
        query = st.text_input("🔎 جست‌وجو", key="library_search",
                              placeholder="نام، نویسنده یا دسته")
    with filters[1]:
        sort_label = st.selectbox("مرتب‌سازی", ["تازه‌ترین", "قدیمی‌ترین", "نام (الف‌با)",
                                               "نام (برعکس)", "وضعیت"], key="library_sort")
    with filters[2]:
        status_label = st.selectbox("وضعیت", ["همه", *STATUS_LABELS.values()],
                                   key="library_status_filter")
    sort_map = {"تازه‌ترین": "added_desc", "قدیمی‌ترین": "added_asc",
                "نام (الف‌با)": "title_asc", "نام (برعکس)": "title_desc", "وضعیت": "status"}
    status_value = next((key for key, label in STATUS_LABELS.items() if label == status_label), None)

    selected_id = st.session_state.get("library_book_id")
    if selected_id and library.get_book(selected_id):
        _render_book_page(ctx, library, selected_id)
        return

    books = library.list_books(search=query, sort=sort_map.get(sort_label, "added_desc"),
                               status=status_value, playlist=active_id)
    if not books:
        st.info("هنوز کتابی در کتابخانه نیست؛ از بخش «افزودن کتاب» شروع کنید.")
        return
    columns = st.columns(3)
    for index, book in enumerate(books):
        column = columns[index % 3]
        with column:
            with st.container(border=True):
                if book.get("cover_exists"):
                    st.image(book["cover"], use_container_width=True)
                st.markdown(f"**{book['title']}**")
                st.caption(f"{book.get('ext', '').lstrip('.').upper() or '—'} · "
                           f"{book['size_mb']} MB · {book.get('category') or 'بدون دسته'}")
                st.caption(f"{STATUS_ICONS.get(book['status'], '•')} {book['status_label']}")
                if book.get("job_id"):
                    snap = job_snapshot(book["job_id"])
                    if snap:
                        st.progress(min(1.0, float(snap["job"].get("progress") or 0)),
                                    text=snap["job"].get("stage") or "")
                if st.button("باز کردن", key=f"library_open_{book['id']}"):
                    st.session_state["library_book_id"] = book["id"]
                    library.touch(book["id"])
                    st.rerun()
                if active_id:
                    if st.button("حذف از پلی‌لیست", key=f"library_unlist_{book['id']}"):
                        library.remove_from_playlist(active_id, book["id"])
                        st.rerun()


def _render_book_page(ctx: dict, library: Library, book_id: str) -> None:
    book = library.get_book(book_id)
    if not book:
        st.session_state.pop("library_book_id", None)
        return
    if st.button("⬅️ بازگشت به کتابخانه", key="library_back"):
        st.session_state.pop("library_book_id", None)
        st.rerun()

    head = st.columns([2, 3])
    with head[0]:
        if book.get("cover_exists"):
            st.image(book["cover"], use_container_width=True)
        else:
            st.markdown("### 📕")
    with head[1]:
        st.markdown(f"### {book['title']}")
        st.caption(f"نویسنده: {book.get('author') or '—'} · فرمت: "
                   f"{book.get('ext', '').lstrip('.').upper() or '—'} · "
                   f"{book['size_mb']} MB · افزوده‌شده: "
                   f"{time.strftime('%Y-%m-%d', time.localtime(book['added_at']))}")
        st.caption(f"وضعیت: {STATUS_ICONS.get(book['status'], '•')} {book['status_label']}")
        if book.get("job_id"):
            snap = job_snapshot(book["job_id"])
            if snap:
                job = snap["job"]
                st.progress(min(1.0, float(job.get("progress") or 0)),
                            text=job.get("stage") or "")
                cols = st.columns(4)
                cols[0].metric("واحدها", f"{snap['completed']}/{snap['total']}")
                cols[1].metric("ناموفق", snap["failed"])
                cols[2].metric("توکن", f"{snap['input_tokens'] + snap['output_tokens']:,}")
                cols[3].metric("زمان", format_duration(snap["elapsed_seconds"]))
                actions = st.columns(4)
                manager = _manager()
                if job["status"] in {"running", "pausing", "queued"}:
                    if actions[0].button("⏸️ توقف", key="library_pause"):
                        manager.pause(book["job_id"])
                        library.set_status(book_id, "paused")
                        st.rerun()
                if job["status"] in {"paused", "failed"}:
                    if actions[1].button("▶️ ادامه", key="library_resume"):
                        manager.resume(book["job_id"])
                        library.set_status(book_id, "queued")
                        st.rerun()
                if job["status"] in {"queued", "running", "paused", "pausing"}:
                    if actions[2].button("🚫 لغو", key="library_cancel"):
                        manager.cancel(book["job_id"])
                        library.set_status(book_id, "ready")
                        st.rerun()
                if job.get("result_path"):
                    output = Path(job["result_path"])
                    if output.exists():
                        st.download_button("⬇️ دانلود خروجی", output.read_bytes(), output.name,
                                           "application/octet-stream", key="library_download")

    st.divider()
    st.markdown("#### ⚙️ تنظیمات این کتاب")
    saved = dict(book.get("settings") or {})
    template_options = ["—"] + [f"{item.name}" for item in templates.list_templates()]
    template_map = {f"{item.name}": item.key for item in templates.list_templates()}
    current_key = saved.get("template")
    current_name = "—"
    for item in templates.list_templates():
        if item.key == current_key:
            current_name = item.name
    with st.form(f"library_settings_{book_id}"):
        row1 = st.columns(3)
        with row1[0]:
            template_name = st.selectbox("قالب پروژه", template_options,
                                         index=template_options.index(current_name)
                                         if current_name in template_options else 0)
        with row1[1]:
            lang_label = st.selectbox("زبان مقصد", list(LANGUAGES),
                                      index=max(0, list(LANGUAGES.values()).index(
                                          saved.get("target_lang", "fa"))
                                          if saved.get("target_lang", "fa") in LANGUAGES.values() else 0))
        with row1[2]:
            fmt = st.selectbox("فرمت خروجی", OUTPUT_FORMATS,
                               index=OUTPUT_FORMATS.index(saved.get("output_format", "txt"))
                               if saved.get("output_format", "txt") in OUTPUT_FORMATS else 0)
        row2 = st.columns(2)
        with row2[0]:
            provider = st.selectbox("ارائه‌دهنده", PROVIDERS,
                                    index=PROVIDERS.index(saved.get("provider"))
                                    if saved.get("provider") in PROVIDERS else
                                    (PROVIDERS.index(ctx["provider"]) if ctx["provider"] in PROVIDERS else 0))
        with row2[1]:
            model = st.text_input("مدل", value=str(saved.get("model") or ctx.get("model") or ""))
        row3 = st.columns(3)
        with row3[0]:
            chunk_size = st.slider("اندازه دسته (کاراکتر)", 4000, 24000,
                                   int(saved.get("chunk_size") or ctx.get("chunk_size") or 14000), 1000)
        with row3[1]:
            workers = st.slider("درخواست هم‌زمان", 1, 10,
                                int(saved.get("workers") or ctx.get("workers") or 4))
        with row3[2]:
            enable_ocr = st.checkbox("OCR برای صفحات تصویری", value=bool(saved.get("enable_ocr")))
        prompt = st.text_area("پرامپت این کتاب (اختیاری)",
                              value=str(saved.get("short_prompt") or ""), height=80)
        saved_now = st.form_submit_button("💾 ذخیرهٔ تنظیمات", type="secondary")
        start_now = st.form_submit_button("🚀 شروع ترجمه", type="primary")

    settings = {
        "template": template_map.get(template_name),
        "target_lang": LANGUAGES[lang_label], "output_format": fmt,
        "provider": provider, "model": model, "chunk_size": int(chunk_size),
        "workers": int(workers), "enable_ocr": bool(enable_ocr),
        "short_prompt": prompt,
    }
    if saved_now:
        library.set_settings(book_id, settings)
        st.success("تنظیمات ذخیره شد و با ری‌استارت باقی می‌ماند.")
        st.rerun()
    if start_now:
        try:
            target_lang = settings["target_lang"]
            config = ctx["job_config"](fmt)
            config.update({
                "provider_chain": [{"provider": provider, "model": model}],
                "provider": provider, "model": model,
                "chunk_size": int(chunk_size), "workers": int(workers),
                "output_format": fmt, "enable_ocr": bool(enable_ocr),
                "short_prompt": prompt,
            })
            if settings.get("template"):
                config = templates.apply_template(settings["template"], config, only_missing=True)
            if not has_credentials(provider):
                st.error(f"برای {LABELS.get(provider, provider)} کلید ذخیره نشده است؛ "
                         "ابتدا در نوار کنار صفحه ذخیره کنید.")
            else:
                source = Path(book["path"])
                if not source.exists():
                    raise FileNotFoundError("فایل کتاب در کتابخانه پیدا نشد.")
                job_id = _manager().submit(
                    "pdf" if book["ext"] == ".pdf" else "book", book["title"],
                    config=config, source_bytes=source.read_bytes(),
                    source_name=book["filename"], target_lang=target_lang,
                    provider=provider, model=model, priority=5,
                )
                library.set_status(book_id, "queued", job_id=job_id)
                library.set_settings(book_id, settings)
                st.success(f"ترجمه در صف قرار گرفت: {job_id}")
                st.query_params["job"] = job_id
                st.rerun()
        except Exception as exc:
            st.error(f"شروع ترجمه انجام نشد: {exc}")

    with st.expander("🧩 قالب‌های آماده"):
        for item in templates.list_templates():
            st.markdown(f"**{item.name}** — {item.description}")
            st.caption(" · ".join(f"{key}={value}" for key, value in item.settings.items()
                                  if key in {"chunk_size", "workers", "ocr_lang", "output_format"}))
        st.divider()
        custom_cols = st.columns([2, 2, 1])
        custom_name = custom_cols[0].text_input("نام قالب اختصاصی", key="custom_template_name")
        custom_desc = custom_cols[1].text_input("توضیح", key="custom_template_desc")
        if custom_cols[2].button("ساخت قالب", key="custom_template_save") and custom_name.strip():
            try:
                templates.save_custom(custom_name, {**settings, "output_format": fmt},
                                      description=custom_desc)
                st.success("قالب اختصاصی ساخته شد.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    st.divider()
    confirm = st.checkbox("می‌خواهم این کتاب فقط از کتابخانه حذف شود (فایل اصلی می‌ماند).",
                          key="library_delete_confirm")
    if st.button("🗑️ حذف از کتابخانه", key="library_delete_book", disabled=not confirm):
        library.delete_book(book_id, remove_file=False)
        st.session_state.pop("library_book_id", None)
        st.success("کتاب از کتابخانه حذف شد؛ فایل اصلی دست‌نخورده است.")
        st.rerun()


# -------------------------------------------------------------------- repair
def render_repair_tab(ctx: dict) -> None:
    st.markdown("### 🩺 تعمیر خودکار کتاب")
    st.caption("صفحات تکراری، سفید، خراب، کم‌کیفیت و جابه‌جا پیدا می‌شوند. اول گزارش را می‌بینید، "
               "بعد خودتان تأیید می‌کنید. فایل اصلی هرگز تغییر نمی‌کند و نسخهٔ اصلاح‌شده جدا ساخته می‌شود.")

    mode = st.radio("چه کاری انجام شود؟", ["تعمیر و بررسی PDF", "تبدیل فرمت"],
                    horizontal=True, key="repair_mode")
    if mode == "تبدیل فرمت":
        _render_convert(ctx)
        return

    uploaded = st.file_uploader("فایل کتاب", type=None, key="repair_upload")
    if not uploaded:
        return
    path = _save_upload(uploaded)
    try:
        with st.spinner("در حال بررسی صفحه‌ها..."):
            report = analyze_book(path)
        cols = st.columns(4)
        cols[0].metric("صفحه/بخش", report.pages)
        cols[1].metric("تکراری", len(report.duplicates))
        cols[2].metric("سفید", len(report.blank_pages))
        cols[3].metric("امتیاز کیفیت", f"{report.quality_score}/100")
        st.caption(report.summary_fa())
        if report.issues:
            st.dataframe([{"صفحه": issue.page or "—", "نوع": issue.kind,
                           "شدت": {"critical": "بحرانی", "warning": "هشدار", "info": "اطلاع"}.get(
                               issue.severity, issue.severity),
                           "توضیح": issue.detail} for issue in report.issues],
                         use_container_width=True, hide_index=True)
        else:
            st.success("مشکلی پیدا نشد؛ فایل آمادهٔ ترجمه است.")
        for note in report.notes:
            st.caption(f"• {note}")

        if report.format != "pdf":
            st.info("تعمیر فیزیکی فقط برای PDF است؛ برای این فرمت گزارش کیفیت کافی است. "
                    "برای تبدیل فرمت از گزینهٔ «تبدیل فرمت» استفاده کنید.")
            return
        st.divider()
        st.markdown("#### ✅ تأیید اصلاح")
        options = st.columns(3)
        drop_duplicates = options[0].checkbox("حذف صفحات تکراری", value=True, key="repair_dup")
        drop_blank = options[1].checkbox("حذف صفحات سفید", value=False, key="repair_blank")
        fix_order = options[2].checkbox("اصلاح ترتیب صفحات", value=False, key="repair_order",
                                        help="فقط وقتی شمارهٔ چاپ‌شدهٔ صفحات پیدا شود.")
        st.caption("فایل اصلی حفظ می‌شود؛ خروجی یک فایل جدید است.")
        if st.button("🛠️ ساخت نسخهٔ اصلاح‌شده", type="primary", key="repair_build"):
            try:
                output = Path(tempfile.mkdtemp()) / f"{Path(uploaded.name).stem}_repaired.pdf"
                result = repair_pdf(path, output, report, drop_duplicates=drop_duplicates,
                                    drop_blank=drop_blank, fix_order=fix_order)
                st.success(f"نسخهٔ اصلاح‌شده آماده شد: {result.pages_before} → {result.pages_after} صفحه")
                for line in result.log:
                    st.caption(f"• {line}")
                st.download_button("⬇️ دانلود فایل اصلاح‌شده", output.read_bytes(),
                                   output.name, "application/pdf", type="primary",
                                   key="repair_download")
            except Exception as exc:
                st.error(f"اصلاح انجام نشد: {exc}")
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _render_convert(ctx: dict) -> None:
    st.markdown("#### 🔁 تبدیل فرمت")
    st.caption("PDF، EPUB، DOCX، HTML و TXT به هم تبدیل می‌شوند. عنوان‌ها، پاراگراف‌ها و جدول‌ها "
               "تا حد امکان حفظ می‌شوند و متن فارسی راست‌چین می‌ماند. ورودی دست‌نخورده می‌ماند.")
    uploaded = st.file_uploader("فایل ورودی", type=None, key="convert_upload")
    if not uploaded:
        return
    cols = st.columns(2)
    with cols[0]:
        target = st.selectbox("فرمت خروجی", list(SUPPORTED_TARGETS),
                              format_func=lambda key: FORMAT_LABELS[key], key="convert_target")
    with cols[1]:
        rtl = st.checkbox("راست‌چین (فارسی/عربی)", value=True, key="convert_rtl")
    if st.button("تبدیل کن", type="primary", key="convert_run"):
        path = _save_upload(uploaded)
        try:
            output = Path(tempfile.mkdtemp()) / f"{Path(uploaded.name).stem}.{target}"
            report = convert_file(path, output, target, rtl=rtl)
            values = st.columns(4)
            values[0].metric("بلوک", report.blocks)
            values[1].metric("عنوان", report.headings)
            values[2].metric("جدول", report.tables)
            values[3].metric("کاراکتر", f"{report.chars:,}")
            st.caption(f"ورودی: {FORMAT_LABELS.get(report.input_format.upper(), report.input_format)} · "
                       f"خروجی: {FORMAT_LABELS.get(report.output_format, report.output_format)}")
            for warning in report.warnings:
                st.warning(warning)
            mime = {"pdf": "application/pdf", "epub": "application/epub+zip",
                    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "html": "text/html", "txt": "text/plain"}[target]
            st.download_button("⬇️ دانلود فایل تبدیل‌شده", output.read_bytes(), output.name,
                               mime, type="primary", key="convert_download")
        except Exception as exc:
            st.error(f"تبدیل انجام نشد: {exc}")
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


# -------------------------------------------------------------------- resume
def render_resume_section() -> None:
    """دفتر مراحل Smart Resume: نشون می‌دهد هر پروژه تا کجا رفته و از کجا ادامه می‌دهد."""
    st.divider()
    st.markdown("### ⏯️ ادامهٔ هوشمند (Smart Resume)")
    st.caption("وضعیت هر مرحله (خواندن، تعمیر، OCR، ترجمه، خروجی) در پایگاه‌دادهٔ محلی ذخیره می‌شود؛ "
               "بعد از بستن صفحه، ری‌استارت یا کرش، از آخرین مرحلهٔ موفق ادامه می‌دهد و "
               "مرحله‌های تمام‌شده دوباره اجرا نمی‌شوند.")
    try:
        ledger = ResumeLedger()
        projects = ledger.list_projects(30)
    except Exception as exc:
        st.warning(f"دفتر مراحل در دسترس نیست: {exc}")
        return
    if not projects:
        st.caption("هنوز پروژه‌ای با مرحلهٔ ذخیره‌شده وجود ندارد.")
        return
    st.dataframe([{
        "پروژه": project.get("title") or project.get("source_path") or project["key"],
        "وضعیت": {"running": "▶️ ادامه‌پذیر", "completed": "✅ کامل",
                  "failed": "❌ ناموفق"}.get(str(project.get("status")), str(project.get("status"))),
        "پیشرفت": f"{int(round(float(project.get('progress') or 0) * 100))}٪",
        "مرحلهٔ بعدی": STAGE_LABELS.get(str(project.get("next_stage") or ""), "—"),
        "آخرین به‌روزرسانی": time.strftime("%Y-%m-%d %H:%M", time.localtime(project.get("updated_at") or 0)),
    } for project in projects], use_container_width=True, hide_index=True)
    labels = {f"{project.get('title') or project['key']}": project["key"] for project in projects}
    chosen = st.selectbox("جزئیات پروژه", list(labels), key="resume_project_pick")
    key = labels.get(chosen)
    if key:
        st.dataframe(stage_table(ledger, key), use_container_width=True, hide_index=True)
        st.caption(f"مرحلهٔ بعدی: {ledger.snapshot(key)['next_stage_label'] or 'همهٔ مرحله‌ها کامل'}")
        if st.button("🧹 پاک کردن وضعیت این پروژه", key="resume_clear"):
            ledger.clear(key)
            st.rerun()


# ----------------------------------------------------------------- analytics
def render_analytics_section() -> None:
    st.divider()
    st.markdown("### 📈 آمار پروژه‌ها")
    st.caption("همهٔ اعداد از تسک‌ها و مصرف واقعی همین دستگاه خوانده می‌شوند؛ هیچ مقداری ساختگی نیست.")
    try:
        data = dashboard(30)
    except Exception as exc:
        st.warning(f"آمار در دسترس نیست: {exc}")
        return
    first = st.columns(4)
    first[0].metric("کتاب‌های کامل‌شده", data["books_translated"])
    first[1].metric("صفحات پردازش‌شده", f"{data['pages_processed']:,}")
    first[2].metric("توکن مصرفی", f"{data['tokens_all']:,}")
    first[3].metric("هزینهٔ ماه (تخمینی)", f"${data['cost_month']:.4f}")
    second = st.columns(4)
    second[0].metric("سرعت متوسط", f"{data['avg_pages_per_minute']} صفحه/دقیقه")
    second[1].metric("خطاهای OCR", data["ocr_errors"])
    second[2].metric("در صف/جریان", data["books_total"] - data["books_translated"] - data["books_failed"])
    second[3].metric("صفحات OCR ماه", f"{data['ocr_pages_month']:,}")
    if data["daily"]:
        st.bar_chart({"روز": [row["day"] for row in data["daily"]],
                      "کتاب کامل‌شده": [row["completed"] for row in data["daily"]],
                      "صفحه": [row["pages"] for row in data["daily"]]},
                     x="روز", y=["کتاب کامل‌شده", "صفحه"])
    if data["providers"]:
        st.markdown("#### مصرف هر ارائه‌دهنده و مدل (این ماه)")
        st.dataframe([{"ارائه‌دهنده": row["provider"], "مدل": row["model"], "نوع": row["kind"],
                       "توکن": row["tokens"], "درخواست": row["requests"],
                       "هزینه ($)": row["cost_usd"]} for row in data["providers"]],
                     use_container_width=True, hide_index=True)
    else:
        st.caption("هنوز مصرفی برای ارائه‌دهنده‌ها ثبت نشده است.")
    st.markdown("#### تاریخچهٔ پروژه‌ها")
    if data["history"]:
        st.dataframe([{
            "پروژه": row["title"], "نوع": row["kind"],
            "وضعیت": {"completed": "✅ کامل", "failed": "❌ ناموفق", "running": "▶️ در حال اجرا",
                      "queued": "⏳ در صف", "paused": "⏸️ متوقف", "cancelled": "🚫 لغو"}.get(
                          row["status"], row["status"]),
            "واحد": f"{row['done']}/{row['units']}", "توکن": row["tokens"],
            "هزینه ($)": row["cost_usd"],
            "تاریخ": time.strftime("%Y-%m-%d", time.localtime(row["created_at"])) if row["created_at"] else "",
        } for row in data["history"]], use_container_width=True, hide_index=True)
    else:
        st.caption("هنوز پروژه‌ای ثبت نشده است.")
    if data["usage_by_project"]:
        st.markdown("#### مصرف هر پروژه (این ماه)")
        st.dataframe([{"پروژه": row["project"], "توکن": row["total_tokens"],
                       "درخواست": row["requests"], "هزینه ($)": row["cost_usd"]}
                      for row in data["usage_by_project"]],
                     use_container_width=True, hide_index=True)
