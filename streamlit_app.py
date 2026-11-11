"""
🌿 تراریوم - رابط وب با Streamlit
اجرا: streamlit run streamlit_app.py
"""
import os
import tempfile
import time
import threading
from pathlib import Path

import streamlit as st

from trarium.output import save_as_pdf, save_as_text
from trarium.processors.pipeline import TranslationResult
from trarium.translators import get_translator
from trarium.translators.openrouter import FREE_MODELS
from trarium.pdf_optimized import OPENROUTER_VISION_MODELS
from trarium.processors import TextChunker
from trarium.extractors import get_extractor
from trarium.utils import probably_already_in_persian

st.set_page_config(page_title="🌿 تراریوم", page_icon="🌿", layout="wide")

st.markdown("""
<style>
    .stButton > button { width: 100%; border-radius: 10px; font-size: 1.1rem; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

st.markdown("# 🌿 تراریوم (Trarium)")
st.markdown("### ترجمه خودکار کتاب با هوش مصنوعی — کاملاً رایگان")

# ============================================================
# سایدبار
# ============================================================
with st.sidebar:
    st.markdown("## ⚙️ تنظیمات")

    from trarium.translators.router9 import MODELS_9ROUTER

    provider = st.selectbox("🤖 ارائه‌دهنده",
        ["OpenRouter (رایگان)", "9Router (localhost)", "Google Gemini"])

    if "9Router" in provider:
        provider_key = "router9"
        api_key = "9router"
        st.info("💡 9Router روی localhost:20128 اجرا میشه")
        model_name = st.selectbox("🤖 مدل", list(MODELS_9ROUTER.keys()), index=0)
        model = MODELS_9ROUTER[model_name]
        custom_model = st.text_input("✏️ مدل سفارشی (اختیاری)",
            placeholder="مثلاً: gemini-2.5-flash یا combo-1")
        if custom_model.strip():
            model = custom_model.strip()
    elif "OpenRouter" in provider:
        provider_key = "openrouter"
        api_key = st.text_input("🔑 OpenRouter API Key", type="password",
                                value=os.environ.get("OPENROUTER_API_KEY", ""))
        model_name = st.selectbox("🤖 مدل", list(FREE_MODELS.keys()), index=0)
        model = FREE_MODELS[model_name]
        custom_model = st.text_input("✏️ مدل سفارشی (اختیاری)",
            placeholder="مثلاً: google/gemini-2.5-flash:free")
        if custom_model.strip():
            model = custom_model.strip()
    else:
        provider_key = "gemini"
        api_key = st.text_input("🔑 Google API Key", type="password",
                                value=os.environ.get("GOOGLE_API_KEY", ""))
        model = st.selectbox("🤖 مدل", ["gemini-2.5-flash", "gemini-2.5-pro"])
        custom_model = st.text_input("✏️ مدل سفارشی (اختیاری)",
            placeholder="مثلاً: gemini-2.5-flash-preview")
        if custom_model.strip():
            model = custom_model.strip()

    if not api_key:
        st.warning("API Key لازمه!")

    st.divider()
    languages = {
        "فارسی": "fa", "انگلیسی": "en", "عربی": "ar", "فرانسوی": "fr",
        "آلمانی": "de", "اسپانیایی": "es", "ترکی": "tr", "ژاپنی": "ja",
        "کره‌ای": "ko", "چینی": "zh", "روسی": "ru",
    }
    target_lang_name = st.selectbox("🌍 زبان مقصد", list(languages.keys()))
    target_lang = languages[target_lang_name]

    st.divider()
    with st.expander("🔧 تنظیمات پیشرفته"):
        chunk_size = st.slider("اندازه بخش", 2000, 30000, 15000, 500, key="chunk_size")
        delay = st.slider("تاخیر (ثانیه)", 0.0, 5.0, 0.0, 0.5, key="delay")
        max_workers = st.slider("ترجمه موازی (درخواست همزمان)", 1, 15, 5, 1, key="max_workers")

        from trarium.pdf_optimized import (
            _has_easyocr, _is_tesseract_installed,
            _has_paddleocr, _has_surya,
        )
        easyocr_ok = False  # B8: EasyOCR disabled
        tesseract_ok = _is_tesseract_installed()
        paddleocr_ok = _has_paddleocr()
        surya_ok = _has_surya()

        # ── وضعیت کتابخونه‌ها ──
        st.markdown("**📦 وضعیت کتابخونه‌ها:**")
        libs_status = [
            ("PaddleOCR", paddleocr_ok, "pip install paddleocr paddlepaddle"),
            ("Surya OCR", surya_ok, "pip install surya-ocr"),
            ("Tesseract", tesseract_ok, "pip install pytesseract"),
            ("OpenCV", True, ""),
            ("NumPy", True, ""),
        ]
        all_installed = all(ok for _, ok, _ in libs_status)
        if all_installed:
            st.success("✅ همه کتابخونه‌ها نصب هستن!")
        else:
            not_installed = [name for name, ok, _ in libs_status if not ok]
            st.warning(f"⚠️ نصب نشده: {', '.join(not_installed)}")
            for name, ok, cmd in libs_status:
                if ok:
                    st.markdown(f"  ✅ {name}")
                else:
                    st.markdown(f"  ❌ {name}")
                    if cmd:
                        st.code(cmd, language="bash")

        batch_size = st.slider("ترجمه دسته‌ای", 1, 20, 5, 1, key="batch_size")

        st.divider()
        st.markdown("**🖼️ مدل OCR Vision**")
        vision_model_name = st.selectbox("🤖 مدل Vision", list(OPENROUTER_VISION_MODELS.keys()), index=0)
        vision_model = OPENROUTER_VISION_MODELS[vision_model_name]

        st.divider()
        st.markdown("**🔑 API Key OCR Vision (اختیاری)**")
        st.caption("اگه API Key بذاری، OpenRouter Vision هم فعال میشه")
        vision_api_key = st.text_input(
            "🔑 OpenRouter API Key برای OCR", type="password",
            value=os.environ.get("OPENROUTER_API_KEY", ""),
            key="vision_api_key_settings",
        )

# ============================================================
# آپلود فایل
# ============================================================
st.markdown("## 📖 فایل کتاب رو آپلود کنید")
uploaded = st.file_uploader("PDF, EPUB یا TXT", type=["pdf", "epub", "txt"])

if uploaded:
    st.info(f"**فایل:** {uploaded.name} | **اندازه:** {uploaded.size / 1024:.1f} KB")
    import hashlib
    uploaded_bytes = uploaded.getvalue()
    file_hash = hashlib.sha256(uploaded_bytes).hexdigest()
    if st.session_state.get("file_hash") != file_hash:
        st.session_state["file_bytes"] = uploaded_bytes
        st.session_state["file_name"] = uploaded.name
        st.session_state["file_hash"] = file_hash
        # پاک کردن آنالیز قبلی وقتی فایل جدید آپلود میشه
        for k in [
            "analysis", "analysis_file", "analysis_file_hash", "mode_analyzed",
            "novel_text", "novel_images", "novel_url", "novel_title",
            "novel_site_type", "chapter_urls", "auto_urls", "pattern_found",
            "last_novel_input_url",
        ]:
            st.session_state.pop(k, None)
        uploaded.seek(0)

mode = st.radio("🎯 حالت ترجمه", [
    "📄 متن ساده (فقط متن ترجمه)",
    "🎨 حفظ قالب (ترجمه در جای اصلی)",
    "🌿 وب‌نول (ترجمه از سایت)"
], horizontal=True)
is_hybrid = "حفظ قالب" in mode
is_webnovel = "وب‌نول" in mode

# اگه حالت عوض شد، state مربوط به حالت قبلی رو پاک کن
if st.session_state.get("mode_name") != mode:
    for k in [
        "analysis", "analysis_file", "analysis_file_hash", "mode_analyzed",
        "novel_text", "novel_images", "novel_url", "novel_title",
        "novel_site_type", "chapter_urls", "auto_urls", "pattern_found",
        "last_novel_input_url", "ocr_method_choice",
    ]:
        st.session_state.pop(k, None)
    st.session_state["mode_name"] = mode

# مقدار پیش‌فرض برای ocr_lang
ocr_lang = "fas+eng"
if is_webnovel:
    out_fmt = st.radio("📤 فرمت خروجی", ["PDF", "TXT", "EPUB", "HTML"], horizontal=True)
elif not is_hybrid:
    out_fmt = st.radio("📤 فرمت خروجی", ["TXT", "PDF"], horizontal=True)
else:
    out_fmt = "PDF_HYBRID"

st.divider()

# ============================================================
# Helper: ساخت فایل موقت از session_state
# ============================================================
def _make_tmp_file(suffix=".pdf"):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(st.session_state["file_bytes"])
    tmp.close()
    return tmp.name

# ============================================================
# 🌿 وب‌نول — آدرس URL
# ============================================================
if is_webnovel:
    st.markdown("## 🌿 آدرس وب‌نول رو وارد کنید")
    novel_url = st.text_input(
        "🔗 URL فصل وب‌نول",
        placeholder="https://example.com/chapter-1/",
        help="آدرس صفحه فصل وب‌نول رو اینجا بزنید"
    )

    if novel_url:
        if st.session_state.get("last_novel_input_url") not in (None, novel_url):
            for k in ["novel_text", "novel_images", "novel_url", "novel_title",
                      "novel_site_type", "chapter_urls", "auto_urls", "pattern_found"]:
                st.session_state.pop(k, None)
        st.session_state["last_novel_input_url"] = novel_url

        col1, col2 = st.columns([1, 1])
        with col1:
            use_browser = st.checkbox("🌐 استفاده از مرورگر", value=False,
                help="برای سایت‌هایی که JS دارن")
        with col2:
            download_images = st.checkbox("🖼️ دانلود عکس‌ها", value=True)

        if st.button("🔍 تحلیل صفحه", type="primary", key="analyze_novel"):
            with st.spinner("در حال تحلیل..."):
                try:
                    from trarium.novel.scraper import NovelScraper

                    scraper = NovelScraper(use_browser=use_browser)
                    result = scraper.scrape_chapter(novel_url)

                    if result.text:
                        if download_images and result.images:
                            result.images = NovelScraper.download_all_images(result.images)
                        st.session_state["novel_text"] = result.text
                        st.session_state["novel_images"] = [
                            {"url": img.url, "alt": img.alt, "data": img.data, "filename": img.filename}
                            for img in result.images
                        ]
                        st.session_state["novel_url"] = novel_url
                        st.session_state["novel_title"] = result.title
                        st.session_state["novel_site_type"] = result.site_type

                        st.success(
                            f"✅ پیدا شد! "
                            f"{len(result.text):,} کاراکتر + "
                            f"{len(result.images)} عکس | "
                            f"نوع سایت: {result.site_type}"
                        )

                        if result.title:
                            st.info(f"📖 عنوان: {result.title}")

                        st.text_area("پیش‌نمایش متن", result.text[:1000] + "...", height=200)

                        # نمایش عکس‌ها
                        if result.images:
                            st.markdown(f"### 🖼️ {len(result.images)} عکس پیدا شد")
                            for i, img in enumerate(result.images[:5]):
                                st.image(img.url, caption=img.alt or f"عکس {i+1}", width=300)
                            if len(result.images) > 5:
                                st.caption(f"... و {len(result.images) - 5} عکس دیگر")

                            if download_images:
                                import io as _io
                                import zipfile as _zipfile
                                image_files = [img for img in result.images if img.data]
                                if image_files:
                                    _zip_buffer = _io.BytesIO()
                                    with _zipfile.ZipFile(_zip_buffer, "w", _zipfile.ZIP_DEFLATED) as _zf:
                                        for img in image_files:
                                            _zf.writestr(img.filename, img.data)
                                    st.download_button(
                                        "⬇️ دانلود عکس‌های این فصل (ZIP)",
                                        _zip_buffer.getvalue(),
                                        "chapter_images.zip",
                                        "application/zip",
                                        key="download_novel_images",
                                    )
                    else:
                        st.error("❌ محتوا پیدا نشد!")
                except Exception as e:
                    st.error(f"❌ خطا: {e}")

    # ── تشخیص الگوی لینک ──
    if "novel_url" in st.session_state and st.session_state["novel_url"]:
        from trarium.novel.scraper import ChapterPatternDetector
        detected = ChapterPatternDetector.detect_and_generate(st.session_state["novel_url"])
        if detected:
            st.info(f"🔗 الگوی لینک کشف شد! لینک‌های بعدی: {detected[0][:50]}...")
            st.session_state["auto_urls"] = detected
            st.session_state["pattern_found"] = True
        else:
            st.session_state["pattern_found"] = False
            st.session_state["auto_urls"] = []

    # ── ادامه خودکار یا ورود دستی ──
    if "pattern_found" in st.session_state:
        st.divider()
        if st.session_state["pattern_found"]:
            st.markdown("### 🔗 ادامه خودکار")
            auto_count = st.selectbox(
                "چند چپتر بعدی ترجمه بشه?",
                [2, 5, 10, 15, 20], key="auto_count"
            )
            st.session_state["chapter_urls"] = [st.session_state["novel_url"]] + \
                st.session_state["auto_urls"][:auto_count]
        else:
            st.markdown("### ✏️ ورود دستی لینک‌ها")
            st.caption("الگوی لینک کشف نشد — لینک‌ها رو دستی وارد کنید")
            manual_urls = st.text_area(
                "لینک هر چپتر در یک خط",
                placeholder="https://example.com/chapter/1\nhttps://example.com/chapter/2\nhttps://example.com/chapter/3",
                height=150, key="manual_urls"
            )
            if manual_urls:
                urls = [u.strip() for u in manual_urls.strip().split("\n") if u.strip()]
                st.session_state["chapter_urls"] = urls
                st.info(f"📝 {len(urls)} لینک وارد شد")
            else:
                st.session_state["chapter_urls"] = [st.session_state.get("novel_url", "")]

    # ── دکمه ترجمه وب‌نول ──
    if "novel_text" in st.session_state and st.session_state["novel_text"]:
        st.divider()
        chapter_urls = st.session_state.get("chapter_urls", [st.session_state.get("novel_url", "")])
        if len(chapter_urls) > 1:
            st.markdown(f"### 🚀 ترجمه {len(chapter_urls)} چپتر")
        else:
            st.markdown("### 🚀 آماده ترجمه!")

        with st.expander("⚙️ تنظیمات ترجمه", expanded=False):
            _lang_map = {"فارسی": "fa", "عربی": "ar", "ترکی": "tr", "چینی": "zh", "ژاپنی": "ja"}
            _lang_label = st.selectbox("🌐 زبان مقصد", list(_lang_map.keys()),
                                       key="novel_target_lang")
            target_lang = _lang_map[_lang_label]
            chunk_size_novel = st.slider("اندازه بخش", 2000, 30000, 15000, 500, key="novel_chunk")

        if st.button("🚀 شروع ترجمه!", type="primary", key="start_novel_translate") and api_key:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from trarium.translators import get_translator
            from trarium.processors import TextChunker
            from trarium.novel.scraper import NovelScraper

            translator = get_translator(provider_key, api_key=api_key, model_name=model)
            chapter_urls = st.session_state.get("chapter_urls", [st.session_state.get("novel_url", "")])
            all_chapters_text = []
            rate_lock = threading.Lock()
            next_request_at = [0.0]

            # ── ترجمه هر چپتر ──
            progress = st.progress(0, text="شروع...")
            status_text = st.empty()

            for ch_idx, ch_url in enumerate(chapter_urls):
                status_text.markdown(f"**📖 چپتر {ch_idx + 1}/{len(chapter_urls)}**")

                try:
                    # دریافت متن چپتر
                    cached_url = st.session_state.get("novel_url")
                    if ch_idx == 0 and ch_url == cached_url and "novel_text" in st.session_state:
                        text = st.session_state["novel_text"]
                    else:
                        scraper = NovelScraper(use_browser=use_browser)
                        result = scraper.scrape_chapter(ch_url)
                        text = result.text

                    if not text:
                        all_chapters_text.append(f"[چپتر {ch_idx + 1}: محتوا خالی]")
                        continue

                    # تقسیم و ترجمه
                    chunker = TextChunker(max_chars=chunk_size_novel, overlap=0)
                    raw_chunks = chunker.chunk_text(text)
                    chunks = [{"title": f"بخش {i+1}", "content": ch} for i, ch in enumerate(raw_chunks)]

                    translated_parts = [None] * len(chunks)
                    failed_count = 0

                    def _translate_novel_chunk(i, chunk):
                        try:
                            source_text = chunk["content"]
                            if target_lang == "fa" and probably_already_in_persian(source_text):
                                return i, source_text, None
                            if delay > 0:
                                with rate_lock:
                                    now = time.monotonic()
                                    wait = max(0.0, next_request_at[0] - now)
                                    next_request_at[0] = max(now, next_request_at[0]) + delay
                                if wait:
                                    time.sleep(wait)
                            return i, translator.translate(source_text, target_lang), None
                        except Exception as exc:
                            return i, "", str(exc)

                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = [executor.submit(_translate_novel_chunk, i, chunk) for i, chunk in enumerate(chunks)]
                        for done_idx, future in enumerate(as_completed(futures), 1):
                            i, tr, err = future.result()
                            translated_parts[i] = tr if not err else "[ترجمه نشد]"
                            if err:
                                failed_count += 1
                            pct = (ch_idx + done_idx / max(len(chunks), 1)) / max(len(chapter_urls), 1)
                            progress.progress(min(pct, 1.0), text=f"چپتر {ch_idx+1} - بخش {done_idx}/{len(chunks)}...")

                    chapter_text = "\n\n".join(p for p in translated_parts if p is not None)
                    all_chapters_text.append(chapter_text)

                except Exception as e:
                    all_chapters_text.append(f"[چپتر {ch_idx + 1}: خطا - {e}]")

            progress.progress(1.0, text="✅ تمام!")

            # ── نمایش نتایج ──
            st.divider()
            st.markdown("### 📊 آمار ترجمه")
            c1, c2 = st.columns(2)
            c1.metric("تعداد چپتر", len(chapter_urls))
            total_chars = sum(len(t) for t in all_chapters_text)
            c2.metric("کل کاراکتر ترجمه", f"{total_chars:,}")

            # ── جدول دانلود ──
            st.markdown("### ⬇️ دانلود فایل‌ها")

            # دانلود تکی
            for i, ch_text in enumerate(all_chapters_text):
                col1, col2, col3 = st.columns([1, 4, 2])
                col1.write(f"**{i+1}**")
                col2.write(f"Chapter {i+1}")
                col3.download_button(
                    "⬇️ دانلود", ch_text,
                    f"chapter_{i+1}.txt", "text/plain",
                    key=f"dl_ch_{i}"
                )

            # فایل ترکیبی
            if len(all_chapters_text) > 1:
                combined = "\n\n".join([
                    f"═══════════════════════════════════════════\n"
                    f"         Chapter {i+1}\n"
                    f"═══════════════════════════════════════════\n\n"
                    + ch_text
                    for i, ch_text in enumerate(all_chapters_text)
                ])

                st.divider()
                col1, col2, col3 = st.columns([1, 4, 2])
                col1.write("**All**")
                col2.write(f"Chapters 1–{len(all_chapters_text)} Combined")
                col3.download_button(
                    "⬇️ دانلود ترکیبی", combined,
                    f"chapters_1_to_{len(all_chapters_text)}_combined.txt", "text/plain",
                    key="dl_combined", type="primary"
                )

            st.success("🎉 ترجمه تموم شد!")

# ============================================================
# اگه فایلی آپلود شده
# ============================================================
if uploaded:
    suffix = Path(uploaded.name).suffix

    if is_hybrid and suffix.lower() != ".pdf":
        st.error("ترجمه ترکیبی فقط برای PDF!")
        st.stop()

    # ── آنالیز خودکار ──
    if "analysis" not in st.session_state or st.session_state.get("analysis_file_hash") != file_hash:
        tmp_path = _make_tmp_file(suffix)

        if is_hybrid:
            import pymupdf
            from trarium.pdf_optimized import extract_text_blocks

            doc = pymupdf.open(tmp_path)
            total = len(doc)
            text_pages = []
            scanned_pages = []

            progress = st.progress(0, text="تحلیل صفحات...")
            for i in range(total):
                if i % 10 == 0 or i == total - 1:
                    progress.progress((i + 1) / total, text=f"صفحه {i + 1}/{total}...")
                try:
                    blocks = extract_text_blocks(doc[i])
                except Exception:
                    blocks = []
                # صفحه متنی: حداقل یه بلاک متنی با حداقل ۵ کاراکتر
                has_text = any(len(b.get("text", "")) >= 5 for b in blocks)
                if has_text:
                    text_pages.append(i + 1)
                else:
                    scanned_pages.append(i + 1)
            progress.progress(1.0, text="✅ تمام شد!")
            doc.close()

            st.session_state["analysis"] = {
                "total": total,
                "text_pages": text_pages,
                "scanned_pages": scanned_pages,
                "ocr_results": {},
                "ocr_done": False,
            }
            st.session_state["analysis_file"] = uploaded.name
            st.session_state["analysis_file_hash"] = file_hash
            st.session_state["mode_analyzed"] = is_hybrid

            st.success(f"📊 **{len(text_pages)}** صفحه متنی | **{len(scanned_pages)}** صفحه اسکن‌شده")
        else:
            extractor = get_extractor(tmp_path, use_ocr=False)
            book = extractor.extract(tmp_path)
            st.session_state["analysis"] = {
                "mode": "simple",
                "book": book,
            }
            st.session_state["analysis_file"] = uploaded.name
            st.session_state["analysis_file_hash"] = file_hash
            st.session_state["mode_analyzed"] = is_hybrid
            st.success(f"📖 **عنوان:** {book.title} | **کاراکتر:** {book.total_chars:,}")

        # پاک کردن فایل موقت
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    # ── نمایش و اجرا ──
    analysis = st.session_state.get("analysis")
    if analysis:

        # ╔══════════════════════════════════════════════╗
        # ║  حالت متن ساده (EPUB/TXT/PDF ساده)          ║
        # ╚══════════════════════════════════════════════╝
        if not is_hybrid and "book" in analysis:
            book = analysis["book"]

            if book.chapters:
                st.markdown("### 🚫 فصل‌هایی که نیازی به ترجمه ندارن")
                st.caption("تیک بزنید تا اون فصل‌ها فقط حفظ بشن")
                skip_chapters = set()
                for i, ch in enumerate(book.chapters):
                    title = ch.get("title", f"بخش {i + 1}")[:50]
                    chars = len(ch.get("content", ""))
                    label = f"📖 {title} ({chars:,} کاراکتر)"
                    if st.checkbox(label, key=f"skip_ch_{i}", value=False):
                        skip_chapters.add(i)
                if skip_chapters:
                    st.session_state["analysis"]["skip_chapters"] = skip_chapters
                    st.warning(f"⚠️ {len(skip_chapters)} فصل رد میشه")

            if st.button("🚀 شروع ترجمه!", type="primary") and api_key:
                with tempfile.TemporaryDirectory() as tmpdir:
                    translator = get_translator(provider_key, api_key=api_key, model_name=model)
                    chunker = TextChunker(max_chars=chunk_size, overlap=0)
                    skip_ch = analysis.get("skip_chapters", set())
                    active_chapters = [ch for i, ch in enumerate(book.chapters) if i not in skip_ch]
                    if not active_chapters:
                        st.error("❌ همه فصل‌ها رد شدن!")
                        st.stop()
                    chunks = chunker.chunk_by_chapters(active_chapters)
                    total = len(chunks)
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    progress = st.progress(0, text="شروع...")
                    translated_parts = [None] * total
                    # ── آمار ترجمه ──
                    stats = {
                        "timings": [0.0] * total, "tokens_est": 0,
                        "original_chars": sum(len(ch.get("content", "")) for ch in active_chapters),
                    }
                    completed_count = [0]
                    rate_lock = threading.Lock()
                    next_request_at = [0.0]

                    def _translate_one(idx, ch):
                        t0 = time.time()
                        try:
                            source_text = ch["content"]
                            if target_lang == "fa" and probably_already_in_persian(source_text):
                                tr = source_text
                            else:
                                if delay > 0:
                                    with rate_lock:
                                        now = time.monotonic()
                                        wait = max(0.0, next_request_at[0] - now)
                                        next_request_at[0] = max(now, next_request_at[0]) + delay
                                    if wait:
                                        time.sleep(wait)
                                tr = translator.translate(source_text, target_lang)
                            elapsed = time.time() - t0
                            return idx, {"title": ch["title"], "content": tr}, elapsed
                        except Exception as e:
                            return idx, {"title": ch["title"], "content": "", "failed": True, "error": str(e)}, time.time() - t0

                    # از تنظیم کاربر استفاده کن، نه مقدار hard-code شده بر اساس provider.
                    max_workers_val = max_workers

                    with ThreadPoolExecutor(max_workers=max_workers_val) as executor:
                        futures = {executor.submit(_translate_one, i, ch): i for i, ch in enumerate(chunks)}
                        for future in as_completed(futures):
                            idx, part, elapsed = future.result()
                            translated_parts[idx] = part
                            stats["timings"][idx] = elapsed
                            stats["tokens_est"] += len(chunks[idx]["content"]) // 4 + len(part["content"]) // 2
                            completed_count[0] += 1
                            progress.progress(completed_count[0] / total, text=f"ترجمه {completed_count[0]}/{total}...")

                    progress.progress(1.0, text="✅ تمام!")
                    failed_parts = [p for p in translated_parts if p and p.get("failed")]
                    translated_text = "\n\n".join(
                        f"--- {p['title']} ---\n\n{p['content']}" for p in translated_parts if p
                    )
                    st.divider()
                    # ── نمایش آمار ──
                    st.markdown("### 📊 آمار ترجمه")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("بخش‌ها", total)
                    c2.metric("کاراکتر اصلی", f"{stats['original_chars']:,}")
                    c3.metric("کاراکتر ترجمه", f"{len(translated_text):,}")
                    c4.metric("توکن تقریبی", f"{stats['tokens_est']:,}")
                    if stats["timings"]:
                        valid = [t for t in stats["timings"] if t > 0]
                        if valid:
                            c5, c6, c7 = st.columns(3)
                            c5.metric("سریع‌ترین", f"{min(valid):.1f}s")
                            c6.metric("کندترین", f"{max(valid):.1f}s")
                            c7.metric("میانگین", f"{sum(valid)/len(valid):.1f}s")
                    # بررسی نکات نگارشی
                    literary_checks = [
                        ("بدون em-dash", "—" not in translated_text),
                        ("گیومه فارسی", "«" in translated_text or "»" in translated_text),
                        ("نیم‌فاصله", "‌" in translated_text),
                    ]
                    st.markdown("**📝 رعایت نکات نگارشی:**")
                    lc1, lc2, lc3 = st.columns(3)
                    for col, (name, ok) in zip([lc1, lc2, lc3], literary_checks):
                        col.metric(name, "✅" if ok else "⚠️")
                    safe = "".join(c for c in book.title if c.isalnum() or c in " _-").strip() or "translated"
                    if out_fmt == "PDF":
                        result_obj = TranslationResult(
                            title=book.title, author=book.author, original_lang="auto",
                            target_lang=target_lang, translated_text=translated_text,
                            total_chunks=total, translated_chunks=sum(1 for p in translated_parts if p and not p.get("failed")),
                            total_chars=book.total_chars, translated_chars=len(translated_text),
                        )
                        pdf_path = os.path.join(tmpdir, "output.pdf")
                        save_as_pdf(result_obj, pdf_path)
                        with open(pdf_path, "rb") as f:
                            pdf_bytes = f.read()
                        st.download_button("⬇️ دانلود PDF", pdf_bytes,
                                           f"{safe}_{target_lang}.pdf", "application/pdf", type="primary")
                    else:
                        st.download_button("⬇️ دانلود TXT", translated_text,
                                           f"{safe}_{target_lang}.txt", "text/plain", type="primary")
                    if failed_parts:
                        st.warning(f"⚠️ ترجمه تمام شد، اما {len(failed_parts)} بخش ناموفق بود.")
                    else:
                        st.success("🎉 ترجمه تموم شد!")

        # ╔══════════════════════════════════════════════╗
        # ║  حالت ترکیبی (PDF حفظ قالب)                 ║
        # ╚══════════════════════════════════════════════╝
        elif is_hybrid and "scanned_pages" in analysis:
            total = analysis["total"]
            text_pages = analysis["text_pages"]
            scanned_pages = analysis["scanned_pages"]
            ocr_results = analysis.get("ocr_results", {})

            # ── تنظیمات OCR (فقط در حالت ترکیبی) ──
            ocr_languages = {
                "فارسی+انگلیسی": "fas+eng",
                "عربی+انگلیسی": "ara+eng",
                "ژاپنی": "jpn",
                "چینی ساده": "chi_sim",
                "فقط انگلیسی": "eng",
            }
            ocr_lang_name = st.selectbox("🌐 زبان متن کتاب (OCR)", list(ocr_languages.keys()))
            ocr_lang = ocr_languages[ocr_lang_name]

            # ── خلاصه ──
            st.markdown("### 📊 خلاصه تحلیل")
            c1, c2, c3 = st.columns(3)
            c1.metric("📄 صفحات متنی", len(text_pages))
            c2.metric("🔍 صفحات اسکن‌شده", len(scanned_pages))
            c3.metric("✅ OCR شده", len([p for p in ocr_results if ocr_results[p]]))

            skip_pages_ui = set()
            if not scanned_pages:
                # همه صفحات متنی هستن — نیازی به OCR نیست
                st.success("🎉 همه صفحات متنی هستن — نیازی به OCR نیست!")
                analysis["ocr_done"] = True
            else:
                # ── صفحات اسکن‌شده — انتخاب صفحات رد شده ──
                st.markdown("### 🔍 صفحات اسکن‌شده")
                st.caption("تیک بزنید تا صفحاتی که نیازی به ترجمه ندارن رد بشن")

                skip_pages_ui = set()
                for p in scanned_pages:
                    is_done = (p - 1) in ocr_results and ocr_results.get(p - 1)
                    if is_done:
                        label = f"✅ صفحه {p} — OCR انجام شد"
                    else:
                        label = f"🔍 صفحه {p} — نیاز به OCR"
                    if st.checkbox(label, key=f"skip_p_{p}", value=False):
                        skip_pages_ui.add(p)

                pending_pages = [p for p in scanned_pages
                                 if p not in skip_pages_ui and not (ocr_results.get(p - 1))]

                if not pending_pages:
                    st.success("🎉 همه صفحات آماده ترجمه!")
                    analysis["ocr_done"] = True
                else:
                    st.markdown(f"### 🔍 OCR — {len(pending_pages)} صفحه باقی‌مانده")

                    from trarium.pdf_optimized import (
                        _has_easyocr, _is_tesseract_installed,
                        _has_paddleocr, _has_surya,
                        extract_ocr_blocks, extract_ocr_blocks_batch_openrouter,
                    )

                    easyocr_ok = False  # B8: EasyOCR disabled
                    tesseract_ok = _is_tesseract_installed()
                    paddleocr_ok = _has_paddleocr()
                    surya_ok = _has_surya()

                    if not surya_ok:
                        st.caption("ℹ️ Surya اختیاری است و فقط وقتی وابستگی‌هایش کامل نصب باشند در لیست ظاهر می‌شود.")

                    # ── لیست تمام روش‌های OCR موجود ──
                    available_methods = []
                    available_keys = []
                    if paddleocr_ok:
                        available_methods.append("PaddleOCR (دقیق‌ترین)")
                        available_keys.append("paddleocr")
                    if surya_ok:
                        available_methods.append("Surya OCR (اختیاری)")
                        available_keys.append("surya")
                    if tesseract_ok:
                        available_methods.append("Tesseract (سریع‌ترین)")
                        available_keys.append("tesseract")
                    # OpenRouter Vision — همیشه موجود
                    available_methods.append("OpenRouter Vision (نیاز به API Key)")
                    available_keys.append("openrouter")

                    # ── انتخاب روش OCR ──
                    ocr_choice = st.selectbox(
                        "🔍 روش OCR",
                        ["خودکار (پیشنهادی)"] + available_methods,
                        index=0,
                        key="ocr_method_choice"
                    )

                    if ocr_choice == "خودکار (پیشنهادی)":
                        # اولویت: PaddleOCR > Surya > Tesseract > OpenRouter
                        if paddleocr_ok:
                            ocr_method = "paddleocr"
                        elif surya_ok:
                            ocr_method = "surya"
                        elif tesseract_ok:
                            ocr_method = "tesseract"
                        else:
                            ocr_method = "openrouter"
                    else:
                        idx = available_methods.index(ocr_choice)
                        ocr_method = available_keys[idx]

                    # ── API Key OCR Vision (اختیاری) ──
                    vision_key = vision_api_key if 'vision_api_key' in dir() else os.environ.get("OPENROUTER_API_KEY", "")
                    if ocr_method == "openrouter":
                        if not vision_key:
                            st.warning("⚠️ OpenRouter Vision نیاز به API Key داره!")
                            st.info("💡 می‌تونید API Key رو توی تنظیمات پیشرفته سایدبار بذارید (اختیاری)")
                        else:
                            st.info(f"🔍 روش: **OpenRouter Vision** — مدل: {vision_model_name}")

                    # ── دکمه OCR ──
                    if ocr_method == "openrouter":
                        if vision_key:
                            if st.button("▶️ اجرای OpenRouter Vision", type="primary", key="run_vision"):
                                tmp_path = _make_tmp_file(suffix)
                                with st.spinner(f"OCR {len(pending_pages)} صفحه با Vision..."):
                                    import pymupdf
                                    doc = pymupdf.open(tmp_path)
                                    pages_for_vision = [(p - 1, doc[p - 1]) for p in pending_pages]
                                    ocr_batch = extract_ocr_blocks_batch_openrouter(
                                        pages_for_vision, api_key=vision_key,
                                        model_name=vision_model, batch_size=3,
                                    )
                                    for p in pending_pages:
                                        ocr_results[p - 1] = ocr_batch.get(p - 1, [])
                                    doc.close()
                                analysis["ocr_results"] = ocr_results
                                try:
                                    os.unlink(tmp_path)
                                except Exception:
                                    pass
                                st.rerun()

                    elif ocr_method == "paddleocr":
                        st.info("🔍 روش: **PaddleOCR** — محلی، دقیق‌ترین، صفر توکن")
                        from trarium.pdf_optimized import extract_ocr_blocks_paddleocr
                        if st.button("▶️ اجرای PaddleOCR", type="primary", key="run_paddleocr"):
                            tmp_path = _make_tmp_file(suffix)
                            with st.spinner(f"OCR {len(pending_pages)} صفحه با PaddleOCR..."):
                                import pymupdf
                                doc = pymupdf.open(tmp_path)
                                for p in pending_pages:
                                    try:
                                        blocks = extract_ocr_blocks_paddleocr(doc[p - 1], lang=ocr_lang)
                                        ocr_results[p - 1] = blocks
                                    except Exception as e:
                                        st.warning(f"خطا صفحه {p}: {e}")
                                        ocr_results[p - 1] = []
                                doc.close()
                            analysis["ocr_results"] = ocr_results
                            try:
                                os.unlink(tmp_path)
                            except Exception:
                                pass
                            st.rerun()

                    elif ocr_method == "surya":
                        st.info("🔍 روش: **Surya OCR** — محلی، جدیدترین مدل، صفر توکن")
                        from trarium.pdf_optimized import extract_ocr_blocks_surya
                        if st.button("▶️ اجرای Surya OCR", type="primary", key="run_surya"):
                            tmp_path = _make_tmp_file(suffix)
                            with st.spinner(f"OCR {len(pending_pages)} صفحه با Surya OCR..."):
                                import pymupdf
                                doc = pymupdf.open(tmp_path)
                                for p in pending_pages:
                                    try:
                                        blocks = extract_ocr_blocks_surya(doc[p - 1], lang=ocr_lang)
                                        ocr_results[p - 1] = blocks
                                    except Exception as e:
                                        st.warning(f"خطا صفحه {p}: {e}")
                                        ocr_results[p - 1] = []
                                doc.close()
                            analysis["ocr_results"] = ocr_results
                            try:
                                os.unlink(tmp_path)
                            except Exception:
                                pass
                            st.rerun()

                    elif ocr_method == "tesseract":
                        st.info("🔍 روش: **Tesseract** — محلی، سریع، صفر توکن")
                        from trarium.pdf_optimized import extract_ocr_blocks_tesseract
                        if st.button("▶️ اجرای Tesseract", type="primary", key="run_tesseract"):
                            tmp_path = _make_tmp_file(suffix)
                            with st.spinner(f"OCR {len(pending_pages)} صفحه با Tesseract..."):
                                import pymupdf
                                doc = pymupdf.open(tmp_path)
                                for p in pending_pages:
                                    try:
                                        blocks = extract_ocr_blocks_tesseract(doc[p - 1], lang=ocr_lang)
                                        ocr_results[p - 1] = blocks
                                    except Exception as e:
                                        st.warning(f"خطا صفحه {p}: {e}")
                                        ocr_results[p - 1] = []
                                doc.close()
                            analysis["ocr_results"] = ocr_results
                            try:
                                os.unlink(tmp_path)
                            except Exception:
                                pass
                            st.rerun()

                    # ── دکمه کافیه ──
                    if ocr_results:
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("✅ کافیه — شروع ترجمه", type="primary", key="done_ocr"):
                                analysis["ocr_done"] = True
                                st.rerun()
                        with col2:
                            method_order = ["paddleocr", "surya", "tesseract", "openrouter"]
                            method_names = {
                                "paddleocr": "PaddleOCR",
                                "surya": "Surya OCR",
                                "tesseract": "Tesseract",
                                "openrouter": "OpenRouter Vision",
                            }
                            idx = method_order.index(ocr_method) if ocr_method in method_order else -1
                            if idx >= 0 and idx < len(method_order) - 1:
                                next_key = method_order[idx + 1]
                                next_name = method_names.get(next_key, next_key)
                                if st.button(f"➡️ روش بعدی ({next_name})", key="next_method"):
                                    for p in pending_pages:
                                        ocr_results[p - 1] = []
                                    analysis["ocr_results"] = ocr_results
                                    st.rerun()

            # ── دکمه شروع ترجمه ──
            if analysis.get("ocr_done"):
                st.divider()
                st.markdown("### 🚀 آماده ترجمه!")
                if st.button("🚀 شروع ترجمه!", type="primary", key="start_translate") and api_key:
                    tmp_path = _make_tmp_file(suffix)
                    with tempfile.TemporaryDirectory() as tmpdir:
                        translator = get_translator(provider_key, api_key=api_key, model_name=model)
                        out_path = os.path.join(tmpdir, "output.pdf")

                        with st.status("🔄 ترجمه...", expanded=True) as status:
                            from trarium.pdf_optimized import translate_pdf_optimized

                            hybrid_progress = st.progress(0, text="شروع...")

                            def hybrid_callback(pct, msg):
                                hybrid_progress.progress(pct, text=msg)

                            user_skip = {p - 1 for p in skip_pages_ui}

                            result_path = translate_pdf_optimized(
                                tmp_path, translator, target_lang, out_path, batch_size,
                                ocr_lang=ocr_lang,
                                api_key=vision_key if 'vision_key' in dir() else None,
                                vision_model=vision_model,
                                skip_pages=user_skip,
                                precomputed_ocr={k: v for k, v in ocr_results.items()},
                                progress_callback=hybrid_callback,
                            )

                            hybrid_progress.progress(1.0, text="✅ تمام!")
                            if not result_path or not os.path.isfile(result_path):
                                raise RuntimeError("ساخت PDF خروجی شکست خورد")
                            with open(result_path, "rb") as f:
                                pdf_bytes = f.read()
                            status.update(label="✅ تمام شد!", state="complete")

                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass

                        safe = "".join(
                            c for c in Path(uploaded.name).stem if c.isalnum() or c in " _-"
                        ).strip() or "translated"
                        st.download_button(
                            "⬇️ دانلود PDF ترجمه‌شده", pdf_bytes,
                            f"{safe}_{target_lang}.pdf", "application/pdf", type="primary",
                        )
                        # ── آمار ترجمه ترکیبی ──
                        st.markdown("### 📊 آمار ترجمه")
                        total_text_pages = len(text_pages)
                        total_scanned = len(scanned_pages)
                        ocr_done = len([p for p in ocr_results if ocr_results.get(p)])
                        c1, c2, c3 = st.columns(3)
                        c1.metric("صفحات متنی", total_text_pages)
                        c2.metric("صفحات OCR شده", ocr_done)
                        c3.metric("کل صفحات", total_text_pages + total_scanned)
                        st.success("🎉 ترجمه تموم شد!")

st.divider()
st.caption("🌿 تراریوم v2.16.0 | ترجمه خودکار کتاب")
