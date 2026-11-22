"""تب‌های Streamlit: «🎙️ دوبله» و «📝 زیرنویس»."""
from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path

import streamlit as st

from trarium.media.ass import FONT_FAMILIES, AssStyle
from trarium.media.langs import LANGUAGES

_MIME = {".mp4": "video/mp4", ".m4a": "audio/mp4", ".wav": "audio/wav", ".srt": "application/x-subrip",
         ".vtt": "text/vtt", ".ass": "text/plain", ".txt": "text/plain"}
_LABELS = {"mp4": "🎬 ویدیوی دوبله‌شده (MP4)", "audio": "🔊 صدای دوبله + صدای اصلی (M4A)",
           "voice": "🗣️ فقط صدای دوبله (WAV)", "srt": "📄 زیرنویس SRT", "vtt": "📄 زیرنویس VTT",
           "srt_original": "📄 زیرنویس زبان اصلی (SRT)", "hardsub": "🎬 ویدیو با زیرنویس حک‌شده (MP4)",
           "ass": "📄 فایل ASS (برای ویرایش استایل)"}


def _home() -> Path:
    base = Path(os.environ.get("TRARIUM_HOME") or Path.home() / ".trarium") / "media"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _gemini_keys(ctx: dict, key: str) -> list[str]:
    from trarium.media.gemini_client import keys_from_env

    keys = keys_from_env()
    if ctx.get("provider") == "gemini" and ctx.get("api_key"):
        keys.insert(0, ctx["api_key"])
    typed = st.text_input("کلیدهای Gemini (اختیاری، با ویرگول جدا کنید؛ اول کلید ۱، بعد کلید ۲)",
                          type="password", key=f"{key}_gemini_keys",
                          help="اگر سهمیهٔ کلید اول تمام شود (429/RESOURCE_EXHAUSTED) خودکار کلید بعدی استفاده می‌شود. "
                               "کلیدها فقط در همین درخواست استفاده می‌شوند و ذخیره نمی‌شوند.")
    typed_keys = [k.strip() for k in re.split(r"[,\s]+", typed or "") if k.strip()]
    keys = list(dict.fromkeys(typed_keys + keys))
    st.caption(f"کلید Gemini در دسترس: {len(keys)}" + (" (از تنظیمات سرور)" if keys and not typed_keys else ""))
    return keys


def _language_picker(key: str, allow_none: bool = False) -> str | None:
    options = (["بدون ترجمه (فقط زبان اصلی)"] if allow_none else []) + [l.label for l in LANGUAGES] + ["زبان دیگر…"]
    choice = st.selectbox("زبان مقصد", options, index=1 if allow_none else 0, key=f"{key}_lang")
    if choice == "زبان دیگر…":
        free = st.text_input("نام یا کد زبان (مثلاً sw، Swahili، Polski)", key=f"{key}_lang_free")
        return free.strip() or None
    if allow_none and choice.startswith("بدون"):
        return None
    return next(l.code for l in LANGUAGES if l.label == choice)


def _source_input(key: str):
    kind = st.radio("ورودی", ["🔗 لینک یوتیوب / ویدیو", "📁 آپلود فایل صوتی یا ویدیویی"], horizontal=True,
                    key=f"{key}_kind")
    if kind.startswith("🔗"):
        url = st.text_input("لینک", placeholder="https://www.youtube.com/watch?v=...", key=f"{key}_url")
        cookies = st.file_uploader("cookies.txt یوتیوب (اختیاری)", type=["txt"], key=f"{key}_cookies",
                                   help="فقط وقتی سرور با خطای «not a bot» بسته شده. بعد از دانلود پاک می‌شود.")
        return ("url", url.strip(), cookies)
    up = st.file_uploader("فایل (mp4, mkv, webm, mov, mp3, m4a, wav, ogg, …)", type=None, key=f"{key}_file")
    return ("file", up, None)


def _prepare_source(spec, work: Path, audio_only: bool, log) -> tuple[Path, str]:
    from trarium.media.source import download, is_url, save_upload

    kind, value, cookies = spec
    if kind == "url":
        if not is_url(value):
            raise ValueError("لینک معتبر نیست.")
        cookie_path = None
        if cookies is not None:
            fd, cookie_path = tempfile.mkstemp(prefix="trarium_yt_", suffix=".txt")
            with os.fdopen(fd, "wb") as handle:
                handle.write(cookies.getvalue())
            os.chmod(cookie_path, 0o600)
        try:
            log("دانلود با yt-dlp…")
            path = download(value, work / "src", audio_only=audio_only, cookies=cookie_path)
        finally:
            if cookie_path:
                Path(cookie_path).unlink(missing_ok=True)
        return path, "video"
    if value is None:
        raise ValueError("فایلی انتخاب نشده.")
    stem = re.sub(r"[^\w\-]+", "_", Path(value.name).stem)[:40] or "video"
    return save_upload(value.getvalue(), value.name, work / "src"), stem


def _style_controls(key: str) -> AssStyle:
    c1, c2, c3 = st.columns(3)
    font = c1.selectbox("فونت", list(FONT_FAMILIES), key=f"{key}_font")
    size = c2.slider("اندازهٔ فونت (٪ ارتفاع تصویر)", 3.0, 10.0, 6.0, 0.5, key=f"{key}_size")
    pos = c3.selectbox("جایگاه", ["پایین", "بالا", "وسط"], key=f"{key}_pos")
    c4, c5 = st.columns(2)
    margin = c4.slider("فاصله از لبه (٪)", 1.0, 20.0, 6.0, 0.5, key=f"{key}_margin")
    box = c5.checkbox("کادر تیره پشت متن", value=False, key=f"{key}_box")
    return AssStyle(font=font, size_pct=size, position={"پایین": "bottom", "بالا": "top", "وسط": "middle"}[pos],
                    margin_pct=margin, box=box)


def _show_result(state_key: str) -> None:
    res = st.session_state.get(state_key)
    if not res:
        return
    st.divider()
    stats = res["stats"]
    cols = st.columns(4)
    cols[0].metric("طول ورودی", f"{stats.get('duration', 0):.1f} ث")
    cols[1].metric("بخش‌ها", stats.get("segments", stats.get("audio_chunks", "-")))
    cols[2].metric("زمان کل", f"{stats.get('total_seconds', 0):.0f} ث")
    cols[3].metric("درخواست Gemini", stats.get("gemini_calls", "-"))
    for name, path in res["files"].items():
        path = Path(path)
        if not path.exists():
            continue
        if path.suffix == ".mp4" and path.stat().st_size < 200 * 1024 * 1024:
            st.video(str(path))
        st.download_button(_LABELS.get(name, name), path.read_bytes(), path.name,
                           _MIME.get(path.suffix, "application/octet-stream"), key=f"{state_key}_{name}")
    with st.expander("جزئیات و گزارش"):
        st.json(stats)
        st.code("\n".join(res["log"][-80:]) or "-", language=None)


def _runner(state_key: str, fn) -> None:
    from trarium.media.gemini_client import GeminiClient

    work = _home() / f"{state_key}_{time.strftime('%Y%m%d_%H%M%S')}"
    lines: list[str] = []
    bar = st.progress(0.0, text="شروع…")
    box = st.empty()

    def log(msg: str):
        lines.append(msg)
        box.caption(msg)

    def progress(p: float, msg: str = ""):
        bar.progress(min(1.0, max(0.0, p)), text=msg or None)

    try:
        result, client = fn(work, log, progress, GeminiClient)
        result.stats["gemini_calls"] = getattr(client, "calls", 0) if client else 0
        st.session_state[state_key] = {"files": {k: str(v) for k, v in result.files.items()},
                                       "stats": result.stats, "log": result.log or lines}
        st.success("✅ انجام شد.")
    except Exception as exc:
        st.error(f"❌ {exc}")
        if lines:
            st.code("\n".join(lines[-30:]), language=None)


# ------------------------------------------------------------------ dubbing
def render_dub_tab(ctx: dict) -> None:
    st.markdown("### 🎙️ دوبله")
    st.caption("لینک یوتیوب یا فایل صوتی/ویدیویی بدهید و زبان مقصد را انتخاب کنید. خروجی: ویدیوی دوبله‌شده (MP4)، "
               "صدای دوبله و زیرنویس ترجمه‌شده. فقط سرویس‌های رایگان استفاده می‌شوند.")
    spec = _source_input("dub")
    target = _language_picker("dub")
    mode = st.radio("حالت", ["⚡ Gemini Live (دوبلهٔ همزمان)", "🎧 عادی (رونویسی → ترجمه → صداسازی)"],
                    key="dub_mode", help="Live: مدل gemini-3.5-live-translate-preview صدای ویدیو را با سرعت واقعی "
                    "می‌شنود و همزمان ترجمه و بیان می‌کند (زمان اجرا ≈ طول ویدیو). اگر سهمیه/مدل در دسترس نبود: "
                    "کلید بعدی → مدل native-audio → حالت عادی. عادی: کیفیت ترجمه بهتر و زمان‌بندی دقیق‌تر.")
    c1, c2 = st.columns(2)
    audio_mode = c1.selectbox("صدای اصلی", ["حذف گوینده، موزیک بماند (پیشنهادی)", "کم شدن زیر دوبله",
                                            "کم در کل ویدیو", "حذف کامل"], key="dub_audio_mode",
                              help="حذف گوینده با Demucs: صدای زبان اصلی کامل حذف و فقط موزیک/افکت می‌ماند.")
    gender = c2.selectbox("صدای گوینده", ["زن", "مرد"], key="dub_gender")
    audio_mode = {"حذف گوینده، موزیک بماند (پیشنهادی)": "novoice", "کم شدن زیر دوبله": "duck", "کم در کل ویدیو": "lower", "حذف کامل": "replace"}[audio_mode]
    gender = "male" if gender == "مرد" else "female"
    with st.expander("تنظیمات بیشتر"):
        bed = st.slider("بلندی صدای اصلی زیر دوبله", 0.0, 0.6, 0.18, 0.02, key="dub_bed")
        tts_engine = st.selectbox("موتور صدا (حالت عادی)", ["خودکار: Gemini TTS → edge-tts", "فقط Gemini TTS",
                                                            "فقط edge-tts"], key="dub_tts")
        voice = st.text_input("نام صدای edge-tts (اختیاری، مثل fa-IR-FaridNeural)", key="dub_voice")
        max_tempo = st.slider("حداکثر تندکردن گفتار برای جا شدن در زمان", 1.0, 1.6, 1.4, 0.05, key="dub_tempo")
        burn = st.checkbox("زیرنویس ترجمه هم روی ویدیو حک شود", value=False, key="dub_burn")
        glossary = st.text_area("واژه‌نامه/لحن برای Live (اختیاری)", key="dub_glossary",
                                placeholder="مثلاً: نام‌ها را ترجمه نکن؛ لحن رسمی.")
        lag = st.slider("جبران تأخیر Live (ثانیه، ۰ = خودکار)", 0.0, 5.0, 0.0, 0.25, key="dub_lag")
    keys = _gemini_keys(ctx, "dub")
    if st.button("🎙️ شروع دوبله", type="primary", key="dub_run"):
        if not target:
            st.warning("زبان مقصد را انتخاب کنید.")
            return
        from trarium.media import pipelines

        def job(work, log, progress, Client):
            client = Client(keys, log=log) if keys else None
            source, stem = _prepare_source(spec, work, audio_only=False, log=log)
            if mode.startswith("⚡"):
                if client is None:
                    raise RuntimeError("حالت Live کلید Gemini لازم دارد.")
                res = pipelines.dub_live(source, work, target=target, client=client, glossary=glossary,
                                         lag=lag or None, audio_mode=audio_mode, bed=bed,
                                         translator=ctx["resolve_translator"]("dub"), gender=gender,
                                         stem=stem, log=log, progress=progress)
            else:
                engine = "auto" if tts_engine.startswith("خودکار") else ("gemini" if "Gemini" in tts_engine else "edge")
                res = pipelines.dub_normal(source, work, target=target, translator=ctx["resolve_translator"]("dub"),
                                           client=client, tts_engine=engine if client else "edge", gender=gender,
                                           voice=voice.strip(), audio_mode=audio_mode, bed=bed,
                                           max_tempo=max_tempo, burn_subs=burn, stem=stem, log=log,
                                           progress=progress)
            return res, client

        _runner("dub_result", job)
    _show_result("dub_result")


# ---------------------------------------------------------------- subtitles
def render_subtitle_tab(ctx: dict) -> None:
    st.markdown("### 📝 زیرنویس‌ساز")
    st.caption("گفتار ویدیو/صدا استخراج و زیرنویس زبان اصلی ساخته می‌شود؛ در صورت تمایل به هر زبانی ترجمه می‌شود. "
               "خروجی: فایل SRT/VTT، ویدیوی با زیرنویس حک‌شده (فونت فارسی، راست‌به‌چپ درست) یا هر دو.")
    spec = _source_input("subs")
    target = _language_picker("subs", allow_none=True)
    output = st.radio("خروجی", ["📄 فایل زیرنویس (SRT + VTT)", "🎬 زیرنویس حک‌شده روی ویدیو", "هر دو",
                                "📤 استخراج زیرنویس داخل ویدیو"], horizontal=True, key="subs_output")
    if output.startswith("📤"):
        st.caption("زیرنویس‌های متنی داخل فایل (MKV/MP4) جدا و SRT می‌شوند؛ اگر زبان مقصد انتخاب شود ترجمه هم می‌شوند.")
        if st.button("📤 استخراج زیرنویس", type="primary", key="subs_extract"):
            from trarium.media import pipelines

            def ejob(work, log, progress, Client):
                source, stem = _prepare_source(spec, work, audio_only=False, log=log)
                res = pipelines.extract_subtitles(source, work, target=target,
                                                  translator=ctx["resolve_translator"]("subtitle") if target else None,
                                                  stem=stem, log=log, progress=progress)
                return res, None

            _runner("subs_result", ejob)
        _show_result("subs_result")
        return
    bilingual = st.checkbox("دوزبانه (ترجمه + متن اصلی)", value=False, key="subs_bilingual",
                            disabled=not target)
    engine = st.selectbox("موتور تشخیص گفتار", ["خودکار: Gemini → faster-whisper", "فقط Gemini",
                                                "فقط faster-whisper (CPU، بدون کلید)"], key="subs_engine")
    burn = not output.startswith("📄")
    style = _style_controls("subs") if burn else None
    keys = _gemini_keys(ctx, "subs")
    if st.button("📝 ساخت زیرنویس", type="primary", key="subs_run"):
        from trarium.media import pipelines

        eng = "auto" if engine.startswith("خودکار") else ("gemini" if "Gemini" in engine else "whisper")

        def job(work, log, progress, Client):
            client = Client(keys, log=log) if keys else None
            source, stem = _prepare_source(spec, work, audio_only=not burn, log=log)
            res = pipelines.make_subtitles(source, work, target=target,
                                           translator=ctx["resolve_translator"]("subtitle") if target else None,
                                           client=client, engine=eng if client else "whisper",
                                           bilingual=bilingual, burn=burn, style=style,
                                           write_files=not output.startswith("🎬"), stem=stem, log=log,
                                           progress=progress)
            return res, client

        _runner("subs_result", job)
    _show_result("subs_result")
