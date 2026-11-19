"""دریافت زیرنویس اصلی یوتیوب، بدون دانلود ویدیو.

اول yt-dlp (زیرنویس دستی، بعد خودکار)، اگر نشد youtube-transcript-api.
خروجی یک فایل SRT است که بعد با همان مسیر ترجمهٔ زیرنویس تراریوم ترجمه می‌شود
و زمان‌بندی اصلی دست نمی‌خورد.

متغیرهای محیطی اختیاری (وقتی یوتیوب IP سرور را مسدود کرده):
    TRARIUM_YT_COOKIES  مسیر فایل cookies.txt
    TRARIUM_YT_PROXY    پروکسی، مثل http://user:pass@host:port
"""
from __future__ import annotations

import os
import re
from pathlib import Path

VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")


class YouTubeError(RuntimeError):
    pass


def video_id(url: str) -> str:
    match = VIDEO_ID.search(url or "")
    if match:
        return match.group(1)
    if re.fullmatch(r"[\w-]{11}", (url or "").strip()):
        return url.strip()
    raise YouTubeError("لینک یوتیوب معتبر نیست.")


def _ts(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _with_yt_dlp(url: str, out_dir: Path, langs: list[str]) -> tuple[Path, dict]:
    import yt_dlp

    options = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": langs,
        "subtitlesformat": "vtt/srt/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if os.environ.get("TRARIUM_YT_COOKIES"):
        options["cookiefile"] = os.environ["TRARIUM_YT_COOKIES"]
    if os.environ.get("TRARIUM_YT_PROXY"):
        options["proxy"] = os.environ["TRARIUM_YT_PROXY"]
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted(out_dir.glob(f"{info.get('id')}*.vtt")) + sorted(out_dir.glob(f"{info.get('id')}*.srt"))
    if not files:
        raise YouTubeError("yt-dlp زیرنویسی پیدا نکرد.")
    # زیرنویس دستی (بدون "-orig") را به زیرنویس خودکار ترجیح بده.
    files.sort(key=lambda p: ("orig" in p.name, len(p.name)))
    meta = {"id": info.get("id"), "title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
            "duration": info.get("duration"), "method": "yt-dlp", "file": files[0].name}
    return files[0], meta


def _with_transcript_api(vid: str, out_dir: Path, langs: list[str]) -> tuple[Path, dict]:
    from youtube_transcript_api import YouTubeTranscriptApi

    kwargs = {}
    proxy = os.environ.get("TRARIUM_YT_PROXY")
    if proxy:
        from youtube_transcript_api.proxies import GenericProxyConfig
        kwargs["proxy_config"] = GenericProxyConfig(http_url=proxy, https_url=proxy)
    api = YouTubeTranscriptApi(**kwargs)
    fetched = api.fetch(vid, languages=[lang.split("-")[0] if lang != "en-orig" else "en" for lang in langs])
    lines = []
    for index, snippet in enumerate(fetched.snippets, 1):
        text = " ".join(snippet.text.split())
        if not text:
            continue
        lines += [str(index), f"{_ts(snippet.start)} --> {_ts(snippet.start + snippet.duration)}", text, ""]
    if not lines:
        raise YouTubeError("رونوشت این ویدیو خالی است.")
    path = out_dir / f"{vid}.{fetched.language_code}.srt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path, {"id": vid, "method": "youtube-transcript-api", "language": fetched.language_code,
                  "generated": fetched.is_generated, "file": path.name}


def fetch_subtitles(url: str, out_dir: str | Path, langs: list[str] | None = None) -> tuple[Path, dict]:
    """زیرنویس اصلی ویدیو را می‌گیرد و (مسیر فایل، اطلاعات) برمی‌گرداند."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    langs = langs or ["en", "en-orig", "en-US", "en-GB"]
    vid = video_id(url)
    errors = []
    for method in (_with_yt_dlp, _with_transcript_api):
        try:
            target = url if method is _with_yt_dlp else vid
            return method(target, out, langs)
        except ImportError as exc:
            errors.append(f"{method.__name__}: کتابخانه نصب نیست ({exc.name})")
        except Exception as exc:  # noqa: BLE001 - every failure is reported together
            errors.append(f"{method.__name__}: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
    hint = ""
    if any("bot" in e.lower() or "block" in e.lower() for e in errors):
        hint = (" یوتیوب IP این سرور را مسدود کرده؛ فایل کوکی (TRARIUM_YT_COOKIES)"
                " یا پروکسی (TRARIUM_YT_PROXY) بدهید.")
    raise YouTubeError("زیرنویس یوتیوب دریافت نشد." + hint + " | " + " | ".join(errors))
