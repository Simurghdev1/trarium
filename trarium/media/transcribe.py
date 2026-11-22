"""گفتار → بخش‌های زمان‌دار.

۱) Gemini (درک صوت، خروجی JSON با زمان شروع/پایان)
۲) اگر Gemini نبود یا خطا داد: faster-whisper روی CPU (اختیاری، pip install faster-whisper)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from trarium.media.ffmpeg_tools import extract_audio, probe
from trarium.media.gemini_client import GeminiClient, GeminiError, response_text
from trarium.media.timeline import Segment, normalize, parse_time, split_long

TRANSCRIBE_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-flash-latest"]

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "language": {"type": "STRING"},
        "segments": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"},
            "speaker": {"type": "STRING"}, "text": {"type": "STRING"}},
            "required": ["start", "end", "text"]}},
    },
    "required": ["language", "segments"],
}

PROMPT = (
    "Transcribe every spoken word of this audio verbatim in the language actually spoken (do not translate). "
    "Split into subtitle-sized segments of one sentence or clause, at most ~7 seconds and ~80 characters each. "
    "For each segment give start and end as seconds from the beginning of the file (decimals allowed, "
    "accurate to ~0.2 s), a short speaker label (A, B, ...) and the text. Skip music and silence. "
    "Also return the ISO-639-1 code of the spoken language. The audio is {duration:.1f} seconds long; "
    "no timestamp may exceed it."
)


def parse_gemini_segments(raw: str, total: float | None = None) -> tuple[str, list[Segment]]:
    """JSON مدل (گاهی داخل ```json) → (زبان، بخش‌ها). زمان‌های MM:SS هم پذیرفته می‌شود."""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    data = json.loads(text)
    if isinstance(data, list):
        data = {"language": "", "segments": data}
    segments = []
    for item in data.get("segments") or []:
        try:
            start, end = parse_time(item.get("start", 0)), parse_time(item.get("end", 0))
        except ValueError:
            continue
        body = " ".join(str(item.get("text") or "").split())
        if body:
            segments.append(Segment(start, max(end, start), body, speaker=str(item.get("speaker") or "")))
    return str(data.get("language") or ""), normalize(segments, total)


def with_gemini(audio_src: str | Path, client: GeminiClient, work: Path, models: list[str] | None = None,
                log=None) -> tuple[str, list[Segment], dict]:
    log = log or (lambda m: None)
    total = probe(audio_src)["duration"]
    ogg = extract_audio(audio_src, work / "transcribe.ogg", rate=16000)
    part = client.audio_part(ogg, "audio/ogg")
    last = None
    for model in models or TRANSCRIBE_MODELS:
        try:
            data = client.generate(model, [{"parts": [part, {"text": PROMPT.format(duration=total)}]}],
                                   config={"responseMimeType": "application/json", "responseSchema": SCHEMA,
                                           "temperature": 0.0})
            lang, segs = parse_gemini_segments(response_text(data), total)
            if not segs:
                raise GeminiError("Gemini متنی از صدا برنگرداند.")
            return lang, segs, {"engine": "gemini", "model": model}
        except GeminiError as exc:
            last = exc
            if "HTTP 404" in str(exc) or "HTTP 400" in str(exc):
                log(f"مدل {model} در دسترس نبود؛ مدل بعدی.")
                continue
            raise
        except (ValueError, KeyError) as exc:
            last = exc
            log(f"خروجی {model} قابل خواندن نبود؛ مدل بعدی.")
    raise GeminiError(f"رونویسی با Gemini ناموفق بود: {last}")


def with_whisper(audio_src: str | Path, model_size: str = "small", language: str | None = None,
                 log=None) -> tuple[str, list[Segment], dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper نصب نیست (pip install faster-whisper).") from exc
    from trarium.media.ffmpeg_tools import read_mono

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    # صدا با ffmpeg خود سیستم خوانده می‌شود (به نسخهٔ PyAV وابسته نیست).
    audio = read_mono(audio_src, 16000)
    pieces, info = model.transcribe(audio, language=language, vad_filter=True)
    segments = [Segment(p.start, p.end, p.text.strip()) for p in pieces if p.text.strip()]
    return info.language, normalize(segments, probe(audio_src)["duration"]), {"engine": "faster-whisper", "model": model_size}


def transcribe(audio_src: str | Path, work: Path, client: GeminiClient | None = None, engine: str = "auto",
               whisper_model: str = "small", log=None) -> tuple[str, list[Segment], dict]:
    """engine: auto | gemini | whisper. خروجی همیشه برای زیرنویس شکسته و مرتب است.

    auto: اگر faster-whisper نصب باشد اول آن (زمان‌بندی دقیق‌تر و چیزی جا نمی‌اندازد)، بعد Gemini.
    """
    log = log or (lambda m: None)
    work.mkdir(parents=True, exist_ok=True)
    errors = []
    if engine == "auto" and _has_whisper():
        try:
            lang, segs, meta = with_whisper(audio_src, whisper_model, log=log)
            if segs:
                return lang, split_long(segs), meta
        except Exception as exc:  # noqa: BLE001
            errors.append(f"whisper: {exc}")
            log(f"faster-whisper رونویسی نکرد ({str(exc)[:120]}); Gemini امتحان می‌شود.")
    if engine in ("auto", "gemini") and client is not None:
        try:
            lang, segs, meta = with_gemini(audio_src, client, work, log=log)
            return lang, split_long(segs), meta
        except Exception as exc:
            errors.append(f"Gemini: {exc}")
            if engine == "gemini":
                raise
            log(f"Gemini رونویسی نکرد ({str(exc)[:120]}); faster-whisper امتحان می‌شود.")
    if engine == "whisper" or (engine == "auto" and not _has_whisper()):
        try:
            lang, segs, meta = with_whisper(audio_src, whisper_model, log=log)
            return lang, split_long(segs), meta
        except Exception as exc:
            errors.append(f"whisper: {exc}")
    raise RuntimeError("رونویسی انجام نشد. " + " | ".join(errors))


def _has_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False
