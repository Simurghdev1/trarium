"""ترجمهٔ بخش‌های زمان‌دار با همان ترجمه‌گرهای تراریوم (هر ارائه‌دهنده/زنجیرهٔ failover).

بخش‌ها با شمارهٔ [n] دسته‌ای فرستاده می‌شوند؛ اگر مدل شمارش را به هم زد،
همان دسته بخش‌به‌بخش ترجمه می‌شود تا زمان‌بندی هیچ‌وقت جابه‌جا نشود.
"""
from __future__ import annotations

import re

from trarium.media.langs import resolve
from trarium.media.timeline import Segment

MARK = re.compile(r"^\s*\[(\d+)\]\s*(.*)$")


def build_batches(segments: list[Segment], max_chars: int = 2500) -> list[list[int]]:
    batches, current, size = [], [], 0
    for i, seg in enumerate(segments):
        if current and size + len(seg.text) + 8 > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(i)
        size += len(seg.text) + 8
    if current:
        batches.append(current)
    return batches


def parse_numbered(output: str, expected: list[int]) -> dict[int, str] | None:
    """خروجی «[1] ...» → {1: متن}. اگر هر شماره‌ای کم بود None."""
    found: dict[int, str] = {}
    current = None
    for line in (output or "").splitlines():
        match = MARK.match(line)
        if match:
            current = int(match.group(1))
            found[current] = match.group(2).strip()
        elif current is not None and line.strip():
            found[current] = (found[current] + " " + line.strip()).strip()
    if all(found.get(n) for n in expected):
        return {n: found[n] for n in expected}
    return None


def dub_instructions(lang_name: str, for_speech: bool) -> str:
    text = (f"These are timed subtitle lines from a video, numbered [n]. Translate each into {lang_name}. "
            "Return exactly one line per input line, each starting with the same [n] marker, nothing else. "
            "Never merge, split, drop or reorder lines. Keep names and numbers.")
    if for_speech:
        text += (" The result will be spoken by a dubbing voice in the same time slot, so keep each line about "
                 "as short as the original: natural spoken register, concise, no parenthetical notes.")
    return text


def translate_segments(segments: list[Segment], translator, target: str, for_speech: bool = False,
                       log=None, progress=None) -> dict:
    """seg.translation را پر می‌کند. خروجی: آمار (تعداد درخواست، بخش‌های ناموفق)."""
    log = log or (lambda m: None)
    lang = resolve(target)
    name = lang.english if lang.english else target
    instructions = dub_instructions(name, for_speech)
    stats = {"requests": 0, "fallback_lines": 0, "failed": 0}
    batches = build_batches(segments)
    for bi, batch in enumerate(batches):
        numbers = list(range(1, len(batch) + 1))
        payload = "\n".join(f"[{n}] {segments[i].text}" for n, i in zip(numbers, batch))
        parsed = None
        try:
            stats["requests"] += 1
            parsed = parse_numbered(translator.translate(payload, lang.code, instructions), numbers)
        except Exception as exc:
            log(f"ترجمهٔ دستهٔ {bi + 1} خطا داد: {str(exc)[:160]}")
        if parsed:
            for n, i in zip(numbers, batch):
                segments[i].translation = parsed[n]
        else:
            log(f"دستهٔ {bi + 1} شماره‌ها را نگه نداشت؛ خط‌به‌خط ترجمه می‌شود.")
            for i in batch:
                try:
                    stats["requests"] += 1
                    out = translator.translate(segments[i].text, lang.code,
                                               f"Translate this subtitle line into {name}. Output only the translation.")
                    segments[i].translation = MARK.sub(r"\2", out.strip()).strip()
                    stats["fallback_lines"] += 1
                except Exception as exc:
                    stats["failed"] += 1
                    log(f"خط {i + 1} ترجمه نشد: {str(exc)[:120]}")
        if progress:
            progress((bi + 1) / len(batches))
    return stats
