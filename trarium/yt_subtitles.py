"""ترجمهٔ زیرنویس یوتیوب: دانلود زیرنویس با yt-dlp و ترجمه با حفظ زمان‌بندی.

قبلاً تراریوم فقط فایل زیرنویسی را ترجمه می‌کرد که کاربر از قبل داشت و هیچ
ورودی «لینک ویدیو» نداشت. این ماژول لینک یوتیوب را با yt-dlp (کتابخانهٔ
پایتونی) باز می‌کند، زیرنویس دستی یا خودکار را دانلود می‌کند و همان مسیر
زیرنویس‌های معمولی را جلو می‌برد؛ زمان‌بندی هر cue دست‌نخورده می‌ماند.
"""
from __future__ import annotations

import importlib.util
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from trarium.subtitles import SubtitleFile, read_subtitles

# فرمت‌هایی که yt-dlp می‌دهد و خوانندهٔ زیرنویس تراریوم پشتیبانی می‌کند.
PREFERRED_FORMATS = ("vtt", "srt", "srv3", "ttml", "json3")
DEFAULT_LANGUAGES = ("en", "fa", "ar", "tr")


class YouTubeSubtitleError(RuntimeError):
    """خطای قابل‌نمایش برای کاربر (لینک، کوکی، دسترسی یا نصب yt-dlp)."""


@dataclass
class SubtitleTrack:
    path: Path
    language: str
    kind: str                       # manual | automatic
    title: str = ""
    video_id: str = ""
    url: str = ""
    extra: dict = field(default_factory=dict)


def has_ytdlp() -> bool:
    try:
        return importlib.util.find_spec("yt_dlp") is not None
    except Exception:
        return False


def parse_video_id(url: str) -> str:
    """شناسهٔ ۱۱ حرفی ویدیو را از هر شکل لینک یوتیوب بیرون می‌کشد."""
    value = (url or "").strip()
    if not value:
        raise YouTubeSubtitleError("لینک ویدیوی یوتیوب خالی است.")
    patterns = (
        r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/|/live/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    raise YouTubeSubtitleError("این لینک یوتیوب معتبر نیست؛ لینک کامل ویدیو را بدهید.")


def _normalise_language(code: str) -> str:
    return (code or "").strip().lower().replace("_", "-")


def choose_track(manual: dict, automatic: dict, preferred=()) -> tuple[str, str]:
    """انتخاب زبان و نوع زیرنویس؛ اول دستی، بعد خودکار، بعد اولین موجود.

    ``preferred`` می‌تواند کد زبان دقیق یا پیشوند باشد (``en`` → ``en-US``).
    """
    manual = manual or {}
    automatic = automatic or {}
    wanted = [_normalise_language(code) for code in (preferred or ()) if str(code).strip()]
    for language in wanted:
        for source, kind in ((manual, "manual"), (automatic, "automatic")):
            if language in source:
                return language, kind
            for code in source:
                if _normalise_language(code).split("-")[0] == language.split("-")[0]:
                    return code, kind
    if manual:
        return next(iter(manual)), "manual"
    if automatic:
        return next(iter(automatic)), "automatic"
    raise YouTubeSubtitleError("این ویدیو هیچ زیرنویسی ندارد (نه دستی و نه خودکار).")


def _youtube_dl():
    if not has_ytdlp():
        raise YouTubeSubtitleError(
            "برای دانلود زیرنویس یوتیوب باید بستهٔ yt-dlp نصب باشد: pip install yt-dlp")
    import yt_dlp
    return yt_dlp


def _options(workdir: Path, *, cookies_file: str | None = None,
             browser: str | None = None, proxy: str | None = None,
             languages=(), write: bool = True) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "/".join(PREFERRED_FORMATS),
        "subtitleslangs": list(languages or DEFAULT_LANGUAGES),
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "retries": 3,
        "socket_timeout": 30,
    }
    if write is False:
        options.pop("writesubtitles", None)
        options.pop("writeautomaticsub", None)
    cookies = cookies_file or os.environ.get("TRARIUM_YTDLP_COOKIES") or ""
    if cookies and Path(cookies).exists():
        options["cookiefile"] = str(cookies)
    browser = (browser or os.environ.get("TRARIUM_YTDLP_BROWSER") or "").strip()
    if browser:
        options["cookiesfrombrowser"] = (browser, None, None, None)
    proxy = (proxy or os.environ.get("TRARIUM_YTDLP_PROXY") or "").strip()
    if proxy:
        options["proxy"] = proxy
    return options


def list_subtitle_tracks(url: str, *, cookies_file: str | None = None,
                         browser: str | None = None, proxy: str | None = None) -> list[dict]:
    """زیرنویس‌های موجود برای یک ویدیو را بدون دانلود کردن فهرست می‌کند."""
    yt_dlp = _youtube_dl()
    video_id = parse_video_id(url)
    with tempfile.TemporaryDirectory() as tmp:
        options = _options(Path(tmp), cookies_file=cookies_file, browser=browser,
                           proxy=proxy, write=False)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except Exception as exc:
            raise _friendly_error(exc) from exc
    tracks = []
    for kind, source in (("manual", info.get("subtitles") or {}),
                         ("automatic", info.get("automatic_captions") or {})):
        for code, formats in source.items():
            ext = ", ".join(sorted({str(f.get("ext")) for f in formats or [] if f.get("ext")}))
            tracks.append({"language": code, "kind": kind, "formats": ext,
                           "name": (formats[0].get("name") if formats else "") or code})
    return tracks, video_id, info.get("title") or ""


def download_subtitle(url: str, *, language: str = "", cookies_file: str | None = None,
                      browser: str | None = None, proxy: str | None = None,
                      workdir: str | Path | None = None) -> SubtitleTrack:
    """زیرنویس را دانلود می‌کند و مسیر فایل محلی را برمی‌گرداند."""
    yt_dlp = _youtube_dl()
    video_id = parse_video_id(url)
    target = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="trarium-yt-"))
    target.mkdir(parents=True, exist_ok=True)
    preferred = [language] if language else list(DEFAULT_LANGUAGES)
    options = _options(target, cookies_file=cookies_file, browser=browser, proxy=proxy,
                       languages=preferred)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True) or {}
    except Exception as exc:
        raise _friendly_error(exc) from exc

    code, kind = choose_track(info.get("subtitles") or {}, info.get("automatic_captions") or {},
                              preferred)
    produced = _find_caption_file(target, video_id)
    if produced is None:
        raise YouTubeSubtitleError(
            "زیرنویس دانلود شد ولی فایل خوانا پیدا نشد. یک بار دیگر تلاش کنید یا زبان دیگری انتخاب کنید.")
    return SubtitleTrack(path=produced, language=code, kind=kind,
                         title=info.get("title") or "", video_id=video_id, url=url)


def _find_caption_file(directory: Path, video_id: str) -> Path | None:
    candidates = [p for p in directory.glob(f"{video_id}*")
                  if p.suffix.lower() in {".vtt", ".srt", ".json3", ".ttml", ".srv3"}]
    if not candidates:
        candidates = [p for p in directory.iterdir()
                      if p.suffix.lower() in {".vtt", ".srt"}]
    if not candidates:
        return None
    # VTT/SRT قابل خواندن هستند؛ بقیه فرمت‌ها شانس کمتری دارند.
    candidates.sort(key=lambda p: (p.suffix.lower() not in {".vtt", ".srt"}, len(p.name)))
    return candidates[0]


def _friendly_error(exc: Exception) -> YouTubeSubtitleError:
    message = str(exc)
    lowered = message.lower()
    if "sign in" in lowered or "not a bot" in lowered or "confirm your age" in lowered:
        return YouTubeSubtitleError(
            "یوتیوب این درخواست را «ربات» تشخیص داد. یک فایل cookies.txt از مرورگر خود بدهید "
            "یا گزینهٔ «کوکی از مرورگر» را انتخاب کنید.\nجزئیات: " + message[:300])
    if "private" in lowered or "members-only" in lowered:
        return YouTubeSubtitleError("این ویدیو خصوصی یا محدود است و زیرنویسش در دسترس نیست.")
    if "no subtitles" in lowered or "there are no subtitles" in lowered:
        return YouTubeSubtitleError("این ویدیو زیرنویس ندارد.")
    return YouTubeSubtitleError(f"دانلود زیرنویس یوتیوب ناموفق بود: {message[:400]}")


# ------------------------------------------------------------------ translation
def _cue_batches(document: SubtitleFile, max_chars: int) -> list[list[int]]:
    batches: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, cue in enumerate(document.cues):
        text = (cue.text or "").strip()
        if not text:
            continue
        if current and size + len(text) + 2 > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(index)
        size += len(text) + 2
    if current:
        batches.append(current)
    return batches


def translate_subtitle_document(document: SubtitleFile, translate: Callable[[str], str],
                                *, max_chars: int = 2500,
                                progress: Callable[[int, int], None] | None = None) -> list[str]:
    """متن هر cue را ترجمه می‌کند؛ تعداد و زمان‌بندی cue‌ها تغییر نمی‌کند."""
    if document is None or not document.cues:
        raise YouTubeSubtitleError("فایل زیرنویس هیچ cue قابل ترجمه‌ای ندارد.")
    texts = [cue.text for cue in document.cues]
    batches = _cue_batches(document, max(500, int(max_chars)))
    for number, batch in enumerate(batches, 1):
        joined = "\n\n".join((document.cues[i].text or "").strip() for i in batch)
        try:
            output = str(translate(joined) or "").strip()
        except Exception:
            output = ""
        parts = [part.strip() for part in re.split(r"\n\s*\n", output) if part.strip()]
        if len(parts) == len(batch):
            for cue_index, part in zip(batch, parts):
                texts[cue_index] = part
        else:
            # اگر مدل مرز cue‌ها را نگه نداشت، cue‌به‌cue ترجمه می‌شود تا
            # زمان‌بندی هر جمله به هم نریزد.
            for cue_index in batch:
                source = (document.cues[cue_index].text or "").strip()
                try:
                    translated = str(translate(source) or "").strip()
                except Exception:
                    translated = ""
                if translated:
                    texts[cue_index] = translated
        if progress:
            progress(number, len(batches))
    return texts


def translate_subtitle_file(source: str | Path, destination: str | Path,
                            translate: Callable[[str], str], *, max_chars: int = 2500,
                            progress: Callable[[int, int], None] | None = None) -> dict:
    """یک فایل زیرنویس را ترجمه و با همان قالب/زمان‌بندی ذخیره می‌کند."""
    source, destination = Path(source), Path(destination)
    document = read_subtitles(source)
    if document is None:
        raise YouTubeSubtitleError(f"ساختار زیرنویس خوانا نبود: {source.name}")
    texts = translate_subtitle_document(document, translate, max_chars=max_chars, progress=progress)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document.render(texts), encoding="utf-8")
    changed = sum(1 for cue, text in zip(document.cues, texts)
                  if text.strip() and text.strip() != (cue.text or "").strip())
    return {"output": str(destination), "cues": len(document.cues), "translated": changed,
            "kind": document.kind}
