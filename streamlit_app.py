"""🌿 Trarium 2.19 — clean Streamlit UI for book and web-novel translation."""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from trarium import __version__
from trarium.output import save_as_epub, save_as_html, save_as_pdf, save_as_text
from trarium.processors.pipeline import TranslationPipeline
from trarium.translators import get_translator
from trarium.translators.openrouter import FREE_MODELS
from trarium.translators.gemini import GEMINI_MODELS
from trarium.translators.groq import GROQ_MODELS
from trarium.translators.router9 import MODELS_9ROUTER
from trarium.pdf_optimized import OPENROUTER_VISION_MODELS
from trarium.cache import CachedTranslator, TranslationCache
from trarium.short_prompt import SHORT_PROMPT_CHIPS, build_short_prompt
from trarium.scheduler import FallbackTranslator
from trarium.extractors.formats import DISPLAY_FORMAT_GROUPS, format_stats, search_formats
from trarium.extractors.generic import UnsupportedFormatError
from trarium.jobs import get_manager
from trarium.local_state import chain_to_text, get_provider_config, load_settings, mask_secret, save_settings, split_chain_text
from trarium.provider_registry import chain_from_settings, label as provider_label, remember
from trarium.usage import UsageTrackingTranslator
from trarium import ui_panels

st.set_page_config(page_title="Trarium", page_icon="🌿", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
:root { --trarium-red:#e50914; --trarium-black:#080808; }
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] { background:#080808; }
.block-container { max-width:1180px; padding-top:1.4rem; padding-bottom:4rem; }
[data-testid="stSidebar"] { background:#0d0d0d; border-right:1px solid #3a0b0d; }
[data-testid="stSidebar"] * { color:#f2f2f2; }
.hero { padding:30px 32px; border-radius:22px; background:linear-gradient(145deg,#0b0b0b,#1a0506); border:1px solid #5a1116; margin-bottom:20px; box-shadow:0 16px 42px rgba(0,0,0,.35); }
.hero h1 { margin:0 0 8px; font-size:2.45rem; color:#fff !important; text-shadow:0 2px 18px rgba(229,9,20,.25); }
.hero p { margin:0; color:#d7d7d7; font-size:1.05rem; }
.card { padding:18px 20px; border:1px solid #2b1516; border-radius:16px; background:#121212; }
.small-muted { color:#a6a6a6; font-size:.92rem; }
div.stButton > button { border-radius:12px; font-weight:700; min-height:2.7rem; }
button[kind="primary"] { background:linear-gradient(135deg,#e50914,#b30710) !important; border-color:#e50914 !important; color:#fff !important; }
button[kind="secondary"] { border-color:#4a1c1f !important; background:#171717 !important; color:#f0f0f0 !important; }
[data-baseweb="tab"] { color:#bcbcbc; }
[aria-selected="true"][data-baseweb="tab"] { color:#fff !important; border-bottom-color:#e50914 !important; }
.status-pill { display:inline-block; padding:5px 10px; border-radius:999px; background:#2a080b; color:#ff7077; font-size:.85rem; font-weight:700; border:1px solid #621118; }
div[data-testid="stMetricValue"] { color:#fff; }
</style>
""", unsafe_allow_html=True)

LANGUAGES = {"فارسی": "fa", "English": "en", "العربية": "ar", "Türkçe": "tr", "中文": "zh", "日本語": "ja", "한국어": "ko", "Русский": "ru"}


def _safe_name(name: str) -> str:
    return "".join(c for c in (name or "") if c.isalnum() or c in " _-").strip() or "translated"


def _tmp_from_session(suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    Path(path).write_bytes(st.session_state["file_bytes"])
    return path


def _store_uploaded(uploaded):
    data = uploaded.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    if st.session_state.get("file_hash") != digest:
        for key in [
            "file_hash", "file_bytes", "file_name", "text_result",
            "layout_hash", "layout_analysis", "layout_ocr", "layout_result",
            "novel_results", "novel_translation",
        ]:
            st.session_state.pop(key, None)
        st.session_state["file_hash"] = digest
        st.session_state["file_bytes"] = data
        st.session_state["file_name"] = uploaded.name
    return digest



def _clear_short_prompt():
    # Runs as a widget callback, before the text area is created again.
    st.session_state["short_prompt_selected"] = []
    st.session_state["short_prompt_extra"] = ""


def _show_direct_provider_test(provider: str, api_key: str, model: str, base_url: str = ""):
    if not st.button("🧪 تست درخواست", key=f"test_{provider}"):
        return
    if not api_key and provider != "router9":
        st.error("API Key وارد نشده است.")
        return
    try:
        if provider == "gemini":
            t = get_translator("gemini", api_key=api_key, model_name=model)
        elif provider == "groq":
            t = get_translator("groq", api_key=api_key, model_name=model)
        elif provider == "openai_compatible":
            t = get_translator("openai_compatible", api_key=api_key, model_name=model, base_url=base_url, provider_name="OpenAI-compatible")
        else:
            raise RuntimeError("تست مستقیم برای این ارائه‌دهنده در دسترس نیست.")
        answer=t.translate("Reply only with: OK", "en")
        st.success(f"درخواست موفق بود: {answer[:80]}")
    except Exception as exc:
        st.error(f"درخواست ناموفق بود: {exc}")


def _translator_settings():
    def provider_config(label: str, suffix: str = ""):
        custom_base = ""
        if label == "OpenRouter":
            provider = "openrouter"
            api_key = st.text_input(f"OpenRouter API Key{suffix}", value=os.environ.get("OPENROUTER_API_KEY", ""), type="password", key=f"or_key{suffix}")
            model_label = st.selectbox(f"مدل OpenRouter{suffix}", list(FREE_MODELS.keys()), key=f"or_model{suffix}")
            model = FREE_MODELS[model_label]
        elif label == "Google Gemini":
            provider = "gemini"
            api_key = st.text_input(f"Google API Key{suffix}", value=os.environ.get("GOOGLE_API_KEY", ""), type="password", key=f"ge_key{suffix}")
            model_label = st.selectbox(f"مدل Gemini{suffix}", list(GEMINI_MODELS.keys()), key=f"ge_model{suffix}")
            model = GEMINI_MODELS[model_label]
        elif label == "Groq":
            provider = "groq"
            api_key = st.text_input(f"Groq API Key{suffix}", value=os.environ.get("GROQ_API_KEY", ""), type="password", key=f"gr_key{suffix}")
            model_label = st.selectbox(f"مدل Groq{suffix}", list(GROQ_MODELS.keys()), key=f"gr_model{suffix}")
            model = GROQ_MODELS[model_label]
        elif label == "9Router":
            provider = "router9"; api_key = "9router"
            model_label = st.selectbox(f"مدل 9Router{suffix}", list(MODELS_9ROUTER.keys()), key=f"r9_model{suffix}")
            model = MODELS_9ROUTER[model_label]
        else:
            provider = "openai_compatible"
            api_key = st.text_input(f"API Key{suffix}", value=os.environ.get("TRARIUM_API_KEY", ""), type="password", key=f"oa_key{suffix}")
            custom_base = st.text_input(f"Base URL{suffix}", value=os.environ.get("TRARIUM_BASE_URL", ""), placeholder="https://example.com/v1", key=f"oa_base{suffix}")
            model = st.text_input(f"Model ID{suffix}", value=os.environ.get("TRARIUM_MODEL", ""), placeholder="model-id", key=f"oa_model{suffix}")
        return provider, api_key, model, custom_base

    with st.sidebar:
        st.markdown("## ⚙️ تنظیمات ترجمه")
        labels = ["OpenRouter", "Google Gemini", "Groq", "9Router", "OpenAI-compatible"]
        primary_label = st.selectbox("ارائه‌دهنده اصلی", labels, key="primary_provider")
        provider, api_key, model, custom_base = provider_config(primary_label)

        fallback_label = st.selectbox("ارائه‌دهنده پشتیبان", ["خاموش"] + labels, key="fallback_provider")
        fallback_cfg = None
        if fallback_label != "خاموش":
            fallback_cfg = provider_config(fallback_label, " (پشتیبان)")
            st.caption("در صورت خطا، Trarium ارائه‌دهنده پشتیبان را امتحان می‌کند؛ تقسیم کورکورانه صفحات انجام نمی‌شود.")

        st.divider(); st.markdown("### 🔁 جابه‌جایی هوشمند")
        failover_enabled = st.checkbox("Failover (انتقال خودکار در خطا، Rate Limit یا اتمام سهمیه)", value=False, key="failover_enabled")
        default_chain = chain_to_text(chain_from_settings())
        if not default_chain:
            default_chain = f"{provider}:{model}" + (f"\n{fallback_cfg[0]}:{fallback_cfg[2]}" if fallback_cfg else "")
        failover_chain_text = st.text_area(
            "زنجیرهٔ ارائه‌دهنده‌ها (هر خط: provider:model)", value=default_chain, height=92,
            key="failover_chain_text", placeholder="openrouter:model/a\ngemini:gemini-3.8-flash",
            help="ترتیب خط‌ها همان اولویت است. مدل را خالی بگذارید تا مدل پیش‌فرض همان ارائه‌دهنده استفاده شود.",
        )
        fc1, fc2 = st.columns(2)
        with fc1:
            failover_retries = st.number_input("تلاش مجدد", 0, 6, 2, key="failover_retries")
        with fc2:
            failover_cost_cap = st.number_input("سقف هزینه ماهانه ($، ۰=بی‌نهایت)", 0.0, 1000.0, 0.0, 0.5, key="failover_cost_cap")
        failover_smart = st.checkbox("Smart Routing (انتخاب بر اساس مصرف و کیفیت)", value=False, key="failover_smart")
        if st.button("💾 ذخیرهٔ کلیدها و زنجیره برای اجرای پس‌زمینه", key="save_provider_chain"):
            remember(provider, api_key=api_key, model=model, base_url=custom_base)
            if fallback_cfg:
                remember(fallback_cfg[0], api_key=fallback_cfg[1], model=fallback_cfg[2], base_url=fallback_cfg[3])
            settings = load_settings()
            settings["failover_chain"] = split_chain_text(failover_chain_text)
            settings["failover_enabled"] = bool(failover_enabled)
            save_settings(settings)
            st.success("ذخیره شد. کلید فقط روی همین دستگاه (دسترسی ۶۰۰) نگه داشته می‌شود.")
        saved_key = get_provider_config(provider)["api_key"]
        st.caption(f"کلید ذخیره‌شدهٔ {provider_label(provider)}: {mask_secret(saved_key) or '—'} · برای تسک‌های پس‌زمینه لازم است.")

        target_label = st.selectbox("زبان مقصد", list(LANGUAGES.keys()), index=0)
        target_lang = LANGUAGES[target_label]
        st.divider(); st.markdown("### 🧠 صرفه‌جویی و پایداری")
        safe_opt = st.checkbox("Safe Optimization (بدون دستکاری محتوای داستان)", value=True)
        protect_spans = st.checkbox("حفاظت URL/ایمیل/تگ/کد با Placeholder", value=True)
        use_memory = st.checkbox("Translation Memory + Cache", value=True)
        use_checkpoint = st.checkbox("Checkpoint برای ادامه بعد از توقف/خاموشی", value=True)
        adaptive_batch = st.checkbox("Adaptive Batch + Token-aware splitting", value=True)
        do_probe = st.checkbox("Pre-flight Probe + برآورد Token", value=False)
        smart_routing = st.checkbox("Smart model routing (دیالوگ ساده → سریع، متن پیچیده → اصلی)", value=False, disabled=False)

        st.divider(); st.markdown("### 📚 فرهنگ واژه و شخصیت")
        registry_text = st.text_area("هر خط: English = فارسی", value="", height=110, placeholder="Subaru = سوبارو\nEmilia = امیلیا")
        from trarium.term_registry import TermRegistry
        registry = TermRegistry()
        if registry_text.strip():
            for line in registry_text.splitlines():
                if "=" in line:
                    src, dst = line.split("=", 1)
                    registry.items[src.strip()] = dst.strip()
            registry.save()
        st.caption(f"{len(registry.items)} اصطلاح ذخیره شده است.")

        st.divider(); st.markdown("### ✍️ پرامپت کوتاه")
        st.caption("با یک کلیک انتخاب می‌شود و همان‌جا در پرامپت می‌نشیند؛ برای ساختنش هیچ درخواستی به هوش مصنوعی نمی‌رود و توکنی مصرف نمی‌شود.")
        selected_chips = st.session_state.setdefault("short_prompt_selected", [])
        chip_labels = list(SHORT_PROMPT_CHIPS)
        for row_start in range(0, len(chip_labels), 2):
            chip_cols = st.columns(2)
            for offset, chip_col in enumerate(chip_cols):
                idx = row_start + offset
                if idx >= len(chip_labels):
                    break
                label = chip_labels[idx]
                active = label in selected_chips
                if chip_col.button(("✅ " if active else "") + label, key=f"short_chip_{idx}",
                                   type="primary" if active else "secondary", use_container_width=True):
                    if active:
                        selected_chips.remove(label)
                    else:
                        selected_chips.append(label)
                    st.session_state["short_prompt_selected"] = selected_chips
                    st.rerun()
        chip_extra = st.text_area("متن دلخواه (اختیاری)", key="short_prompt_extra", height=80,
                                  placeholder="مثلاً: نام‌های ژاپنی را همان‌طور که هستند بنویس")
        short_prompt = build_short_prompt(selected_chips, chip_extra)
        if short_prompt:
            st.caption(f"فعال: {short_prompt}")
            st.button("🧹 پاک کردن پرامپت کوتاه", key="short_prompt_clear", on_click=_clear_short_prompt)
        else:
            st.caption("هیچ دستوری فعال نیست؛ ترجمه با تنظیمات پیش‌فرض انجام می‌شود.")

        st.divider(); st.markdown("### ⚡ سرعت")
        chunk_size = st.slider("اندازه Batch (کاراکتر)", 4000, 24000, 14000, 1000)
        workers = st.slider("درخواست همزمان", 1, 10, 4)
        delay = st.slider("تاخیر بین درخواست‌ها", 0.0, 3.0, 0.0, 0.1)
        show_details = st.checkbox("Show Details — نمایش زندهٔ مصرف و پیشرفت", value=False, key="show_details")
        if st.button("🧹 پاک کردن Translation Memory", key="clear_translation_cache"):
            TranslationCache().clear(); st.success("حافظه ترجمه پاک شد.")
        if st.button("🧹 پاک کردن Checkpointها", key="clear_checkpoints"):
            from trarium.checkpoint import CheckpointStore
            CheckpointStore().clear(); st.success("Checkpointها پاک شد.")
        return (provider, api_key, model, target_lang, chunk_size, workers, delay,
                use_memory, custom_base, fallback_cfg, safe_opt, protect_spans,
                use_checkpoint, adaptive_batch, do_probe, smart_routing, registry, short_prompt,
                fallback_label, failover_enabled, failover_chain_text, failover_retries,
                failover_cost_cap, failover_smart, show_details)


(provider, api_key, model, target_lang, chunk_size, workers, delay, use_cache, custom_base,
 fallback_cfg, safe_opt, protect_spans, use_checkpoint, adaptive_batch, do_probe,
 smart_routing, registry, short_prompt, fallback_name, failover_enabled, failover_chain_text,
 failover_retries, failover_cost_cap, failover_smart, show_details) = _translator_settings()

def _make_translator(cfg):
    p, key, mdl, base = cfg
    if p == "openai_compatible":
        return get_translator(p, api_key=key, model_name=mdl, base_url=base, provider_name="OpenAI-compatible")
    return get_translator(p, api_key=key, model_name=mdl)


primary_cfg = (provider, api_key, model, custom_base)


def _metered(cfg, project: str = ""):
    """Every foreground request is recorded in the local usage ledger."""
    p, _key, mdl, _base = cfg
    return UsageTrackingTranslator(_make_translator(cfg), provider=p, model=mdl, project=project)


def _resolve_translator(project: str = "", job_id: str = ""):
    """Failover-aware translator used by all foreground tabs."""
    if failover_enabled:
        chain = split_chain_text(failover_chain_text)
        if not chain:
            chain = [{"provider": provider, "model": model}]
        from trarium.failover import build_chain
        return build_chain(chain, retries=int(failover_retries),
                           cost_cap_usd=float(failover_cost_cap) or None,
                           smart=bool(failover_smart), project=project, job_id=job_id)
    primary_tr = _metered(primary_cfg, project)
    if fallback_cfg:
        fallback_tr = _metered(fallback_cfg, project)
        if smart_routing:
            from trarium.smart_router import SmartRoutingTranslator
            return SmartRoutingTranslator(primary_tr, fallback_tr)
        return FallbackTranslator([(provider, primary_tr), (fallback_name, fallback_tr)])
    return primary_tr


def _chain_config() -> list[dict]:
    if failover_enabled:
        chain = split_chain_text(failover_chain_text)
        if chain:
            return chain
    chain = [{"provider": provider, "model": model}]
    if fallback_cfg:
        chain.append({"provider": fallback_cfg[0], "model": fallback_cfg[2]})
    return chain


def _job_config(output_format: str = "txt", extra: dict | None = None) -> dict:
    config = {
        "provider_chain": _chain_config(),
        "provider": provider, "model": model,
        "chunk_size": int(chunk_size), "workers": int(workers), "delay": float(delay),
        "safe_optimization": bool(safe_opt), "protect_spans": bool(protect_spans),
        "use_checkpoint": bool(use_checkpoint), "use_cache": bool(use_cache),
        "adaptive_batch": bool(adaptive_batch), "short_prompt": str(short_prompt or ""),
        "output_format": output_format,
        "failover": {"retries": int(failover_retries),
                     "cost_cap_usd": float(failover_cost_cap) or None,
                     "smart": bool(failover_smart)},
    }
    config.update(extra or {})
    return config


job_manager = get_manager().start()
ui_ctx = {
    "provider": provider, "model": model, "api_key": api_key, "custom_base": custom_base,
    "target_lang": target_lang, "chunk_size": chunk_size, "workers": workers,
    "delay": delay, "short_prompt": short_prompt, "show_details": show_details,
    "failover_enabled": failover_enabled, "failover_chain_text": failover_chain_text,
    "job_config": _job_config, "resolve_translator": _resolve_translator,
    "save_uploaded": _store_uploaded, "manager": job_manager,
}
st.markdown(f'<div class="hero"><h1>🌿 Trarium</h1><p>ترجمه‌ی کتاب، PDF و وب‌ناول با تمرکز روی سرعت، حفظ قالب و جلوگیری از توقف‌های بی‌دلیل.</p><span class="status-pill">v{__version__}</span></div>', unsafe_allow_html=True)

from trarium import expansion_ui

(book_tab, layout_tab, web_tab, library_tab, queue_tab, site_tab, manga_tab,
 repair_tab, usage_tab) = st.tabs([
    "📄 ترجمه متنی", "🎨 حفظ قالب PDF", "🌐 وب‌ناول", "📚 کتابخانه",
    "🗂️ صف و تسک‌ها", "🌐 ترجمه وب‌سایت", "🖼️ افزایش کیفیت مانگا",
    "🩺 تعمیر و تبدیل", "📊 مصرف و هزینه"])



def render_upload(accepted=None, key="book"):
    if accepted:
        return st.file_uploader("فایل را انتخاب کنید", type=accepted, key=f"uploader_{key}")
    return st.file_uploader(
        "فایل را انتخاب کنید",
        type=None,
        key=f"uploader_{key}",
        help="هر فایلی که متن دارد. اگر فرمت قابل ترجمه نباشد، دلیلش را می‌گوید.",
    )


with book_tab:
    st.markdown("### 📄 ترجمه متنی")
    st.caption("برای PDF، TXT، EPUB، سندهای آفیس و هر فایلی که متن دارد. صفحه‌آرایی اصلی هدف نیست و هر خروجی یک بخش پایانی «ترجمه شده توسط تراریوم» دارد.")
    st.caption("هر فایلی که متن داشته باشد ترجمه می‌شود؛ حتی اگر پسوندش در لیست نباشد. اگر فایل ذاتاً متن ندارد (ویدیو، صدا، عکس) تراریوم صریح می‌گوید که نمی‌تواند.")
    translatable_formats, total_formats, non_text_formats = format_stats()
    with st.expander(f"📚 فرمت‌های پشتیبانی‌شده ({translatable_formats} پسوند قابل ترجمه)"):
        st.caption(
            f"تراریوم {total_formats} پسوند را می‌شناسد؛ {translatable_formats} پسوند متن دارد و ترجمه می‌شود "
            f"و {non_text_formats} پسوند عکس، صدا، ویدیو، آرشیو یا باینری است که با دلیل روشن رد می‌شود."
        )
        for family, examples in DISPLAY_FORMAT_GROUPS:
            st.markdown(f"**{family}** — `{examples}`")
        st.caption("فقط پرکاربردترین‌ها اینجاست. بقیهٔ پسوندها را با جست‌وجو پیدا کنید.")
        query = st.text_input("🔎 جست‌وجوی فرمت", placeholder="مثلاً srt یا kicad", key="format_search")
        if query.strip():
            found = search_formats(query)
            if found:
                st.code("  ".join(found), language=None)
                st.caption(f"{len(found)} پسوند پیدا شد.")
            else:
                st.caption("پسوندی با این نام پیدا نشد؛ با این حال فایل‌های متنیِ ناشناخته هم امتحان می‌شوند.")
        st.caption("سند Word/OpenDocument، PowerPoint، Excel، RTF، HTML/XML، FB2/MOBI، زیرنویس، کد، آرشیو ZIP با فایل متنی و پسوند ناشناخته هم امتحان می‌شوند.")
    uploaded = render_upload(key="book")
    if uploaded:
        digest = _store_uploaded(uploaded)
        suffix = Path(uploaded.name).suffix.lower()
        background = st.checkbox(
            "🕓 اجرا در پس‌زمینه (مستقل از صفحه؛ بعد از رفرش و ری‌استارت ادامه می‌دهد)",
            value=False, key="book_background",
            help="تسک در سرور اجرا می‌شود و در تب «صف و تسک‌ها» دیده می‌شود.",
        )
        bg_fmt = "txt"
        if background:
            bg_fmt = st.selectbox("فرمت خروجی تسک پس‌زمینه", ["txt", "epub", "html", "pdf"],
                                  key="book_bg_fmt")
            st.caption("زیرنویس، سند آفیس، JSON/CSV و فایل کد همیشه با فرمت خودشان ذخیره می‌شوند.")
        if background and st.button("🕓 افزودن به صف پس‌زمینه", type="primary", key="translate_text_bg"):
            job_id = ui_panels.submit_upload_job(ui_ctx, uploaded, config=_job_config(bg_fmt))
            st.success(f"تسک در صف قرار گرفت: {job_id}")
            st.query_params["job"] = job_id
            st.session_state["book_job_id"] = job_id
        if st.button("🔍 تحلیل و ترجمه", type="primary", key="translate_text", disabled=background):
            from trarium.extractors import get_extractor
            from trarium.extractors.generic import sniff_container
            path = _tmp_from_session(suffix)
            try:
                with st.spinner("در حال استخراج متن..."):
                    # OCR فقط برای PDF معنا دارد؛ تشخیص از روی محتوا، نه پسوند.
                    is_pdf = suffix == ".pdf" or sniff_container(Path(path)) == "pdf"
                    book = get_extractor(path, use_ocr=is_pdf).extract(path)
                st.info(f"📖 {book.title} — {book.total_chars:,} کاراکتر — {len(book.chapters)} بخش")
                if not book.chapters:
                    st.error("هیچ متن قابل ترجمه‌ای پیدا نشد.")
                elif not api_key:
                    st.error("API Key لازم است.")
                else:
                    translator = _resolve_translator(project=uploaded.name)
                    if use_cache and not isinstance(translator, (FallbackTranslator,)):
                        translator = CachedTranslator(translator, provider, model, instructions=short_prompt)
                    pipe = TranslationPipeline(translator, target_lang, chunk_size, delay, workers,
                                               safe_optimization=safe_opt, protect_nontranslatable=protect_spans,
                                               use_checkpoint=use_checkpoint, translation_memory=use_cache,
                                               adaptive_batch=adaptive_batch, registry=registry,
                                               short_prompt=short_prompt)
                    if do_probe:
                        from trarium.preflight import probe as run_probe, estimate_book
                        total_tokens, estimated_batches = estimate_book([str(c.get("content", "")) for c in book.chapters], batch_chars=chunk_size)
                        report = run_probe(translator, target_lang)
                        if not report.ok:
                            raise RuntimeError(f"Pre-flight ناموفق بود: {report.error}")
                        st.success(f"✅ Pre-flight موفق — سقف توکن اعلام‌شده: {report.token_limit:,} · متن کتاب: حدود {total_tokens:,} توکن · حدود {estimated_batches:,} Batch")
                    progress = st.progress(0.0, text="شروع ترجمه...")
                    with st.spinner("ترجمه در حال انجام است..."):
                        result = pipe.run(book)
                    progress.progress(1.0, text="✅ تمام شد")
                    st.session_state["text_result"] = result
            except UnsupportedFormatError as exc:
                st.warning(f"⚠️ {exc}")
            except Exception as exc:
                st.error(f"❌ {exc}")
            finally:
                try: os.unlink(path)
                except OSError: pass

        result = st.session_state.get("text_result")
        if result:
            st.divider()
            a, b, c, d = st.columns(4)
            a.metric("بخش‌ها", f"{result.translated_chunks}/{result.total_chunks}")
            b.metric("زمان", f"{result.elapsed_seconds:.1f} ثانیه")
            c.metric("Cache/Resume", f"{result.cache_hits + result.checkpoint_hits}")
            d.metric("درخواست API", result.api_requests)
            st.caption(f"توکن ورودی تقریبی/واقعی: {result.estimated_input_tokens:,} · خروجی: {result.estimated_output_tokens:,} · Placeholder: {result.protected_spans}")
            if show_details:
                ui_panels.render_foreground_details(result, provider, model, target_lang)
            st.text_area("پیش‌نمایش", result.translated_text[:12000], height=300)
            if result.translated_chunks == 0 and result.failed_chunks > 0:
                st.error("❌ هیچ بخش ترجمه نشد؛ خروجی قابل دانلود ساخته نمی‌شود.")
            else:
                fmt = st.radio("فرمت خروجی", ["TXT", "PDF", "EPUB", "HTML"], horizontal=True, key="text_format")
                with tempfile.TemporaryDirectory() as td:
                    out = str(Path(td) / f"{_safe_name(result.title)}_fa.{fmt.lower()}")
                    if fmt == "TXT": save_as_text(result, out); mime = "text/plain"
                    elif fmt == "PDF": save_as_pdf(result, out); mime = "application/pdf"
                    elif fmt == "EPUB": save_as_epub(result, out); mime = "application/epub+zip"
                    else: save_as_html(result, out); mime = "text/html"
                    st.download_button("⬇️ دانلود خروجی", Path(out).read_bytes(), Path(out).name, mime, type="primary", key="download_text_result")
        if st.session_state.get("book_job_id"):
            st.divider()
            st.markdown("#### 🕓 تسک پس‌زمینه")
            ui_panels.render_job_monitor(st.session_state["book_job_id"], live=show_details, prefix="book")

with layout_tab:
    st.markdown("### 🎨 حفظ قالب PDF")
    st.caption("متن اصلی در نواحی خودش جایگزین می‌شود. OCR کاملاً اختیاری است؛ PDFهای متنی و PDFهای OCR‌شده مستقیماً قابل استفاده‌اند.")
    st.info("💡 اگر فایل قبلاً با PDF24/iLovePDF OCR شده، OCR را دوباره روشن نکن؛ Trarium ابتدا لایه متن موجود را استفاده می‌کند.")
    uploaded = render_upload(["pdf"], key="layout")
    if uploaded:
        digest = _store_uploaded(uploaded)
        if st.session_state.get("layout_hash") != digest:
            st.session_state["layout_hash"] = digest
            st.session_state.pop("layout_analysis", None)
            st.session_state.pop("layout_ocr", None)
            st.session_state.pop("layout_result", None)

        if "layout_analysis" not in st.session_state:
            path = _tmp_from_session(".pdf")
            try:
                import pymupdf
                from trarium.pdf_optimized import extract_text_blocks
                doc = pymupdf.open(path)
                text_pages, scanned_pages = [], []
                for i, page in enumerate(doc):
                    try:
                        blocks = extract_text_blocks(page)
                    except Exception:
                        blocks = []
                    if any(len(b.get("text", "")) >= 4 for b in blocks): text_pages.append(i + 1)
                    else: scanned_pages.append(i + 1)
                doc.close()
                st.session_state["layout_analysis"] = {"total": len(text_pages) + len(scanned_pages), "text": text_pages, "scanned": scanned_pages}
            finally:
                try: os.unlink(path)
                except OSError: pass

        analysis = st.session_state["layout_analysis"]
        x, y, z = st.columns(3)
        x.metric("کل صفحات", analysis["total"])
        y.metric("صفحات دارای لایه متن", len(analysis["text"]))
        z.metric("صفحات بدون لایه متن", len(analysis["scanned"]))

        if analysis["scanned"]:
            st.markdown("#### 🔍 OCR")
            ocr_on = st.checkbox("فعال‌کردن OCR برای صفحات بدون متن", value=False)
            if not ocr_on:
                st.warning("OCR خاموش است؛ صفحات اسکن‌شده بدون تغییر در خروجی باقی می‌مانند. اگر PDF را با PDF24/iLovePDF قبلاً OCR کرده‌اید، دوباره OCR لازم نیست.")
            else:
                from trarium.pdf_optimized import _has_paddleocr, _has_surya, _has_easyocr, _is_tesseract_installed
                options = ["auto"]
                labels = {"auto": "خودکار (PaddleOCR → Surya → EasyOCR → Vision)"}
                gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
                if _has_paddleocr(): options.append("paddleocr"); labels["paddleocr"] = "PaddleOCR"
                if _has_surya(): options.append("surya"); labels["surya"] = "Surya"
                if _has_easyocr(): options.append("easyocr"); labels["easyocr"] = "EasyOCR"
                if api_key: options.append("openrouter"); labels["openrouter"] = "OpenRouter Vision"
                if gemini_key: options.append("gemini"); labels["gemini"] = "Gemini Vision"
                if _is_tesseract_installed(): options.append("tesseract"); labels["tesseract"] = "Tesseract"
                method = st.selectbox("موتور OCR", options, format_func=lambda x: labels[x])
                ocr_lang = st.selectbox("زبان صفحه", ["eng", "fas+eng", "jpn", "ara+eng"], index=0)
                b1, b2 = st.columns(2)
                with b1:
                    run_ocr = st.button("🔎 اجرای OCR", type="primary", key="layout_ocr_run")
                with b2:
                    benchmark_ocr_now = st.button("⚡ بنچمارک ۱۰ صفحه", key="layout_ocr_benchmark")

                if benchmark_ocr_now:
                    from trarium.pdf_optimized import _has_paddleocr, _has_surya, _has_easyocr, _is_tesseract_installed
                    from trarium.benchmark import benchmark_ocr
                    selected_methods = []
                    for candidate, available in [
                        ("paddleocr", _has_paddleocr()),
                        ("surya", _has_surya()),
                        ("easyocr", _has_easyocr()),
                        ("tesseract", _is_tesseract_installed()),
                    ]:
                        if available:
                            selected_methods.append(candidate)
                    if api_key:
                        selected_methods.append("openrouter")
                    if not selected_methods:
                        st.warning("هیچ موتور OCR قابل اجرا پیدا نشد. ابتدا یک موتور OCR اختیاری نصب کنید یا برای Vision کلید API وارد کنید.")
                    else:
                        bench_path = _tmp_from_session(".pdf")
                        try:
                            rows = benchmark_ocr(bench_path, selected_methods, [p - 1 for p in analysis["scanned"][:10]], lang=ocr_lang)
                            st.dataframe(rows, use_container_width=True, hide_index=True)
                        except Exception as exc:
                            st.error(f"❌ بنچمارک OCR: {exc}")
                        finally:
                            try: os.unlink(bench_path)
                            except OSError: pass

                if run_ocr:
                    import pymupdf
                    from trarium.pdf_optimized import (extract_ocr_blocks, extract_ocr_blocks_batch_gemini,
                                                       extract_ocr_blocks_batch_openrouter)
                    path = _tmp_from_session(".pdf")
                    result_map = {}
                    try:
                        doc = pymupdf.open(path)
                        if method == "gemini":
                            result_map = extract_ocr_blocks_batch_gemini(
                                [(p - 1, doc[p - 1]) for p in analysis["scanned"]],
                                api_key=gemini_key, batch_size=3)
                        elif method == "openrouter":
                            result_map = extract_ocr_blocks_batch_openrouter([(p - 1, doc[p - 1]) for p in analysis["scanned"]], api_key=api_key, model_name=OPENROUTER_VISION_MODELS["Auto (رایگان)"], batch_size=3)
                        else:
                            bar = st.progress(0.0)
                            for j, p in enumerate(analysis["scanned"], 1):
                                result_map[p - 1] = extract_ocr_blocks(doc[p - 1], lang=ocr_lang, api_key=api_key, vision_model=OPENROUTER_VISION_MODELS["Auto (رایگان)"], ocr_method=method)
                                bar.progress(j / len(analysis["scanned"]), text=f"OCR صفحه {p}/{analysis['total']}...")
                            bar.empty()
                        doc.close()
                        st.session_state["layout_ocr"] = result_map
                        try:
                            from trarium.usage import record_ocr
                            record_ocr(pages=sum(bool(v) for v in result_map.values()),
                                       requests=len(analysis["scanned"]),
                                       provider="openrouter" if method == "openrouter" else method,
                                       project=uploaded.name)
                        except Exception:
                            pass
                        st.success(f"✅ OCR برای {sum(bool(v) for v in result_map.values())} صفحه نتیجه داد.")
                    except Exception as exc:
                        st.error(f"❌ OCR: {exc}")
                    finally:
                        try: os.unlink(path)
                        except OSError: pass

        ocr_results = st.session_state.get("layout_ocr", {})
        skip = st.multiselect("صفحات بدون ترجمه (اختیاری)", list(range(1, analysis["total"] + 1)), key="layout_skip")
        layout_background = st.checkbox(
            "🕓 اجرا در پس‌زمینه (مستقل از صفحه)", value=False, key="layout_background",
            help="برای PDFهای دارای لایهٔ متن. OCR پیش‌محاسبه‌شده در حالت پس‌زمینه اعمال نمی‌شود.",
        )
        if layout_background and st.button("🕓 افزودن PDF به صف", type="primary", key="layout_queue"):
            job_id = ui_panels.submit_upload_job(ui_ctx, uploaded, kind="pdf",
                                                 config=_job_config("pdf"))
            st.success(f"تسک PDF در صف قرار گرفت: {job_id}")
            st.query_params["job"] = job_id
            st.session_state["layout_job_id"] = job_id
        if st.button("🚀 شروع ترجمه PDF", type="primary", key="layout_translate", disabled=layout_background):
            if not api_key:
                st.error("API Key لازم است.")
            else:
                path = _tmp_from_session(".pdf")
                out = Path(tempfile.gettempdir()) / f"trarium_{digest[:10]}_translated.pdf"
                try:
                    translator = _resolve_translator(project=uploaded.name)
                    if use_cache and not isinstance(translator, (FallbackTranslator,)):
                        translator = CachedTranslator(translator, provider, model, instructions=short_prompt)
                    from trarium.pdf_optimized import translate_pdf_optimized
                    bar = st.progress(0.0)
                    def cb(p, msg): bar.progress(min(max(p, 0), 1), text=msg)
                    _, stats = translate_pdf_optimized(path, translator, target_lang, str(out),
                                           skip_pages={p - 1 for p in skip},
                                           precomputed_ocr=ocr_results,
                                           enable_ocr=False,
                                           translation_workers=workers,
                                           translation_batch_chars=chunk_size,
                                           progress_callback=cb,
                                           return_stats=True,
                                           safe_optimization=safe_opt,
                                           protect_nontranslatable=protect_spans,
                                           adaptive_batch=adaptive_batch,
                                           use_checkpoint=use_checkpoint,
                                           use_cache=use_cache,
                                           short_prompt=short_prompt)
                    st.session_state["layout_result"] = Path(out).read_bytes()
                    if stats.translated_blocks == 0 or stats.pages_written == 0:
                        st.session_state.pop("layout_result", None)
                        try: Path(out).unlink(missing_ok=True)
                        except Exception: pass
                        st.error("❌ هیچ بلاکی با موفقیت در PDF جایگزین نشد؛ خروجی خام ساخته/تحویل داده نشد.")
                    elif stats.failed_blocks or stats.write_failures:
                        detail = []
                        if stats.failed_blocks: detail.append(f"{stats.failed_blocks} بلاک بدون ترجمه")
                        if stats.write_failures: detail.append(f"{stats.write_failures} خطای درج")
                        st.warning(f"⚠️ PDF ساخته شد؛ {stats.translated_blocks} بلاک نوشته شد؛ {' و '.join(detail)}. متن اصلیِ ناموفق حفظ شد.")
                        if stats.first_write_error:
                            st.caption(f"جزئیات اولین خطای درج: {stats.first_write_error}")
                    else:
                        st.success(f"🎉 PDF آماده شد؛ {stats.translated_blocks} بلاک ترجمه و {stats.pages_written} صفحه بازنویسی شد.")
                    st.caption(f"Cache/Checkpoint: {stats.cache_hits + stats.checkpoint_hits} · درخواست API: {stats.api_requests} · توکن ورودی: {stats.input_tokens:,} · خروجی: {stats.output_tokens:,} · Placeholder: {stats.protected_spans}")
                except Exception as exc:
                    from trarium.translators.openrouter_http import OpenRouterSecurityBlockError
                    if isinstance(exc, OpenRouterSecurityBlockError) and exc.source == "waf":
                        st.error("❌ OpenRouter اتصال را در لایهٔ امنیتی/WAF مسدود کرد. این مشکل از PDF، OCR یا متن ترجمه نیست و retry بیشتر فقط زمان هدر می‌دهد. از تست اتصال بالا استفاده کنید یا ارائه‌دهندهٔ دیگری را انتخاب کنید.")
                    else:
                        st.error(f"❌ {exc}")
                finally:
                    try: os.unlink(path)
                    except OSError: pass

        if st.session_state.get("layout_result"):
            st.download_button("⬇️ دانلود PDF ترجمه‌شده", st.session_state["layout_result"], f"{_safe_name(uploaded.name.rsplit('.',1)[0])}_fa.pdf", "application/pdf", type="primary", key="layout_download")
        if st.session_state.get("layout_job_id"):
            ui_panels.render_job_monitor(st.session_state["layout_job_id"], live=show_details, prefix="layout")

with web_tab:
    st.markdown("### 🌐 ترجمه وب‌ناول")
    st.caption("از لینک واقعی Next Chapter جلو می‌رود؛ URL فصل‌های بعدی را حدس نمی‌زند و خروجی یک صفحه/بخش پایانی «ترجمه شده توسط تراریوم» دارد.")
    url = st.text_input("لینک فصل شروع", placeholder="https://example.com/chapter-1/")
    c1, c2, c3 = st.columns(3)
    with c1: count = st.selectbox("تعداد فصل", [1, 2, 5, 10, 20], index=2)
    with c2: use_browser = st.checkbox("مرورگر برای سایت‌های JS", value=False)
    with c3: with_images = st.checkbox("دانلود عکس‌ها", value=False)

    if st.button("🔎 دریافت فصل‌ها", type="primary", key="scrape_novel") and url.strip():
        from trarium.novel.scraper import NovelScraper
        try:
            scraper = NovelScraper(use_browser=use_browser, delay=0.5, max_chapters=count)
            with st.spinner("در حال دریافت فصل‌ها..."):
                chapters = scraper.scrape_chain(url.strip(), count)
            if not chapters:
                st.error("هیچ فصل قابل استفاده‌ای پیدا نشد.")
            else:
                st.session_state["novel_results"] = chapters
                st.success(f"✅ {len(chapters)} فصل پیدا شد.")
        except Exception as exc:
            st.error(f"❌ {exc}")

    chapters = st.session_state.get("novel_results", [])
    if chapters:
        for ch in chapters[:3]:
            st.markdown(f"**{ch.number or '؟'} — {ch.title or 'بدون عنوان'}** · {ch.word_count:,} کلمه")
            st.text_area(f"پیش‌نمایش فصل {ch.number or ''}", ch.text[:2500], height=140, key=f"preview_{ch.url}")
        if len(chapters) > 3: st.caption(f"... و {len(chapters) - 3} فصل دیگر")

        if with_images and st.button("🖼️ دانلود عکس‌ها", key="novel_images"):
            from trarium.novel.scraper import NovelScraper
            images = []
            for ch in chapters: images.extend(ch.images)
            with st.spinner("در حال دانلود عکس‌ها..."):
                downloaded = NovelScraper.download_all_images(images)
            if downloaded:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for img in downloaded: zf.writestr(img.filename, img.data)
                st.download_button("⬇️ دانلود ZIP عکس‌ها", buf.getvalue(), "novel_images.zip", "application/zip", key="novel_images_download")
            else:
                st.info("عکسی دانلود نشد.")

        if st.button("🚀 ترجمه وب‌ناول", type="primary", key="translate_novel"):
            if not api_key:
                st.error("API Key لازم است.")
            else:
                from trarium.extractors.base import ExtractedBook
                book = ExtractedBook(title=chapters[0].title or "Web Novel", author="", chapters=[{"title": f"فصل {c.number or i+1}: {c.title}", "content": c.text} for i, c in enumerate(chapters) if c.text])
                try:
                    translator = _resolve_translator(project="وب‌ناول")
                    if use_cache and not isinstance(translator, (FallbackTranslator,)):
                        translator = CachedTranslator(translator, provider, model, instructions=short_prompt)
                    result = TranslationPipeline(translator, target_lang, chunk_size, delay, workers,
                                                 short_prompt=short_prompt).run(book)
                    st.session_state["novel_translation"] = result
                except Exception as exc:
                    st.error(f"❌ {exc}")

        result = st.session_state.get("novel_translation")
        if result:
            st.divider()
            a, b = st.columns(2)
            a.metric("تعداد بخش‌ها", f"{result.translated_chunks}/{result.total_chunks}")
            b.metric("بخش ناموفق", result.failed_chunks)
            if result.translated_chunks == 0 and result.failed_chunks > 0:
                st.error("❌ هیچ فصل/بخشی ترجمه نشد؛ خروجی قابل دانلود ساخته نمی‌شود.")
            else:
                fmt = st.radio("فرمت خروجی وب‌ناول", ["TXT", "PDF", "EPUB", "HTML"], horizontal=True, key="novel_format")
                with tempfile.TemporaryDirectory() as td:
                    out = str(Path(td) / f"{_safe_name(result.title)}_translated.{fmt.lower()}")
                    if fmt == "TXT": save_as_text(result, out); mime = "text/plain"
                    elif fmt == "PDF": save_as_pdf(result, out); mime = "application/pdf"
                    elif fmt == "EPUB": save_as_epub(result, out); mime = "application/epub+zip"
                    else: save_as_html(result, out); mime = "text/html"
                    st.download_button("⬇️ دانلود خروجی", Path(out).read_bytes(), Path(out).name, mime, type="primary", key="novel_download")

with library_tab:
    expansion_ui.render_library_tab(ui_ctx)

with queue_tab:
    ui_panels.render_queue_tab(ui_ctx)
    expansion_ui.render_resume_section()

with site_tab:
    ui_panels.render_website_tab(ui_ctx)

with manga_tab:
    ui_panels.render_manga_tab(ui_ctx)

with repair_tab:
    expansion_ui.render_repair_tab(ui_ctx)

with usage_tab:
    ui_panels.render_usage_tab(ui_ctx)
    expansion_ui.render_analytics_section()

st.divider()
st.caption("Trarium — OCR اختیاری • ترجمه مقاوم • حفظ قالب PDF • تسک پس‌زمینه • فونت Sahel")
