"""ورودی تب‌های رسانه: لینک یوتیوب (دانلود با yt-dlp) یا فایل آپلودشده."""
from __future__ import annotations

import os
import re
from pathlib import Path

from trarium.media.ffmpeg_tools import MediaError

URL_RE = re.compile(r"^https?://", re.I)


def is_url(value: str) -> bool:
    return bool(URL_RE.match((value or "").strip()))


def download(url: str, out_dir: str | Path, audio_only: bool = False, max_height: int = 720,
             cookies: str | None = None, progress=None) -> Path:
    """ویدیو (یا فقط صدا) را با yt-dlp می‌گیرد. کوکی/پروکسی مثل تب زیرنویس یوتیوب."""
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise MediaError("yt-dlp نصب نیست (pip install yt-dlp).") from exc
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = "bestaudio/best" if audio_only else (
        f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/bv*[height<={max_height}]+ba/b[height<={max_height}]/b")
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "format": fmt,
        "outtmpl": str(out_dir / "source.%(ext)s"), "merge_output_format": None if audio_only else "mp4",
        "max_filesize": int(os.environ.get("TRARIUM_MEDIA_MAX_BYTES", 2 * 1024 ** 3)),
    }
    cookiefile = cookies or os.environ.get("TRARIUM_YT_COOKIES")
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if os.environ.get("TRARIUM_YT_PROXY"):
        opts["proxy"] = os.environ["TRARIUM_YT_PROXY"]
    if progress:
        def hook(d):
            if d.get("status") == "downloading" and d.get("total_bytes"):
                progress(d.get("downloaded_bytes", 0) / d["total_bytes"])
        opts["progress_hooks"] = [hook]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
    except Exception as exc:
        text = str(exc)
        if "not a bot" in text or "Sign in" in text:
            raise MediaError("یوتیوب این سرور را بست (not a bot). cookies.txt بدهید یا TRARIUM_YT_PROXY را تنظیم کنید.") from exc
        raise MediaError(f"دانلود ناموفق بود: {text[:300]}") from exc
    if not path.exists():
        found = sorted(out_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
        if not found:
            raise MediaError("فایل دانلودشده پیدا نشد.")
        path = found[0]
    return path


def save_upload(data: bytes, name: str, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(name or "input").suffix.lower() or ".bin"
    path = out_dir / f"source{suffix}"
    path.write_bytes(data)
    return path
