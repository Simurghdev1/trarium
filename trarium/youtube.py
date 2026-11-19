"""دریافت زیرنویس اصلی یوتیوب، بدون دانلود ویدیو.

اول yt-dlp (زیرنویس دستی، بعد خودکار)، اگر نشد youtube-transcript-api.
خروجی یک فایل SRT است که بعد با همان مسیر ترجمهٔ زیرنویس تراریوم ترجمه می‌شود
و زمان‌بندی اصلی دست نمی‌خورد.

زیرنویس خودکار یوتیوب (VTT) خط‌های تکراری «غلتان» و تگ زمان کلمه‌ای دارد؛
پیش از ترجمه به یک SRT تمیز تبدیل می‌شود تا هر جمله فقط یک بار ترجمه شود.
اگر زیرنویس انگلیسی نبود، زیرنویس زبان اصلی ویدیو گرفته می‌شود.

یوتیوب IP دیتاسنترها (GitHub Actions، Railway و...) را با «Sign in to confirm
you're not a bot» می‌بندد. روی گوشی/کامپیوتر خانگی (مثلاً ترموکس) معمولاً لازم نیست،
روی سرور یکی از این‌ها لازم است:
    فایل cookies.txt (در رابط کاربری آپلود می‌شود، یا TRARIUM_YT_COOKIES)
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


VTT_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})")
WORD_TAG = re.compile(r"<\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3}>")


def _seconds(stamp: str) -> float:
    match = VTT_TIME.search(stamp)
    if not match:
        raise ValueError(stamp)
    h, m, s, ms = match.groups()
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _clean(line: str) -> str:
    line = re.sub(r"<[^>]+>", "", line)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"')):
        line = line.replace(a, b)
    return " ".join(line.split())


def vtt_to_srt(text: str) -> str:
    """WebVTT (دستی یا خودکار یوتیوب) را به SRT تمیز و بدون تکرار تبدیل می‌کند."""
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    auto = bool(WORD_TAG.search(text))
    cues: list[list] = []
    # خط به خط: یوتیوب خط‌های فقط-فاصله دارد که با جداکنندهٔ بلوک اشتباه می‌شوند.
    raw: list[tuple[str, list[str]]] = []
    for line in text.split("\n"):
        if "-->" in line:
            raw.append((line, []))
        elif raw:
            raw[-1][1].append(line)
    for timing, body in raw:
        start_s, _, end_s = timing.partition("-->")
        try:
            start, end = _seconds(start_s), _seconds(end_s.split()[0] if end_s.split() else end_s)
        except ValueError:
            continue
        # شمارهٔ کیوی بعدی (اگر بود) جزو متن نیست.
        while body and (not body[-1].strip() or body[-1].strip().isdigit()):
            body = body[:-1]
        if auto:
            # در زیرنویس خودکار، فقط خطی که تگ زمان کلمه دارد متن تازه است؛
            # بقیه تکرار خط قبلی‌اند که روی صفحه نگه داشته شده.
            fresh = [_clean(line) for line in body if WORD_TAG.search(line)]
        else:
            fresh = [_clean(line) for line in body]
        fresh = [line for line in fresh if line]
        if not fresh or end - start < 0.05:
            continue
        if cues and cues[-1][2] == fresh and start - cues[-1][1] < 0.05:
            cues[-1][1] = end
            continue
        cues.append([start, end, fresh])
    out = []
    for index, (start, end, lines) in enumerate(cues, 1):
        nxt = cues[index][0] if index < len(cues) else end
        if auto and nxt > start:
            end = nxt  # در زیرنویس خودکار هر خط تا شروع خط بعدی روی صفحه می‌ماند
        out += [str(index), f"{_ts(start)} --> {_ts(max(end, start + 0.3))}", *lines, ""]
    return "\n".join(out)


def _to_srt(path: Path) -> Path:
    if path.suffix.lower() != ".vtt":
        return path
    srt = vtt_to_srt(path.read_text(encoding="utf-8", errors="replace"))
    if not srt.strip():
        raise YouTubeError("فایل زیرنویس دریافت شد ولی متنی نداشت.")
    target = path.with_suffix(".srt")
    target.write_text(srt, encoding="utf-8")
    return target


def _pick_language(info: dict, langs: list[str]) -> list[str]:
    """اگر زبان‌های خواسته‌شده نبود، زبان اصلی ویدیو (اول دستی، بعد خودکار)."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    wanted = [lang for lang in langs if lang in manual or lang in auto]
    if wanted:
        return wanted
    original = (info.get("language") or "").split("-")[0]
    for pool in (manual, auto):
        hits = [lang for lang in pool if lang == original or lang == f"{original}-orig"]
        if hits:
            return hits[:1]
    orig_auto = [lang for lang in auto if lang.endswith("-orig")]
    if orig_auto:
        return orig_auto[:1]
    real = [lang for lang in manual if lang != "live_chat"]
    return real[:1]


def _with_yt_dlp(url: str, out_dir: Path, langs: list[str], cookies: str | None = None) -> tuple[Path, dict]:
    import yt_dlp

    base = {"quiet": True, "no_warnings": True, "noplaylist": True}
    cookiefile = cookies or os.environ.get("TRARIUM_YT_COOKIES")
    if cookiefile:
        base["cookiefile"] = cookiefile
    if os.environ.get("TRARIUM_YT_PROXY"):
        base["proxy"] = os.environ["TRARIUM_YT_PROXY"]
    with yt_dlp.YoutubeDL({**base, "skip_download": True}) as ydl:
        probe = ydl.extract_info(url, download=False)
    chosen = _pick_language(probe, langs)
    if not chosen:
        raise YouTubeError("این ویدیو هیچ زیرنویسی (دستی یا خودکار) ندارد.")
    options = {
        **base,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": chosen,
        "subtitlesformat": "vtt/srt/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
    files = sorted(out_dir.glob(f"{info.get('id')}*.vtt")) + sorted(out_dir.glob(f"{info.get('id')}*.srt"))
    if not files:
        raise YouTubeError("yt-dlp زیرنویسی پیدا نکرد.")
    # زیرنویس دستی (بدون "-orig") را به زیرنویس خودکار ترجیح بده.
    manual = set((info.get("subtitles") or probe.get("subtitles") or {}).keys())
    files.sort(key=lambda p: (p.name.split(".")[-2] not in manual, "orig" in p.name, len(p.name)))
    meta = {"id": info.get("id"), "title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
            "duration": info.get("duration"), "method": "yt-dlp", "language": files[0].name.split(".")[-2],
            "generated": files[0].name.split(".")[-2] not in manual}
    path = _to_srt(files[0])
    meta["file"] = path.name
    return path, meta


def _with_transcript_api(vid: str, out_dir: Path, langs: list[str]) -> tuple[Path, dict]:
    from youtube_transcript_api import YouTubeTranscriptApi

    kwargs = {}
    proxy = os.environ.get("TRARIUM_YT_PROXY")
    if proxy:
        from youtube_transcript_api.proxies import GenericProxyConfig
        kwargs["proxy_config"] = GenericProxyConfig(http_url=proxy, https_url=proxy)
    api = YouTubeTranscriptApi(**kwargs)
    codes = list(dict.fromkeys(lang.split("-")[0] for lang in langs))
    try:
        fetched = api.fetch(vid, languages=codes)
    except Exception as exc:  # NoTranscriptFound: زبان اصلی ویدیو را بگیر
        if "NoTranscriptFound" not in type(exc).__name__:
            raise
        listing = list(api.list(vid))
        listing.sort(key=lambda t: t.is_generated)
        if not listing:
            raise
        fetched = listing[0].fetch()
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


def fetch_subtitles(url: str, out_dir: str | Path, langs: list[str] | None = None,
                    cookies: str | None = None) -> tuple[Path, dict]:
    """زیرنویس اصلی ویدیو را می‌گیرد و (مسیر فایل، اطلاعات) برمی‌گرداند."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    langs = langs or ["en", "en-orig", "en-US", "en-GB"]
    vid = video_id(url)
    errors = []
    for method in (_with_yt_dlp, _with_transcript_api):
        try:
            if method is _with_yt_dlp:
                return method(url, out, langs, cookies) if cookies else method(url, out, langs)
            return method(vid, out, langs)
        except ImportError as exc:
            errors.append(f"{method.__name__}: کتابخانه نصب نیست ({exc.name})")
        except Exception as exc:  # noqa: BLE001 - every failure is reported together
            errors.append(f"{method.__name__}: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
    hint = ""
    if any("bot" in e.lower() or "block" in e.lower() for e in errors):
        hint = (" یوتیوب IP این سرور را مسدود کرده؛ فایل cookies.txt یوتیوب را در همین بخش آپلود کنید"
                " (یا TRARIUM_YT_COOKIES / پروکسی TRARIUM_YT_PROXY). روی سیستم خانگی معمولاً لازم نیست.")
    raise YouTubeError("زیرنویس یوتیوب دریافت نشد." + hint + " | " + " | ".join(errors))
