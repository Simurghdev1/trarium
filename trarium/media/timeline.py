"""بخش‌های زمان‌دار گفتار: ساخت SRT/VTT، شکستن خط‌های بلند و برنامهٔ جاگذاری دوبله.

همهٔ تابع‌های این فایل خالص‌اند (بدون شبکه و ffmpeg) تا تست واحد داشته باشند.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

RLM = "\u200f"


@dataclass
class Segment:
    start: float
    end: float
    text: str
    translation: str = ""
    speaker: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


# --------------------------------------------------------------------- time
_TS = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)\s*$")


def parse_time(value) -> float:
    """12.5 / "12.5" / "01:02.300" / "00:01:02,300" → ثانیه."""
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    match = _TS.match(str(value or ""))
    if not match:
        raise ValueError(f"زمان نامعتبر: {value!r}")
    a, b, sec = match.groups()
    seconds = float(sec.replace(",", "."))
    if a is not None and b is not None:
        return int(a) * 3600 + int(b) * 60 + seconds
    if a is not None:
        return int(a) * 60 + seconds
    return seconds


def fmt_time(seconds: float, sep: str = ",") -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02}{sep}{ms:03}"


# --------------------------------------------------------------- normalise
def normalize(segments: list[Segment], total: float | None = None,
              min_duration: float = 0.4) -> list[Segment]:
    """مرتب، بدون همپوشانی، بدون بخش خالی و داخل طول فایل."""
    items = sorted((s for s in segments if (s.text or s.translation).strip()), key=lambda s: s.start)
    out: list[Segment] = []
    for seg in items:
        start, end = max(0.0, seg.start), max(seg.end, seg.start)
        if total:
            start, end = min(start, total), min(end, total)
        if out and start < out[-1].end:
            start = out[-1].end
        if end - start < min_duration:
            end = start + min_duration
            if total and end > total:
                end = total
        if end <= start:
            continue
        out.append(replace(seg, start=round(start, 3), end=round(end, 3)))
    return out


def _wrap(words: list[str], max_chars: int) -> list[str]:
    lines, line = [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > max_chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines


def split_long(segments: list[Segment], max_chars: int = 84, max_seconds: float = 7.0) -> list[Segment]:
    """بخش‌های خیلی بلند را به چند زیرنویس خواناتر می‌شکند (زمان به نسبت طول متن).

    فقط روی متن اصلی کار می‌کند؛ پیش از ترجمه صدا زده شود.
    """
    out: list[Segment] = []
    for seg in segments:
        text = " ".join(seg.text.split())
        if len(text) <= max_chars and seg.duration <= max_seconds:
            out.append(replace(seg, text=text))
            continue
        pieces = max(2, -(-len(text) // max_chars), int(-(-seg.duration // max_seconds)))
        # شکستن روی مرز جمله/ویرگول اگر بشود، وگرنه روی کلمه.
        sentences = [p for p in re.split(r"(?<=[.!?؟،,;:])\s+", text) if p]
        chunks: list[str] = []
        target = len(text) / pieces
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) + 1 > max(target, 1) * 1.25:
                chunks.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            chunks.append(current)
        final: list[str] = []
        for chunk in chunks:
            if len(chunk) > max_chars:
                final.extend(_wrap(chunk.split(), max(20, int(target))))
            else:
                final.append(chunk)
        total_chars = sum(len(c) for c in final) or 1
        cursor = seg.start
        for i, chunk in enumerate(final):
            end = seg.end if i == len(final) - 1 else cursor + seg.duration * len(chunk) / total_chars
            out.append(Segment(round(cursor, 3), round(end, 3), chunk, speaker=seg.speaker))
            cursor = end
    return out


def wrap_lines(text: str, max_line: int = 42) -> str:
    """حداکثر دو خط متوازن برای نمایش (شکستن روی فاصله‌ای که به وسط نزدیک‌تر است)."""
    text = " ".join((text or "").split())
    if len(text) <= max_line:
        return text
    spaces = [i for i, ch in enumerate(text) if ch == " "]
    if not spaces:
        return text
    middle = len(text) / 2
    # ترجیح شکستن بعد از ویرگول/نقطه اگر نزدیک وسط بود.
    def cost(i: int) -> float:
        bonus = -6 if text[i - 1] in ",،.;:!?؟" else 0
        return abs(i - middle) + bonus
    cut = min(spaces, key=cost)
    return text[:cut] + "\n" + text[cut + 1:]


# ------------------------------------------------------------------ render
def _cue_text(seg: Segment, mode: str, rtl: bool) -> str:
    original = wrap_lines(seg.text)
    translated = wrap_lines(seg.translation)
    if rtl and translated:
        translated = "\n".join(f"{RLM}{line}{RLM}" for line in translated.split("\n"))
    if mode == "original":
        return original
    if mode == "translation":
        return translated or original
    if mode == "bilingual":
        return "\n".join(x for x in (translated, original) if x)
    raise ValueError(mode)


def to_srt(segments: list[Segment], mode: str = "translation", rtl: bool = False) -> str:
    blocks = []
    for i, seg in enumerate(segments, 1):
        blocks.append(f"{i}\n{fmt_time(seg.start)} --> {fmt_time(seg.end)}\n{_cue_text(seg, mode, rtl)}")
    return "\n\n".join(blocks) + "\n"


def to_vtt(segments: list[Segment], mode: str = "translation", rtl: bool = False) -> str:
    blocks = ["WEBVTT"]
    for seg in segments:
        blocks.append(f"{fmt_time(seg.start, '.')} --> {fmt_time(seg.end, '.')}\n{_cue_text(seg, mode, rtl)}")
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------- dub plan
@dataclass
class Placement:
    index: int
    at: float          # ثانیه در خروجی
    tempo: float       # ضریب سرعت (۱ = بدون تغییر)
    length: float      # طول بعد از تغییر سرعت


def plan_placements(segments: list[Segment], clip_lengths: list[float],
                    max_tempo: float = 1.35, total: float | None = None) -> list[Placement]:
    """هر کلیپ TTS کجا و با چه سرعتی بنشیند.

    جای هر بخش از شروعش تا شروع بخش بعدی است (فاصلهٔ سکوت هم قابل استفاده است).
    اگر کلیپ بلندتر بود تا ``max_tempo`` تند می‌شود؛ اگر باز هم جا نشد، بخش بعدی
    کمی دیرتر شروع می‌شود (هیچ‌وقت دو صدا روی هم نمی‌افتند).
    """
    out: list[Placement] = []
    cursor = 0.0
    for i, (seg, length) in enumerate(zip(segments, clip_lengths)):
        at = max(seg.start, cursor)
        if i + 1 < len(segments):
            slot_end = segments[i + 1].start
        else:
            slot_end = max(seg.end, total or seg.end)
        slot = max(0.05, slot_end - at)
        tempo = 1.0
        if length > slot:
            tempo = min(max_tempo, length / slot)
        new_len = length / tempo
        out.append(Placement(i, round(at, 3), round(tempo, 4), round(new_len, 3)))
        cursor = at + new_len + 0.05
    return out


def atempo_chain(tempo: float) -> str:
    """atempo فقط ۰٫۵ تا ۲ را می‌پذیرد؛ بیرون از آن زنجیر می‌شود."""
    tempo = float(tempo)
    if tempo <= 0:
        raise ValueError("tempo must be > 0")
    parts = []
    while tempo > 2.0:
        parts.append("atempo=2.0")
        tempo /= 2.0
    while tempo < 0.5:
        parts.append("atempo=0.5")
        tempo /= 0.5
    parts.append(f"atempo={tempo:.4f}")
    return ",".join(parts)
