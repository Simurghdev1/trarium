"""Final quality check: compare a translated PDF with its source.

The check is deliberately measurable — it never guesses "looks good". It reports:

* page count and page sizes (mismatched geometry),
* blank pages (no text and no images),
* text left in a script that was supposed to be translated (CJK/Japanese),
* duplicated blocks (the same translation written twice),
* text that overflows the page or overlaps another text block,
* the spread of body font sizes (a light novel must not print one sentence at
  18 pt and the next at 7 pt),
* images lost per page.

Every issue carries a human readable message so the UI and the QA report can
show the same list.
"""
from __future__ import annotations

import statistics
import unicodedata
from dataclasses import dataclass, field

import pymupdf

# Scripts a Persian output must not still contain (besides stray names).
FOREIGN_RANGES = {
    "cjk": lambda c: "\u4e00" <= c <= "\u9fff",
    "hiragana": lambda c: "\u3040" <= c <= "\u30ff",
    "hangul": lambda c: "\uac00" <= c <= "\ud7af",
}


@dataclass
class QCReport:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, message: str) -> None:
        self.ok = False
        self.issues.append(message)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "issues": self.issues, "stats": self.stats}


def _text_spans(page) -> list[dict]:
    spans: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if str(span.get("text", "")).strip():
                    spans.append(span)
    return spans


def _overlap_pairs(page, tolerance: float = 3.0) -> set[tuple[tuple[int, int, int, int], ...]]:
    """Overlapping span pairs a page already has before we touch it."""
    boxes = [pymupdf.Rect(span["bbox"]) for span in _text_spans(page)]
    keys = set()
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            inter = boxes[i] & boxes[j]
            if inter.is_empty:
                continue
            if inter.width * inter.height > 0.45 * min(boxes[i].get_area(), boxes[j].get_area()):
                keys.add(_pair_key(boxes[i], boxes[j], tolerance))
    return keys


def _pair_key(a, b, tolerance: float = 3.0) -> tuple:
    def cell(rect):
        return (
            int(round(rect.x0 / tolerance)),
            int(round(rect.y0 / tolerance)),
            int(round(rect.x1 / tolerance)),
            int(round(rect.y1 / tolerance)),
        )

    first, second = sorted((cell(a), cell(b)))
    return (first, second)


def _pair_in(a, b, known: set, tolerance: float = 3.0) -> bool:
    """Is this pair one of the pairs the source already had?"""
    key = _pair_key(a, b, tolerance)
    if key in known:
        return True
    # Tolerate a few points of drift in either box.
    for delta in (-1, 1):
        shifted = tuple((c[0], c[1] + delta, c[2], c[3]) for c in key)
        if shifted in known:
            return True
    return False


def _count_script(text: str, name: str) -> int:
    check = FOREIGN_RANGES[name]
    return sum(1 for char in text if check(char))


def check_pdf(source_path: str, output_path: str, target_lang: str = "fa") -> QCReport:
    """Run every check and return a report (never raises on a bad PDF)."""
    report = QCReport()
    with pymupdf.open(source_path) as src, pymupdf.open(output_path) as out:
        report.stats["pages_in"] = src.page_count
        report.stats["pages_out"] = out.page_count

        # A translation may add at most the attribution page.
        if out.page_count not in (src.page_count, src.page_count + 1):
            report.add(f"تعداد صفحات تغییر کرده: {src.page_count} → {out.page_count}")

        size_in = (round(src[0].rect.width), round(src[0].rect.height)) if src.page_count else None
        size_out = (round(out[0].rect.width), round(out[0].rect.height)) if out.page_count else None
        report.stats["page_size"] = size_out
        if size_in and size_out and size_in != size_out:
            report.add(f"اندازهٔ صفحه تغییر کرده: {size_in} → {size_out}")

        sizes: list[float] = []
        duplicated = 0
        overlapping = 0
        outside = 0
        foreign_chars = 0

        for index, page in enumerate(out):
            text = unicodedata.normalize("NFKC", page.get_text())
            images = len(page.get_images())
            if not text.strip() and images == 0:
                report.add(f"صفحهٔ {index + 1} خالی است.")
                continue
            if index < src.page_count and src[index].get_images() and not images:
                report.add(f"تصویر صفحهٔ {index + 1} از دست رفته است.")

            for name in FOREIGN_RANGES:
                foreign_chars += _count_script(text, name)

            spans = _text_spans(page)
            # A repeated line can already be repeated in the source (OCR layers
            # love doubling a heading); only new duplicates count.
            source_seen: dict[str, int] = {}
            if index < src.page_count:
                for span in _text_spans(src[index]):
                    content = str(span.get("text", "")).strip()
                    if len(content) > 8:
                        source_seen[content] = source_seen.get(content, 0) + 1
            seen: dict[str, int] = {}
            for span in spans:
                content = str(span.get("text", "")).strip()
                if len(content) > 8:
                    seen[content] = seen.get(content, 0) + 1
                box = pymupdf.Rect(span["bbox"])
                sizes.append(round(float(span["size"]), 1))
                if box.x1 > page.rect.x1 + 1 or box.x0 < page.rect.x0 - 1:
                    outside += 1
            duplicated += sum(
                1 for content, count in seen.items() if count > max(1, source_seen.get(content, 0))
            )

            boxes = [pymupdf.Rect(span["bbox"]) for span in spans if str(span.get("text", "")).strip()]
            # A scan's own OCR layer often has overlapping boxes. Those are the
            # source's problem, not the translation's, so they are not counted.
            inherited = _overlap_pairs(src[index]) if index < src.page_count else set()
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    inter = boxes[i] & boxes[j]
                    if inter.is_empty:
                        continue
                    area = inter.width * inter.height
                    if area > 0.45 * min(boxes[i].get_area(), boxes[j].get_area()):
                        if _pair_in(boxes[i], boxes[j], inherited):
                            continue
                        overlapping += 1

        # Ignore the attribution page when judging typography.
        body = sizes[:-2] if len(sizes) > 4 else sizes
        if body:
            median = statistics.median(body)
            report.stats["median_font"] = round(median, 1)
            report.stats["font_spread"] = round(max(body) - min(body), 1)
            small = [s for s in body if s < max(5.5, median * 0.55)]
            report.stats["tiny_spans"] = len(small)
            if len(small) > max(6, len(body) * 0.02):
                report.add(f"{len(small)} بخش متن بسیار کوچک‌تر از بقیه است (کمتر از ۵۵٪ اندازهٔ میانه).")

        report.stats.update(
            foreign_chars=foreign_chars,
            duplicated_blocks=duplicated,
            overlapping_spans=overlapping,
            outside_spans=outside,
        )
        if target_lang == "fa" and foreign_chars > 3:
            report.add(f"{foreign_chars} نویسهٔ چینی/ژاپنی/کره‌ای ترجمه‌نشده باقی مانده است.")
        if duplicated > 0:
            report.add(f"{duplicated} بلاک تکراری در خروجی دیده می‌شود.")
        if overlapping > 0:
            report.add(f"{overlapping} هم‌پوشانی متن با متن دیگر پیدا شد.")
        if outside > 0:
            report.add(f"{outside} اسپن از محدودهٔ صفحه بیرون زده است.")
    return report
