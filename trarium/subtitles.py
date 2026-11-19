"""خواندن و نوشتن زیرنویس با حفظ زمان‌بندی.

خروجی TXT/PDF/EPUB برای فایل زیرنویس بی‌فایده است، چون زمان‌بندی را از بین
می‌برد. این ماژول ساختار اصلی فایل را نگه می‌دارد و فقط متن هر cue را عوض
می‌کند: SRT، WebVTT، SBV، LRC، ASS/SSA و MicroDVD.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from trarium.extractors.generic import decode_text
from trarium.utils import ascii_digits

SUBTITLE_OUTPUT_EXTENSIONS = {".srt", ".vtt", ".sbv", ".lrc", ".ass", ".ssa", ".sub"}


@dataclass
class Cue:
    """یک واحد زیرنویس: متن + هر چیزی که باید دست‌نخورده بماند."""

    text: str
    payload: dict = field(default_factory=dict)


@dataclass
class SubtitleFile:
    kind: str
    cues: list[Cue]
    head: str = ""

    def render(self, texts: list[str]) -> str:
        """متن‌های ترجمه‌شده را با همان زمان‌بندی و ترتیب برمی‌گرداند."""
        # Translated prose often turns 10 into ۱۰; subtitle numbers/timings stay ASCII.
        values = [ascii_digits(str(value)) for value in texts]
        values += [""] * max(0, len(self.cues) - len(values))
        if self.kind in {"srt", "vtt", "sbv"}:
            return self._render_blocks(values)
        if self.kind == "lrc":
            lines = [
                f"{cue.payload['stamp']}{values[i].replace(chr(10), ' ').strip()}"
                for i, cue in enumerate(self.cues)
            ]
            return self.head + "\n".join(lines) + "\n"
        if self.kind in {"ass", "ssa"}:
            lines = []
            for i, cue in enumerate(self.cues):
                body = values[i].strip().replace("\n", "\\N")
                lines.append(cue.payload["prefix"] + body)
            return self.head + "\n".join(lines) + "\n"
        if self.kind == "sub":
            lines = []
            for i, cue in enumerate(self.cues):
                body = values[i].strip().replace("\n", "|")
                lines.append(f"{{{cue.payload['start']}}}{{{cue.payload['end']}}}{body}")
            return "\n".join(lines) + "\n"
        raise ValueError(f"فرمت زیرنویس پشتیبانی نمی‌شود: {self.kind}")

    def _render_blocks(self, values: list[str]) -> str:
        blocks = []
        for i, cue in enumerate(self.cues):
            body = values[i].strip()
            lines = []
            if cue.payload.get("index"):
                lines.append(cue.payload["index"])
            if cue.payload.get("timing"):
                lines.append(cue.payload["timing"])
            if body:
                lines.extend(body.splitlines())
            blocks.append("\n".join(lines))
        joiner = "\n\n"
        text = joiner.join(blocks) + "\n"
        head = self.head.strip()
        return (head + joiner + text) if head else text


def _strip_markup(text: str) -> str:
    text = re.sub(r"(?is)</?(?:i|b|u|s|em|strong|font|c|v|ruby|rt)\b[^>]*>", "", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _decode(path: Path) -> str:
    return decode_text(path.read_bytes()).replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")


def _read_blocks(text: str, kind: str) -> tuple[str, list[Cue]]:
    head_lines: list[str] = []
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.rstrip() for line in block.splitlines()]
        lines = [line for line in lines if line.strip()]
        if not lines:
            continue
        if all(line.upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION", "KIND:", "LANGUAGE:")) for line in lines) or (
            kind == "vtt" and not cues and not any("-->" in line for line in lines)
        ):
            head_lines.extend(lines)
            continue
        index = None
        timing = ""
        if "-->" in lines[0]:
            timing = lines[0].strip()
            body = lines[1:] if len(lines) > 1 else []
        elif len(lines) > 1 and "-->" in lines[1]:
            index = lines[0].strip()
            timing = lines[1].strip()
            body = lines[2:]
        else:
            # بلوک SBV بدون شناسه که فقط زمان‌بندی دارد.
            timing = lines[0].strip() if re.search(r"\d", lines[0]) and "-->" in lines[0] else ""
            body = lines[1:] if timing else lines
            if not timing:
                continue
        cues.append(Cue(text=_strip_markup("\n".join(body)), payload={"index": index, "timing": timing}))
    return ("\n".join(head_lines) + "\n") if head_lines else "", cues


def _read_ass(text: str) -> tuple[str, list[Cue]]:
    head: list[str] = []
    cues: list[Cue] = []
    for line in text.splitlines():
        if line.lower().startswith("dialogue:"):
            fields = line.split(",", 9)
            if len(fields) < 10:
                continue
            body = fields[9].replace("\\N", "\n").replace("\\n", "\n")
            prefix = ",".join(fields[:9]) + ","
            cues.append(Cue(text=_strip_markup(body), payload={"prefix": prefix}))
        else:
            head.append(line)
    return "\n".join(head) + "\n", cues


def _read_lrc(text: str) -> tuple[str, list[Cue]]:
    cues: list[Cue] = []
    for line in text.splitlines():
        match = re.match(r"^((?:\[[^\]]*\])+)(.*)$", line)
        if not match:
            continue
        body = match.group(2).strip()
        cues.append(Cue(text=_strip_markup(body), payload={"stamp": match.group(1)}))
    return "", cues


def _read_microdvd(text: str) -> tuple[str, list[Cue]]:
    cues: list[Cue] = []
    for line in text.splitlines():
        match = re.match(r"^\{(\d+)\}\{(\d+)\}(.*)$", line.strip())
        if not match:
            continue
        body = match.group(3).replace("|", "\n")
        cues.append(Cue(text=_strip_markup(body), payload={"start": match.group(1), "end": match.group(2)}))
    return "", cues


def read_subtitles(path: str | Path) -> SubtitleFile | None:
    """زیرنویس را می‌خواند؛ اگر ساختار قابل نگه‌داری نبود None برمی‌گرداند."""
    path = Path(path)
    ext = path.suffix.lower()
    text = _decode(path)
    if ext in {".srt", ".vtt", ".sbv"}:
        head, cues = _read_blocks(text, ext.lstrip("."))
        if not cues:
            return None
        return SubtitleFile(kind=ext.lstrip("."), cues=cues, head=head)
    if ext in {".ass", ".ssa"}:
        head, cues = _read_ass(text)
        return SubtitleFile(kind=ext.lstrip("."), cues=cues, head=head) if cues else None
    if ext == ".lrc":
        head, cues = _read_lrc(text)
        return SubtitleFile(kind="lrc", cues=cues, head=head) if cues else None
    if ext == ".sub":
        head, cues = _read_microdvd(text)
        return SubtitleFile(kind="sub", cues=cues, head=head) if cues else None
    return None
