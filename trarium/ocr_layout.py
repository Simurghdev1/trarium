"""OCR layout algebra: reading order, line merging, box validation, cover sizing.

Nothing in this module touches a network, PyMuPDF or an OCR engine.  Every
function works on plain block dicts (``{"bbox": (x0, y0, x1, y1), "text": ...}``)
and plain rects, so the geometry that decides *where* a translated line lands
can be unit-tested without a PDF and reused by every engine (native text,
PaddleOCR, Tesseract, EasyOCR, Surya, Gemini and OpenRouter vision).

The rules implemented here are the ones the PDF writer depends on:

* reading order comes from a recursive XY-cut, not from a naive ``(y, x)`` sort,
  so two-column pages and side-by-side manga panels are not interleaved;
* OCR lines are merged into paragraphs only when the gap, the horizontal
  overlap and the size ratio all agree, so a bubble is never merged with the
  bubble beside it or with a title above it;
* every block is validated for impossible geometry (empty, off-page, page-sized,
  text-on-text) before it may reach the redaction step;
* translation boxes grow only into free space measured against the *other*
  blocks of the same page, never over artwork that belongs to a neighbour.
"""
from __future__ import annotations

import math
import re
from statistics import median

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# Blocks whose text is shorter than this are OCR dust, not a sentence.
MIN_TEXT_CHARS = 2
# A page is treated as one raster when at least this share of a box is covered
# by a page image; the original glyphs live in those pixels and must be covered.
RASTER_COVER_THRESHOLD = 0.6
# Impossible geometry: no real text block occupies a quarter of the page.
MAX_BLOCK_AREA_RATIO = 0.35


# --------------------------------------------------------------------- basics
def box(block) -> tuple[float, float, float, float]:
    """Return a block bbox as ``(x0, y0, x1, y1)`` floats, or a zero box."""
    try:
        x0, y0, x1, y1 = [float(v) for v in (block.get("bbox") or (0, 0, 0, 0))]
    except (TypeError, ValueError):
        return 0.0, 0.0, 0.0, 0.0
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def text_of(block) -> str:
    return str(block.get("text") or "").strip()


def width(b) -> float:
    return max(0.0, b[2] - b[0])


def height(b) -> float:
    return max(0.0, b[3] - b[1])


def area(b) -> float:
    return width(b) * height(b)


def center(b) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def intersection(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def iou(a, b) -> float:
    inter = intersection(a, b)
    if inter <= 0:
        return 0.0
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def x_overlap_ratio(a, b) -> float:
    """Horizontal overlap of two boxes relative to the narrower one."""
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    narrower = min(width(a), width(b))
    return overlap / narrower if narrower > 0 else 0.0


def line_height_of(block) -> float:
    """Height of one text line, not of the whole paragraph box."""
    explicit = block.get("line_height")
    if explicit:
        try:
            value = float(explicit)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    h = height(box(block))
    lines = block.get("line_count")
    if isinstance(lines, int) and lines > 1:
        return h / lines
    return h


def guess_line_count(b: tuple[float, float, float, float], text: str) -> int:
    """Estimate how many printed lines a box holds.

    A printed line is about ``0.55 * font_size`` tall and average advance width
    is about ``0.5 * font_size``, so for a box of area ``W*H`` holding ``N``
    characters: ``N ≈ (W / 0.5h) * (H / h)`` → ``h ≈ sqrt(W*H / (0.5*N))``.
    """
    n = max(1, len(text.strip()))
    w, h = width(b), height(b)
    if w <= 0 or h <= 0:
        return 1
    if " " not in text.strip() and len(text.strip()) <= 3:
        return 1  # short label, single line
    line_h = math.sqrt((w * h) / max(0.5 * n, 0.5))
    return max(1, int(round(h / max(line_h, 0.1))))


def estimate_font_size(b: tuple[float, float, float, float], text: str,
                       fallback: float = 11.0) -> float:
    """Font size of the *source* text, used as the starting size for Persian.

    Vision OCR only reports a paragraph box, so a naive ``height * 0.25`` turns a
    six-line paragraph into 30pt type.  Estimating the line count first keeps
    the translation at roughly the original size.
    """
    h = height(b)
    if h <= 0:
        return fallback
    lines = guess_line_count(b, text)
    size = (h / max(1, lines)) * 0.75
    if not (2.0 <= size <= 60.0):
        size = fallback
    return max(6.0, min(16.0, size))


def is_rtl(text: str) -> bool:
    arabic = len(ARABIC_RE.findall(text or ""))
    latin = len(_LATIN_RE.findall(text or ""))
    return arabic > latin


def median_line_height(blocks: list[dict]) -> float:
    values = [line_height_of(b) for b in blocks if line_height_of(b) > 0]
    return median(values) if values else 12.0


# ------------------------------------------------------------------ ordering
def _spans_1d(blocks, axis: int) -> list[tuple[float, float]]:
    spans = []
    for b in blocks:
        bb = box(b)
        spans.append((bb[axis], bb[axis + 2]))
    return sorted(spans)


def _widest_gap(spans: list[tuple[float, float]]) -> tuple[float, float | None]:
    if len(spans) < 2:
        return 0.0, None
    best = 0.0
    split = None
    cursor = spans[0][1]
    for start, end in spans[1:]:
        gap = start - cursor
        if gap > best:
            best, split = gap, (cursor + start) / 2.0
        cursor = max(cursor, end)
    return best, split


def _row_key(b) -> tuple[float, float]:
    bb = box(b)
    return (round(bb[1], 1), bb[0])


def _xy_cut(blocks: list[dict], page_width: float, depth: int = 0) -> list[dict]:
    if len(blocks) <= 1 or depth > 14:
        return sorted(blocks, key=_row_key)
    # Vertical gutter first: separate columns and side-by-side panels.
    gap, split = _widest_gap(_spans_1d(blocks, 0))
    min_gap = max(4.0, page_width * 0.025)
    if split is not None and gap >= min_gap:
        left = [b for b in blocks if center(box(b))[0] < split]
        right = [b for b in blocks if center(box(b))[0] >= split]
        if left and right:
            return _xy_cut(left, page_width, depth + 1) + _xy_cut(right, page_width, depth + 1)
    # Horizontal gap: title above body, stacked panels, stacked bubbles.
    gap, split = _widest_gap(_spans_1d(blocks, 1))
    min_h_gap = max(4.0, median_line_height(blocks) * 0.9)
    if split is not None and gap >= min_h_gap:
        top = [b for b in blocks if center(box(b))[1] < split]
        bottom = [b for b in blocks if center(box(b))[1] >= split]
        if top and bottom:
            return _xy_cut(top, page_width, depth + 1) + _xy_cut(bottom, page_width, depth + 1)
    return sorted(blocks, key=_row_key)


def reading_order(blocks: list[dict], page_width: float | None = None) -> list[dict]:
    """Return blocks in human reading order (columns, then top to bottom)."""
    items = [b for b in blocks or [] if text_of(b)]
    if len(items) <= 1:
        return items
    if page_width is None:
        page_width = max([box(b)[2] for b in items] or [0.0]) or 1.0
    return _xy_cut(items, float(page_width))


# ------------------------------------------------------------------- merging
def _can_merge(prev: dict, cur: dict, page_width: float) -> bool:
    pb, cb = box(prev), box(cur)
    if cb[1] < pb[1] - 1.0:
        return False  # cur is above prev: not the next line
    h_prev = line_height_of(prev)
    h_cur = line_height_of(cur)
    if min(h_prev, h_cur) <= 0:
        return False
    gap = cb[1] - pb[3]
    # The gap is measured against one *text line* of each block, not against the
    # whole box: a paragraph box is many lines tall, so using its height let two
    # stacked bubbles merge into a single box that then painted over the art
    # between them.
    if gap < -0.45 * min(h_prev, h_cur):
        return False
    max_gap = max(2.0, 0.35 * min(h_prev, h_cur))
    if gap > max_gap:
        return False
    if x_overlap_ratio(pb, cb) < 0.55:
        return False
    w_ratio = min(width(pb), width(cb)) / max(1e-3, max(width(pb), width(cb)))
    if w_ratio < 0.35:
        # A short last line is legitimate only when it shares the paragraph's
        # left (LTR) or right (RTL) edge; otherwise it is a separate bubble.
        edge_tolerance = max(4.0, page_width * 0.02)
        aligned = (abs(cb[0] - pb[0]) <= edge_tolerance
                   or abs(cb[2] - pb[2]) <= edge_tolerance)
        if not aligned:
            return False
    if max(h_prev, h_cur) / max(1e-3, min(h_prev, h_cur)) > 1.8:
        return False  # title next to body text
    return True


def merge_lines(blocks: list[dict], page_width: float | None = None) -> list[dict]:
    """Merge consecutive OCR lines into paragraph blocks without crossing columns.

    ``line_height`` is carried through so the writer keeps the original type
    size instead of deriving it from the merged (tall) box.
    """
    ordered = reading_order(blocks, page_width)
    if not ordered:
        return []
    if page_width is None:
        page_width = max([box(b)[2] for b in ordered] or [0.0]) or 1.0
    merged: list[dict] = []
    for item in ordered:
        item = dict(item)
        item["text"] = text_of(item)
        item["bbox"] = box(item)
        item["line_height"] = line_height_of(item)
        item["line_count"] = int(item.get("line_count") or guess_line_count(item["bbox"], item["text"]))
        if not merged:
            merged.append(item)
            continue
        prev = merged[-1]
        if _can_merge(prev, item, float(page_width)):
            prev["text"] = f"{prev['text']} {item['text']}".strip()
            px0, py0, px1, py1 = prev["bbox"]
            cx0, cy0, cx1, cy1 = item["bbox"]
            prev["bbox"] = (min(px0, cx0), min(py0, cy0), max(px1, cx1), max(py1, cy1))
            prev["line_count"] = int(prev.get("line_count") or 1) + int(item.get("line_count") or 1)
            prev["line_height"] = (prev["line_height"] + item["line_height"]) / 2.0
            if prev.get("score") and item.get("score"):
                prev["score"] = min(prev["score"], item["score"])
            if prev.get("source") != item.get("source"):
                prev["source"] = "mixed"
            continue
        merged.append(item)
    for item in merged:
        item["font_size"] = float(item.get("font_size") or 0) or max(
            6.5, min(16.0, item["line_height"] * 0.75))
    return reading_order(merged, float(page_width))


# ------------------------------------------------------------------- quality
def dedupe_blocks(blocks: list[dict]) -> list[dict]:
    """Drop boxes a vision model repeated (same text, same place)."""
    items = sorted((dict(b) for b in blocks or [] if text_of(b)),
                   key=lambda b: (-len(text_of(b)), -area(box(b))))
    kept: list[dict] = []
    for item in items:
        bb = box(item)
        text = _normalise_text(text_of(item))
        duplicate = False
        for other in kept:
            ob = box(other)
            overlap = iou(bb, ob)
            if overlap >= 0.85:
                duplicate = True
                break
            if overlap >= 0.45:
                other_text = _normalise_text(text_of(other))
                if text in other_text or other_text in text:
                    duplicate = True
                    break
        if not duplicate:
            kept.append(item)
    return kept


def _normalise_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip().lower()
    # Arabic/Persian letter variants should not defeat duplicate detection.
    return (value.replace("ي", "ی").replace("ك", "ک")
            .replace("\u200c", "").replace("‌", ""))


def block_flags(block, page_rect=None, others: list[dict] | None = None) -> list[str]:
    """Return a list of reasons this box must not be trusted as-is."""
    flags: list[str] = []
    bb = box(block)
    text = text_of(block)
    if len(text) < MIN_TEXT_CHARS:
        flags.append("empty")
    if width(bb) < 2.0 or height(bb) < 2.0:
        flags.append("tiny")
    if page_rect is not None:
        pw, ph = float(page_rect[2] - page_rect[0]), float(page_rect[3] - page_rect[1])
        cx, cy = center(bb)
        if not (page_rect[0] - 1 <= cx <= page_rect[2] + 1 and page_rect[1] - 1 <= cy <= page_rect[3] + 1):
            flags.append("outside")
        if pw > 0 and ph > 0 and area(bb) / (pw * ph) > MAX_BLOCK_AREA_RATIO:
            flags.append("page-sized")
    if height(bb) > 0 and width(bb) > 0 and height(bb) / width(bb) > 8.0 and len(text) > 12:
        flags.append("vertical")
    for other in others or []:
        if other is block:
            continue
        ob = box(other)
        inter = intersection(bb, ob)
        if inter <= 0:
            continue
        if iou(bb, ob) > 0.7:
            flags.append("duplicate")
            break
        smaller = min(area(bb), area(ob))
        if smaller > 0 and inter / smaller > 0.7:
            flags.append("text-on-text")
            break
    return flags


def uncertain_ratio(blocks: list[dict], page_rect=None) -> float:
    items = [b for b in blocks or [] if text_of(b)]
    if not items:
        return 1.0
    bad = 0
    for block in items:
        others = [b for b in items if b is not block]
        if block_flags(block, page_rect, others):
            bad += 1
    return bad / len(items)


def needs_second_pass(blocks: list[dict], page_rect=None) -> bool:
    """True when a page deserves a second, higher-resolution OCR pass."""
    items = [b for b in blocks or [] if text_of(b)]
    if not items:
        return True
    if uncertain_ratio(items, page_rect) >= 0.34:
        return True
    if page_rect is not None:
        page_area = max(1.0, (page_rect[2] - page_rect[0]) * (page_rect[3] - page_rect[1]))
        covered = sum(area(box(b)) for b in items)
        if covered / page_area > 0.75:
            return True
    return False


def merge_passes(primary: list[dict], secondary: list[dict], page_rect=None) -> list[dict]:
    """Combine two OCR passes, trusting whichever text is longer and cleaner.

    The second pass exists to recover boxes the first one missed and to replace
    garbled ones, never to add a second copy of a line we already have.
    """
    primary = dedupe_blocks(primary or [])
    secondary = dedupe_blocks(secondary or [])
    if not secondary:
        return primary
    if not primary:
        return secondary
    result = [dict(b) for b in primary]
    for candidate in secondary:
        cb = box(candidate)
        best_index, best_overlap = None, 0.0
        for i, existing in enumerate(result):
            overlap = iou(cb, box(existing))
            if overlap > best_overlap:
                best_index, best_overlap = i, overlap
        if best_index is not None and best_overlap >= 0.35:
            current = result[best_index]
            if _looks_better(candidate, current):
                merged = dict(candidate)
                # Keep the union so no glyph of the original box is left behind.
                eb = box(current)
                merged["bbox"] = (min(eb[0], cb[0]), min(eb[1], cb[1]),
                                  max(eb[2], cb[2]), max(eb[3], cb[3]))
                result[best_index] = merged
            continue
        # No overlap at all: the second pass found text the first one lost.
        if len(text_of(candidate)) >= 3:
            result.append(dict(candidate))
    return reading_order(result, None)


def _looks_better(candidate: dict, current: dict) -> bool:
    a, b = text_of(candidate), text_of(current)
    if not a:
        return False
    if not b:
        return True
    if block_flags(candidate) and not block_flags(current):
        return False
    if block_flags(current) and not block_flags(candidate):
        return True
    # Prefer clearly longer text; otherwise keep the first pass (it saw the
    # page at the resolution the rest of the document used).
    return len(a) > len(b) * 1.5


# ---------------------------------------------------------------- free space
def free_bounds(b: tuple[float, float, float, float], page_rect, obstacles) -> tuple[float, float, float, float]:
    """Largest free rectangle around ``b`` bounded by neighbours and the page."""
    left = float(page_rect[0]) + 1.0
    top = float(page_rect[1]) + 1.0
    right = float(page_rect[2]) - 1.0
    bottom = float(page_rect[3]) - 1.0
    for ob in obstacles or []:
        o = box(ob) if isinstance(ob, dict) else tuple(ob)
        if o == b:
            continue
        # Neighbour shares our vertical band → it limits left/right growth.
        v_overlap = min(b[3], o[3]) - max(b[1], o[1])
        if v_overlap > 0.35 * max(1.0, height(b)):
            if o[2] <= b[0] + 0.5:
                left = max(left, o[2] + 1.0)
            elif o[0] >= b[2] - 0.5:
                right = min(right, o[0] - 1.0)
        h_overlap = min(b[2], o[2]) - max(b[0], o[0])
        if h_overlap > 0.2 * max(1.0, width(b)):
            if o[3] <= b[1] + 0.5:
                top = max(top, o[3] + 1.5)
            elif o[1] >= b[3] - 0.5:
                bottom = min(bottom, o[1] - 1.5)
    return (min(left, b[0]), min(top, b[1]), max(right, b[2]), max(bottom, b[3]))


def candidate_rects(b: tuple[float, float, float, float], page_rect, obstacles,
                    text: str = "") -> list[tuple[float, float, float, float]]:
    """Ordered candidate areas for the translated text, least disruptive first.

    The first candidate is the original box, so a translation that fits lands
    exactly where the source text was.  Later candidates grow only into space
    that is provably empty, which is what keeps long Persian sentences from
    spilling over an illustration or into the next bubble.
    """
    fb = free_bounds(b, page_rect, obstacles)
    free_left, free_top, free_right, free_bottom = fb
    bw, bh = width(b), height(b)
    candidates: list[tuple[float, float, float, float]] = [(b[0], b[1], b[2], b[3])]
    # 1) grow down a little (the usual case: Persian needs one extra line)
    if free_bottom > b[3] + 1.0:
        grow = min(free_bottom, b[3] + max(4.0, bh * 0.55))
        candidates.append((b[0], b[1], b[2], grow))
    # 2) grow horizontally into the free column width, keeping the RTL/LTR edge
    if free_right > b[2] + 1.0 or free_left < b[0] - 1.0:
        candidates.append((free_left, b[1], free_right, b[3]))
    # 3) full free area below the box (captions, paragraph tails)
    if free_bottom > b[3] + 1.0:
        candidates.append((free_left, b[1], free_right, free_bottom))
    # 4) allow a small lift above the box when it sits mid-page
    if free_top < b[1] - 1.0 and free_bottom > b[3]:
        lift = max(0.0, min(b[1] - free_top, bh * 0.25))
        candidates.append((free_left, b[1] - lift, free_right, free_bottom))
    # 5) last resort: the whole free column band around the box
    candidates.append((free_left, free_top, free_right, free_bottom))
    # De-duplicate while preserving order; reject anything unusable.
    seen: list[tuple[float, float, float, float]] = []
    for rect in candidates:
        rect = (round(rect[0], 2), round(rect[1], 2), round(rect[2], 2), round(rect[3], 2))
        if width(rect) < 2 or height(rect) < 2:
            continue
        if any(all(abs(rect[i] - other[i]) < 0.5 for i in range(4)) for other in seen):
            continue
        seen.append(rect)
    return seen


def cover_rect(b: tuple[float, float, float, float], pad: float = 1.0) -> tuple[float, float, float, float]:
    """Rect that must be painted before the translation is drawn over a raster."""
    return (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)


def raster_share(b, image_rects) -> float:
    """How much of ``b`` is backed by page images (i.e. the glyphs are pixels)."""
    total = area(b)
    if total <= 0:
        return 0.0
    covered = 0.0
    for image in image_rects or []:
        r = box(image) if isinstance(image, dict) else tuple(image)
        covered += intersection(b, r)
    return min(1.0, covered / total)


def background_from_samples(samples) -> tuple[float, float, float]:
    """Median of sampled background pixels as normalised RGB.

    Pure white is never assumed: a cream book page, a grey manga panel and a
    white speech bubble each need their own fill colour, otherwise every
    covered box shows up as a bright rectangle on the artwork.
    """
    vals = [tuple(float(c) for c in s) for s in (samples or [])]
    if not vals:
        return (1.0, 1.0, 1.0)
    channels = []
    for i in range(3):
        column = sorted(v[i] for v in vals)
        channels.append(column[len(column) // 2])
    return tuple(channels)  # type: ignore[return-value]


def coverage_stats(blocks: list[dict], page_rect) -> dict:
    """Diagnostics used by the QA harness and the UI summary."""
    items = [b for b in blocks or [] if text_of(b)]
    page_area = max(1.0, (page_rect[2] - page_rect[0]) * (page_rect[3] - page_rect[1]))
    return {
        "blocks": len(items),
        "chars": sum(len(text_of(b)) for b in items),
        "area_ratio": round(sum(area(box(b)) for b in items) / page_area, 4),
        "uncertain": round(uncertain_ratio(items, page_rect), 3),
    }
