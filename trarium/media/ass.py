"""ساخت فایل ASS برای زیرنویس حک‌شده (فونت فارسی، اندازه، جایگاه، راست‌به‌چپ)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trarium.media.timeline import RLM, Segment, wrap_lines

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
FONT_FAMILIES = {"Vazirmatn": "Vazirmatn-Bold.ttf", "Sahel": "Sahel.ttf"}
POSITIONS = {"bottom": 2, "top": 8, "middle": 5}


@dataclass
class AssStyle:
    font: str = "Vazirmatn"
    size_pct: float = 5.5          # اندازهٔ فونت به درصد ارتفاع تصویر
    position: str = "bottom"
    margin_pct: float = 6.0        # فاصله از لبه به درصد ارتفاع
    primary: str = "&H00FFFFFF"
    outline_color: str = "&H00000000"
    outline: float = 2.6
    shadow: float = 0.8
    box: bool = False              # کادر نیمه‌شفاف پشت متن


def _ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def build_ass(segments: list[Segment], width: int, height: int, style: AssStyle | None = None,
              mode: str = "translation", rtl: bool = False) -> str:
    """mode: translation | original | bilingual (ترجمه بالا، اصل کوچک‌تر پایین)."""
    style = style or AssStyle()
    width, height = max(width, 16), max(height, 16)
    size = max(10, round(height * style.size_pct / 100))
    small = max(10, round(size * 0.72))
    margin = round(height * style.margin_pct / 100)
    align = POSITIONS.get(style.position, 2)
    border_style = 3 if style.box else 1
    back = "&H80000000" if style.box else "&H64000000"
    max_line = max(20, int(width / (size * 0.55)))
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\nYCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Main,{style.font},{size},{style.primary},&H000000FF,{style.outline_color},{back},0,0,0,0,100,100,0,0,"
        f"{border_style},{style.outline},{style.shadow},{align},{margin},{margin},{margin},1\n"
        f"Style: Orig,{style.font},{small},&H00D8D8D8,&H000000FF,{style.outline_color},{back},0,0,0,0,100,100,0,0,"
        f"{border_style},{style.outline},{style.shadow},{align},{margin},{margin},{margin},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for seg in segments:
        main = seg.translation if mode in {"translation", "bilingual"} and seg.translation else seg.text
        main_rtl = rtl and mode != "original" and bool(seg.translation)
        wrapped = wrap_lines(main, max_line)
        if main_rtl:
            # RLM در دو سر هر خط تا علامت‌ها و کلمات لاتین در جای درست راست‌به‌چپ بنشینند.
            wrapped = "\n".join(f"{RLM}{line}{RLM}" for line in wrapped.split("\n"))
        text = _escape(wrapped)
        if mode == "bilingual" and seg.translation and seg.text:
            orig = _escape(wrap_lines(seg.text, int(max_line * 1.3)))
            text = f"{text}\\N{{\\rOrig}}{orig}"
        lines.append(f"Dialogue: 0,{_ass_time(seg.start)},{_ass_time(seg.end)},Main,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"
