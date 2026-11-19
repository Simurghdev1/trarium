"""Measured, collision-free layout for translated PDF text.

The old layout guessed how much room a translation would need from its word
count, and it let every box grow on its own. On a tight page — a list of
illustrations, an index, a table of contents set in two columns — two boxes
grew into each other and the lines were printed on top of one another.

This module does the layout the way a typesetter does:

1. the text is typographically cleaned first (:func:`prepare_text`), so the
   measurements are made on the exact string that will be written;
2. the real width and height are measured with the real font and the real line
   height (:func:`measure`), never estimated from a word count;
3. the page's columns are detected (:func:`column_bands`), so a box cannot grow
   from the left column into the right one;
4. every block gets a writing rectangle that intersects no other block and no
   fixed obstacle such as an illustration (:func:`plan_page`). When a block
   does not fit where it was, it shrinks to the measured size that fits and, if
   there is still no room, it moves to the nearest free space. It is never
   dropped and never painted over something else.

Everything here is pure geometry and font metrics, so each rule is unit-tested
without touching a PDF.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Distance kept between two writing boxes, in points.
EDGE = 1.0
# Line height used both for measuring and for the HTML box that writes the text.
LINE_HEIGHT = 1.02
MIN_SIZE = 6.0
# A translation may need more room than its source, but not three times as much:
# beyond that the font shrinks instead of the box growing across the page.
MAX_GROWTH = 3.0


# ---------------------------------------------------------------------------
# Text preparation: the string that is measured is the string that is written.
# ---------------------------------------------------------------------------
def prepare_text(text: str, target_lang: str = "fa") -> str:
    """Typographically clean the text before anything measures or writes it."""
    value = str(text or "")
    if target_lang == "fa":
        try:
            from trarium.fa_style import normalize

            cleaned = normalize(value)
            if cleaned:
                return cleaned
        except Exception:
            pass
    return value


# ---------------------------------------------------------------------------
# Font metrics
# ---------------------------------------------------------------------------
_FONTS: dict[str, object] = {}


def load_font(path: str):
    """A cached PyMuPDF font, or ``None`` when the file or PyMuPDF is missing."""
    key = str(path)
    if key in _FONTS:
        return _FONTS[key]
    try:
        import pymupdf

        font = pymupdf.Font(fontfile=key)
    except Exception:
        font = None
    _FONTS[key] = font
    return font


def text_width(text: str, font, size: float) -> float:
    if font is None:
        # Rough fallback: Persian letters average about half an em.
        return len(str(text or "")) * float(size) * 0.5
    try:
        return float(font.text_length(str(text or ""), float(size)))
    except Exception:
        return len(str(text or "")) * float(size) * 0.5


def wrap_lines(text: str, font, size: float, width: float) -> list[str]:
    """Break the text the way the HTML box will, by measured word widths."""
    words = str(text or "").split()
    if not words:
        return []
    limit = max(4.0, float(width))
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else current + " " + word
        if current and text_width(trial, font, size) > limit:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True)
class Measurement:
    size: float
    lines: int
    width: float
    height: float


def measure(text: str, font, size: float, width: float,
            line_height: float = LINE_HEIGHT) -> Measurement:
    """Real height of ``text`` at ``size`` inside ``width`` points."""
    count = max(1, len(wrap_lines(text, font, size, width)))
    height = count * float(size) * float(line_height) + float(size) * 0.25
    return Measurement(round(float(size), 2), count, round(float(width), 2), round(height, 2))


def fit_size(text: str, font, width: float, height: float, size: float,
             min_size: float = MIN_SIZE) -> Measurement:
    """Largest size at or below ``size`` whose measured height fits ``height``."""
    limit = float(size)
    floor = min(float(min_size), limit)
    measured = measure(text, font, limit, width)
    while measured.height > height and limit > floor:
        limit = round(max(floor, limit - 0.25), 2)
        measured = measure(text, font, limit, width)
    return measured


# ---------------------------------------------------------------------------
# Rectangle helpers (tuples of x0, y0, x1, y1)
# ---------------------------------------------------------------------------
def rect(box) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in box)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def width_of(box) -> float:
    r = rect(box)
    return max(0.0, r[2] - r[0])


def height_of(box) -> float:
    r = rect(box)
    return max(0.0, r[3] - r[1])


def overlap_area(a, b) -> float:
    ra, rb = rect(a), rect(b)
    w = min(ra[2], rb[2]) - max(ra[0], rb[0])
    h = min(ra[3], rb[3]) - max(ra[1], rb[1])
    if w <= 0 or h <= 0:
        return 0.0
    return w * h


def intersects(a, b, tolerance: float = EDGE) -> bool:
    ra, rb = rect(a), rect(b)
    w = min(ra[2], rb[2]) - max(ra[0], rb[0])
    h = min(ra[3], rb[3]) - max(ra[1], rb[1])
    return w > tolerance and h > tolerance


def overlap_pairs(boxes, tolerance: float = 1.0) -> list[tuple[int, int]]:
    """Index pairs whose boxes really sit on top of each other."""
    pairs = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if intersects(boxes[i], boxes[j], tolerance):
                pairs.append((i, j))
    return pairs


def clamp(box, page) -> tuple[float, float, float, float]:
    r = rect(box)
    p = rect(page)
    return (max(p[0], r[0]), max(p[1], r[1]), min(p[2], r[2]), min(p[3], r[3]))


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------
def _shares_edge(spans, edge: int, block: int = 6, share: float = 0.6) -> bool:
    if not spans:
        return False
    counts: dict[int, int] = {}
    for span in spans:
        cell = int(round(span[edge] / block))
        counts[cell] = counts.get(cell, 0) + 1
    return max(counts.values()) >= share * len(spans)


def column_bands(boxes, gap: float = 18.0, min_blocks: int = 2) -> list[tuple[float, float]]:
    """Detect the page's vertical columns from the boxes' horizontal spans.

    A list of illustrations or an index is often set in two columns, and treating
    it as one wide column lets the left half's entries grow over the right half's.

    Scattered boxes are not columns, though: the speech bubbles of a comic page
    leave gaps everywhere, and splitting them moved a translation out of its
    bubble. A real column therefore needs a page-wide gutter, enough entries on
    both sides, and entries that line up on one edge (that is what typesetting
    does; a comic page does not). Everything else stays a single band.
    """
    spans = sorted((rect(b) for b in boxes), key=lambda r: r[0])
    if not spans:
        return []
    whole = (min(r[0] for r in spans), max(r[2] for r in spans))
    extent = max(1.0, whole[1] - whole[0])

    # A block so wide that it could span the whole text area (a heading above
    # two columns) must not hide the gutter.
    narrow = [r for r in spans if (r[2] - r[0]) <= 0.70 * extent]
    if len(narrow) < 2 * min_blocks:
        return [whole]

    segments: list[list[float]] = []
    for r in narrow:
        if segments and r[0] <= segments[-1][1]:
            segments[-1][1] = max(segments[-1][1], r[2])
        else:
            segments.append([r[0], r[2]])

    gutter = max(float(gap), 0.03 * extent)
    clusters: list[list[list[float]]] = []
    for segment in segments:
        if clusters and segment[0] - clusters[-1][-1][1] >= gutter:
            clusters.append([segment])
        elif clusters:
            clusters[-1].append(segment)
        else:
            clusters.append([segment])
    if len(clusters) < 2:
        return [whole]

    bands: list[tuple[float, float]] = []
    for cluster in clusters:
        start, end = cluster[0][0], cluster[-1][1]
        members = [r for r in narrow if r[0] >= start - 0.5 and r[2] <= end + 0.5]
        if len(members) < min_blocks:
            return [whole]
        if not (_shares_edge(members, 0) or _shares_edge(members, 2)):
            # Scattered boxes with a gap between them are not a column.
            return [whole]
        bands.append((min(r[0] for r in members), max(r[2] for r in members)))
    return bands


def band_index(box, bands) -> int:
    """Which column a box belongs to; ``-1`` when it spans several columns."""
    r = rect(box)
    if len(bands) <= 1:
        return 0
    hits = [
        i for i, band in enumerate(bands)
        if min(r[2], band[1]) - max(r[0], band[0]) > 0.35 * max(1.0, r[2] - r[0])
    ]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        # A full-width heading above both columns is laid out before them.
        return -1
    return min(range(len(bands)), key=lambda i: abs(bands[i][0] - r[0]))


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
@dataclass
class LayoutItem:
    """One block to write: its text, the box it occupies now and its plan."""

    text: str
    cover: tuple[float, float, float, float]
    base_size: float = 11.0
    lang: str = "fa"
    # Largest area this block may use (a bubble that may breathe, a paragraph
    # that may use the gap under it). ``None`` means: as tall as measured.
    limit: tuple[float, float, float, float] | None = None
    plan: tuple[float, float, float, float] | None = None
    size: float = 0.0
    lines: int = 0
    moved: float = 0.0
    shrunk: float = 0.0
    overflow: bool = False


@dataclass
class LayoutReport:
    blocks: int = 0
    moved: int = 0
    shrunk: int = 0
    overflow: int = 0
    columns: int = 1
    notes: list[str] = field(default_factory=list)


def _free(candidate, claimed, obstacles) -> bool:
    # Strictly zero overlap: even a sliver of a line on a drawing or on the next
    # block counts as a collision.
    for box in claimed:
        if intersects(candidate, box, tolerance=0.0):
            return False
    for box in obstacles:
        if intersects(candidate, box, tolerance=0.0):
            return False
    return True


def _candidate_rows(top: float, bottom: float, height: float, floor: float):
    """Every whole-point y a box of ``height`` may start at, from ``top`` down.

    A block never moves above its source position: moving up would print a
    heading above the running head or a paragraph over the previous one.
    """
    top = max(floor, top)
    end = bottom - height
    y = top
    while y <= end:
        yield y
        y += 1.0


def plan_page(items: list[LayoutItem], page, font,
              fixed=(), min_size: float = MIN_SIZE,
              line_height: float = LINE_HEIGHT,
              max_growth: float = MAX_GROWTH) -> LayoutReport:
    """Give every item a writing rectangle that collides with nothing.

    ``fixed`` holds rectangles that may never be written on (an illustration,
    a block that stays in its original language). Items are placed in reading
    order; each one keeps its own box when the measured text fits there, shrinks
    to the largest size that does fit, and only moves when neither works.
    """
    report = LayoutReport(blocks=len(items))
    if not items:
        return report
    p = rect(page)
    obstacles = [rect(box) for box in fixed if width_of(box) > 0 and height_of(box) > 0]
    bands = column_bands([item.cover for item in items])
    report.columns = max(1, len(bands))

    for item in items:
        item.text = prepare_text(item.text, item.lang) or item.text
        item.cover = clamp(item.cover, p)

    order = sorted(
        items,
        key=lambda it: (band_index(it.cover, bands), it.cover[1], it.cover[0]),
    )

    claimed: list[tuple[float, float, float, float]] = []
    for item in order:
        box = item.cover
        x0, _, x1, _ = box
        index = band_index(box, bands)
        if index >= 0 and len(bands) > 1:
            band = bands[index]
            # Stay in the column: never widen a box to the whole column, that is
            # what used to push a short entry over its neighbour.
            x0 = max(x0, band[0])
            x1 = min(x1, band[1])
        x0, x1 = max(p[0] + EDGE, x0), min(p[2] - EDGE, x1)
        if x1 - x0 < 8.0:
            x0 = max(p[0] + EDGE, min(x0, x1 - 8.0))

        top = box[1]
        bottom = min(p[3] - EDGE, top + max(box[3] - box[1], 1.0) * max_growth)
        if item.limit is not None:
            # The limit is the largest area the block may use (a bubble's blank
            # space, a paragraph's gap); text never leaves it.
            limit = rect(item.limit)
            bottom = min(p[3] - EDGE, max(box[3], limit[3]))
            top = max(top, min(limit[1], box[1]))
            x0 = max(x0, min(limit[0], box[0]))
            x1 = min(x1, max(limit[2], box[2]))
        width = max(8.0, x1 - x0)

        base = max(float(item.base_size or 11.0), min_size)

        def find(bottom_limit: float, floor: float = min_size):
            """Largest size that fits between ``top`` and ``bottom_limit``."""
            size = base
            while True:
                measured = measure(item.text, font, size, width, line_height)
                height = measured.height + 1.0
                for y in _candidate_rows(top, bottom_limit, height, p[1] + EDGE):
                    candidate = (x0, y, x1, y + height)
                    if _free(candidate, claimed, obstacles):
                        return size, measured, candidate
                if size <= floor:
                    return None
                size = round(max(floor, size - 0.25), 2)

        # First choice: the block's own space (its source box, the gap under it,
        # the blank area of its bubble) at close to its own size. A heading is not
        # shrunk to seven points just because its source box was two lines tall.
        chosen = find(bottom, max(min_size, round(base * 0.8, 2)))
        if chosen is None:
            # The space it owns is too small for the whole translation. Rather
            # than shrink it to an unreadable size, or print it at the top of the
            # page, it may use the free room further down the page; the blocks
            # below it are placed after it and simply follow.
            chosen = find(p[3] - EDGE)
        if chosen is None:
            # Last resort: keep the block where its text lives. It is still
            # written (nothing is dropped); the caller reports the conflict.
            size = min_size
            measured = measure(item.text, font, size, width, line_height)
            height = min(measured.height + 1.0, p[3] - p[1] - 2 * EDGE)
            item.overflow = True
            report.overflow += 1
            chosen = (size, measured, (x0, box[1], x1, min(p[3] - EDGE, box[1] + height)))

        size, measured, placed = chosen
        item.plan = placed
        item.size = size
        item.lines = measured.lines
        item.shrunk = round(max(0.0, float(item.base_size or size) - size), 2)
        item.moved = round(placed[1] - box[1], 2)
        if abs(item.moved) >= 1.0:
            report.moved += 1
        if item.shrunk >= 0.25:
            report.shrunk += 1
        claimed.append(placed)

    if report.overflow:
        report.notes.append(f"{report.overflow} بلاک حتی با کوچک‌ترین اندازه در صفحه جا نشد.")
    return report
