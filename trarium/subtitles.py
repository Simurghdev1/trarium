"""Subtitle translation for video and audio transcripts.

Trarium translates books page by page; this module does the same job for a
timed transcript. The rules are the same as for a page:

* every cue keeps its own timecode — a translation never shifts, merges away or
  drops a cue;
* the translation is faithful and complete: no summarising, no skipping a line
  because it looked repetitive;
* the Persian typography pass (:mod:`trarium.fa_style`) runs on the result, so
  the subtitles read like the rest of Trarium's output.

The pieces here are pure: parsing, cue merging and subtitle writing are
unit-tested without a network call.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass
class Cue:
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _seconds(stamp: str) -> float:
    stamp = stamp.strip().replace(",", ".")
    parts = stamp.split(":")
    try:
        values = [float(part) for part in parts]
    except ValueError:
        return 0.0
    seconds = 0.0
    for value in values:
        seconds = seconds * 60 + value
    return round(seconds, 3)


def _stamp(seconds: float, comma: bool = True) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole = int(seconds % 60)
    millis = int(round((seconds - math.floor(seconds)) * 1000))
    if millis == 1000:
        millis, whole = 0, whole + 1
    return f"{hours:02d}:{minutes:02d}:{whole:02d}{',' if comma else '.'}{millis:03d}"


def parse_vtt(text: str) -> list[Cue]:
    """Read a WebVTT file (or a YouTube VTT with inline timestamps)."""
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", str(text or "").replace("\r\n", "\n"))
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE")):
            continue
        timing = next((line for line in lines if "-->" in line), None)
        if not timing:
            continue
        left, _, right = timing.partition("-->")
        start = _seconds(left.split()[0])
        end = _seconds(right.split()[0])
        body = " ".join(line for line in lines if "-->" not in line)
        body = re.sub(r"<[^>]+>", "", body)
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            cues.append(Cue(start, end, body))
    return cues


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", str(text or "").replace("\r\n", "\n")):
        lines = [line for line in block.split("\n") if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        left, _, right = lines[timing_index].partition("-->")
        body = " ".join(lines[timing_index + 1:]).strip()
        if body:
            cues.append(Cue(_seconds(left.split()[0]), _seconds(right.split()[0]), body))
    return cues


def parse_segments(items) -> list[Cue]:
    """A list of ``{s|start, d|duration|end, t|text}`` dicts, as scraped or fetched."""
    cues = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("t") or item.get("text") or "").strip()
        if not text:
            continue
        start = float(item.get("s", item.get("start", 0)) or 0)
        if item.get("end") is not None:
            end = float(item["end"])
        else:
            end = start + float(item.get("d", item.get("duration", 2.0)) or 2.0)
        cues.append(Cue(start, end, re.sub(r"\s+", " ", text)))
    cues.sort(key=lambda cue: cue.start)
    return cues


def fix_overlaps(cues: list[Cue], gap: float = 0.04, min_seconds: float = 0.4) -> list[Cue]:
    """Trim a cue that runs into the next one.

    Automatic captions overlap: a cue keeps showing while the next one starts,
    so two subtitles sit on screen at once. Each cue is cut just before the next
    one begins, and never below :attr:`min_seconds`.
    """
    fixed: list[Cue] = []
    for index, cue in enumerate(cues):
        end = cue.end
        if index + 1 < len(cues):
            limit = cues[index + 1].start - gap
            if end > limit:
                end = max(cue.start + min_seconds, limit)
        fixed.append(Cue(cue.start, max(end, cue.start + min_seconds), cue.text))
    return fixed


def merge_cues(cues: list[Cue], max_chars: int = 84, max_seconds: float = 6.0,
               gap: float = 1.2) -> list[Cue]:
    """Join the short pieces of an automatic transcript into readable cues.

    Automatic captions arrive two or three words at a time. Translating them one
    by one produces a subtitle that changes five times a second, so neighbouring
    pieces are joined until the line is long enough to read — never across a long
    silence, and never past :attr:`max_seconds`.
    """
    merged: list[Cue] = []
    for cue in cues:
        if merged:
            last = merged[-1]
            candidate = f"{last.text} {cue.text}".strip()
            if (len(candidate) <= max_chars
                    and cue.start - last.end <= gap + 0.8
                    and cue.end - last.start <= max_seconds
                    and not re.search(r"[.!?…]$", last.text)):
                last.text = candidate
                last.end = cue.end
                continue
        merged.append(Cue(cue.start, cue.end, cue.text))
    return fix_overlaps(merged)


def to_srt(cues: list[Cue]) -> str:
    blocks = []
    for index, cue in enumerate(cues, 1):
        blocks.append(f"{index}\n{_stamp(cue.start)} --> {_stamp(cue.end)}\n{cue.text}\n")
    return "\n".join(blocks)


def to_vtt(cues: list[Cue], header: str = "WEBVTT") -> str:
    blocks = [header]
    for cue in cues:
        blocks.append(f"{_stamp(cue.start, comma=False)} --> {_stamp(cue.end, comma=False)}\n{cue.text}\n")
    return "\n".join(blocks)


def to_bilingual(cues: list[Cue], translated: list[str]) -> str:
    blocks = []
    for index, (cue, text) in enumerate(zip(cues, translated), 1):
        blocks.append(f"{index}\n{_stamp(cue.start)} --> {_stamp(cue.end)}\n{text}\n{cue.text}\n")
    return "\n".join(blocks)


BATCH_PROMPT = "Translate these subtitle lines to Persian."
LINE_HINT = (
    "Keep the number of every line. Answer with exactly {count} lines, each starting "
    "with its number and a dot, like '12. ...'. Translate every line completely and "
    "naturally for subtitles; never merge, split, skip or reorder lines."
)


def _numbered(lines: list[str]) -> str:
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, 1))


def parse_numbered(text: str, count: int) -> list[str | None]:
    """Read the model's numbered answer back into a list of translations."""
    result: list[str | None] = [None] * count
    for raw in str(text or "").split("\n"):
        match = re.match(r"\s*\[?(\d{1,3})\]?\s*[\.\)\-–:]\s*(.+)$", raw.strip())
        if not match:
            continue
        index = int(match.group(1)) - 1
        if 0 <= index < count:
            value = match.group(2).strip().strip("«»\"'")
            result[index] = value or None
    return result


def batches(cues: list[Cue], max_chars: int = 1400, max_lines: int = 40) -> list[list[int]]:
    """Group cue indices so one request stays small and keeps the order."""
    groups: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, cue in enumerate(cues):
        length = len(cue.text) + 8
        if current and (size + length > max_chars or len(current) >= max_lines):
            groups.append(current)
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        groups.append(current)
    return groups


def translate_cues(cues: list[Cue], translator, target_lang: str = "fa",
                   max_chars: int = 1400, max_lines: int = 40,
                   progress_callback=None, retries: int = 2) -> list[str]:
    """Translate every cue, keeping the one-to-one mapping with the timings.

    A batch that comes back with a missing line is retried line by line, so a
    single bad answer can never leave a cue untranslated.
    """
    from trarium.fa_style import normalize

    translated: list[str] = [""] * len(cues)
    groups = batches(cues, max_chars, max_lines)
    for group_number, group in enumerate(groups, 1):
        prompt = f"{BATCH_PROMPT}\n{LINE_HINT.format(count=len(group))}\n\n" + _numbered([cues[i].text for i in group])
        answers: list[str | None] = [None] * len(group)
        for attempt in range(retries + 1):
            try:
                out = translator.translate(prompt, target_lang)
                answers = parse_numbered(out, len(group))
            except Exception:
                answers = [None] * len(group)
            if all(answers):
                break
        for position, cue_index in enumerate(group):
            if answers[position]:
                translated[cue_index] = normalize(answers[position] or "")
        # Anything the batch missed is asked for on its own.
        for position, cue_index in enumerate(group):
            if translated[cue_index]:
                continue
            for _ in range(retries + 1):
                try:
                    single = translator.translate(
                        f"{BATCH_PROMPT}\nAnswer with the translation only.\n\n{cues[cue_index].text}",
                        target_lang,
                    )
                    if single and single.strip():
                        translated[cue_index] = normalize(single.strip())
                        break
                except Exception:
                    continue
        if progress_callback:
            progress_callback(group_number / max(len(groups), 1), f"زیرنویس {group_number}/{len(groups)}")
    return translated
