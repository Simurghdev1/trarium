"""Automatic cover, title, illustration and advertising page detection.

Trarium translates a PDF page by page. A book, though, is not made of pages
that all want the same treatment: the cloth cover, the title page, a full-page
plate and the publisher's advertisement at the end are artwork, and writing a
Persian translation over them destroys the only copy of that design. This
module looks at every page — not only the first and the last — and decides,
before a character is OCR'd or translated, whether the page should be left
exactly as it is.

Two kinds of evidence are used, in this order:

1. **Structure.** The page's own text layer tells a story page (many words in a
   paragraph shape) from a cover (a few short, centred, display-size lines on
   top of a full-page picture) or an advertisement (a link, a handle, an
   imprint and almost nothing else). This part is pure and unit-tested.
2. **Pixels.** Ink bands, darkness and colourfulness separate a photographed
   cover from a scanned page of prose, and a saturated digital cover from a
   cloth one. It only runs for the few pages that are short enough to be a
   candidate, so a 200-page novel costs a couple of seconds.
3. **A vision model**, when a key is configured, settles the genuinely
   ambiguous cases: an image-only page in the front or back matter (is it the
   cover, or a comic page with dialogue), and a page whose structural evidence
   sits just below the threshold. Its answer is capped by
   :attr:`CoverDetectionConfig.max_vision_pages`.

The decision is intentionally conservative. A page is only taken out of the
translation when the evidence is strong: below
:attr:`CoverDetectionConfig.threshold` the page is translated anyway and merely
reported as suspicious, because skipping a story page by mistake is far worse
than translating a cover by mistake.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field

# Page statuses. The strings are part of the public API: they are written to
# metrics.json, shown in the UI and accepted as manual overrides.
NORMAL_PAGE = "NORMAL_PAGE"
BOOK_COVER = "BOOK_COVER"
MANGA_COVER = "MANGA_COVER"
TITLE_PAGE = "TITLE_PAGE"
ILLUSTRATION_PAGE = "ILLUSTRATION_PAGE"
AD_PAGE = "AD_PAGE"
UNCERTAIN = "UNCERTAIN"

STATUSES = (
    NORMAL_PAGE,
    BOOK_COVER,
    MANGA_COVER,
    TITLE_PAGE,
    ILLUSTRATION_PAGE,
    AD_PAGE,
    UNCERTAIN,
)

# Statuses that remove a page from translation *when the confidence is high
# enough*. UNCERTAIN is deliberately absent: a doubtful page is still translated.
EXEMPT_STATUSES = frozenset({BOOK_COVER, MANGA_COVER, TITLE_PAGE, ILLUSTRATION_PAGE, AD_PAGE})

FA_LABELS = {
    NORMAL_PAGE: "صفحهٔ عادی",
    BOOK_COVER: "جلد کتاب",
    MANGA_COVER: "جلد مانگا",
    TITLE_PAGE: "صفحهٔ عنوان",
    ILLUSTRATION_PAGE: "صفحهٔ تصویری",
    AD_PAGE: "صفحهٔ تبلیغاتی",
    UNCERTAIN: "نامطمئن",
}

# A link, a handle, a shop or an imprint. Advertising pages are full of these
# and story pages are not.
_AD_PATTERN = re.compile(
    r"(https?://|www\.|@[A-Za-z0-9_.]{3,}|\binstagram\b|\bpatreon\b|\bko-?fi\b|"
    r"\btwitter\b|\bdiscord\b|\bfacebook\b|\bsubscribe\b|\bfollow\s+(me|us)\b|"
    r"\bsupport\s+the\s+author\b|\bavailable\s+(now|on)\b|\bbuy\s+(it|now)\b|"
    r"\bpublisher\b|\bimprint\b|\bISBN\b|\bcopyright\b|©)",
    re.I,
)


@dataclass
class CoverDetectionConfig:
    """Every knob the detector has. All of them are settings, not constants."""

    enabled: bool = True
    # Minimum confidence for a page to be taken out of the translation.
    threshold: float = 0.72
    # Evidence below this is not even worth reporting: the page is normal.
    uncertain_floor: float = 0.42
    detect_illustrations: bool = True
    detect_ads: bool = True
    # A vision model settles the ambiguous pages instead of guessing. Without a
    # key the structural rules decide alone.
    vision: bool = True
    vision_api_key: str | None = None
    vision_model: str | None = None
    max_vision_pages: int = 12
    # page index (0-based) -> status. The user always wins over the heuristics.
    manual: dict = field(default_factory=dict)

    def override(self, page_index: int) -> str | None:
        value = self.manual.get(page_index)
        if value is None:
            value = self.manual.get(str(page_index))
        if value is None:
            return None
        value = str(value).strip().upper()
        return value if value in STATUSES else None


@dataclass
class PageDecision:
    page_index: int
    status: str
    confidence: float
    reasons: list = field(default_factory=list)
    features: dict = field(default_factory=dict)
    source: str = "structure"

    @property
    def exempt(self) -> bool:
        """True when the page must be left exactly as it is."""
        return self.status in EXEMPT_STATUSES

    def as_dict(self) -> dict:
        return {
            "page": self.page_index + 1,
            "status": self.status,
            "label": FA_LABELS.get(self.status, self.status),
            "confidence": round(float(self.confidence), 3),
            "exempt": self.exempt,
            "source": self.source,
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
def _rect_tuple(rect) -> tuple:
    return (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))


def _union_area(rects, clip) -> float:
    """Area covered by the union of ``rects`` inside ``clip`` (sweep on x)."""
    cx0, cy0, cx1, cy1 = _rect_tuple(clip)
    boxes = []
    for rect in rects:
        x0, y0, x1, y1 = _rect_tuple(rect)
        x0, y0 = max(x0, cx0), max(y0, cy0)
        x1, y1 = min(x1, cx1), min(y1, cy1)
        if x1 > x0 and y1 > y0:
            boxes.append((x0, y0, x1, y1))
    if not boxes:
        return 0.0
    xs = sorted({b[0] for b in boxes} | {b[2] for b in boxes})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        if right <= left:
            continue
        spans = sorted((b[1], b[3]) for b in boxes if b[0] <= left and b[2] >= right)
        covered, cursor = 0.0, None
        for y0, y1 in spans:
            if cursor is None or y0 > cursor:
                covered += y1 - y0
                cursor = y1
            elif y1 > cursor:
                covered += y1 - cursor
                cursor = y1
        area += covered * (right - left)
    return area


def _text_features(page) -> dict:
    """Words, lines, display size and centring of the page's own text layer."""
    lines: list[tuple[str, tuple]] = []
    sizes: list[float] = []
    try:
        data = page.get_text("dict")
    except Exception:
        data = {"blocks": []}
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not text:
                continue
            lines.append((text, tuple(line.get("bbox") or (0, 0, 0, 0))))
            for span in spans:
                if str(span.get("text", "")).strip():
                    try:
                        sizes.append(float(span.get("size") or 0))
                    except (TypeError, ValueError):
                        pass
    width = float(page.rect.width) or 1.0
    chars = sum(len(text) for text, _ in lines)
    words = sum(len(text.split()) for text, _ in lines)
    centered = 0
    for _, box in lines:
        if abs(((box[0] + box[2]) / 2) - width / 2) <= width * 0.12:
            centered += 1
    long_lines = sum(1 for text, _ in lines if len(text) >= 45)
    return {
        "lines": len(lines),
        "words": words,
        "chars": chars,
        "avg_line_len": round(chars / len(lines), 1) if lines else 0.0,
        "long_lines": long_lines,
        "centered_ratio": round(centered / len(lines), 2) if lines else 0.0,
        "max_font": round(max(sizes), 2) if sizes else 0.0,
        "max_font_ratio": round(max(sizes) / float(page.rect.height), 4) if sizes else 0.0,
    }


def _image_coverage(page) -> tuple[float, int]:
    """Fraction of the page covered by its pictures (their union, not a sum)."""
    try:
        infos = page.get_image_info()
    except Exception:
        return 0.0, 0
    rects = [info.get("bbox") for info in infos if info.get("bbox")]
    page_area = max(1.0, float(page.rect.width) * float(page.rect.height))
    area = _union_area(rects, page.rect)
    return round(min(1.0, area / page_area), 3), len(rects)


def _raster_signals(page, dpi: int = 30) -> dict:
    """Cheap pixel statistics: a photographed cover or a scanned page of prose?

    A cloth-bound cover scanned flat is dark and almost monochrome and carries
    two or three tall ink bands (the title, the author); a page of prose is
    covered by many short horizontal ink bands; a digital comic cover is
    saturated from edge to edge.
    """
    signals = {"colorfulness": 0.0, "dark_ratio": 0.0, "ink_bands": 0, "max_band_frac": 0.0}
    try:
        import pymupdf

        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    except Exception:
        return signals
    width, height, n = pix.width, pix.height, pix.n
    if not width or not height:
        return signals
    samples = pix.samples
    step_x = max(1, width // 120)
    step_y = max(1, height // 160)
    columns = len(range(0, width, step_x))
    rows: dict[int, int] = {}
    saturation = 0
    sampled = 0
    dark = 0
    for y in range(0, height, step_y):
        base = y * width * n
        row_dark = 0
        for x in range(0, width, step_x):
            i = base + x * n
            r, g, b = samples[i], samples[i + 1], samples[i + 2]
            if (r * 299 + g * 587 + b * 114) // 1000 < 110:
                row_dark += 1
            saturation += max(r, g, b) - min(r, g, b)
            sampled += 1
        rows[y] = row_dark
        dark += row_dark
    if not sampled:
        return signals
    # Contiguous ink bands: rows where at least 2% of the width is ink.
    threshold = max(1, int(columns * 0.02))
    bands, current = [], 0
    for y in sorted(rows):
        if rows[y] >= threshold:
            current += 1
        elif current:
            bands.append(current)
            current = 0
    if current:
        bands.append(current)
    signals["colorfulness"] = round(saturation / sampled, 1)
    signals["dark_ratio"] = round(dark / sampled, 3)
    signals["ink_bands"] = len(bands)
    signals["max_band_frac"] = round(max(bands) * step_y / height, 3) if bands else 0.0
    return signals


def _page_raw_text(page) -> str:
    try:
        return str(page.get_text() or "")
    except Exception:
        return ""


def page_features(page, page_index: int = 0, total_pages: int = 1,
                  typical_size: tuple[float, float] | None = None,
                  raster: bool = True, doc_comic: bool = False) -> dict:
    """Everything the classifier needs to know about one page."""
    text = _text_features(page)
    coverage, image_count = _image_coverage(page)
    width, height = float(page.rect.width), float(page.rect.height)
    size_deviation = 0.0
    if typical_size and typical_size[0] and typical_size[1]:
        size_deviation = abs(width * height - typical_size[0] * typical_size[1]) / (
            typical_size[0] * typical_size[1]
        )
    try:
        drawings = len(page.get_drawings())
    except Exception:
        drawings = 0
    features = {
        "page_index": page_index,
        "width": round(width, 1),
        "height": round(height, 1),
        "image_coverage": coverage,
        "image_count": image_count,
        "drawings": drawings,
        "size_deviation": round(size_deviation, 3),
        "edge_page": page_index == 0 or page_index >= max(total_pages - 1, 1),
        "front_matter": page_index < 3 or page_index >= max(total_pages - 2, 1),
        "has_text_layer": bool(_page_raw_text(page).strip()),
        "doc_comic": bool(doc_comic),
        "linkish": bool(_AD_PATTERN.search(_page_raw_text(page))),
        **text,
    }
    # Pixel work only for pages short enough to be a cover or an illustration.
    if raster and features["words"] <= 130:
        features.update(_raster_signals(page))
    else:
        features.update({"colorfulness": 0.0, "dark_ratio": 0.0, "ink_bands": 0, "max_band_frac": 0.0})
    return features


# ---------------------------------------------------------------------------
# Classification (pure: no PDF access, so every rule is unit-tested)
# ---------------------------------------------------------------------------
def _clamp(value: float) -> float:
    return round(max(0.0, min(0.98, value)), 2)


def body_text_score(features: dict) -> float:
    """How much this page reads like translated prose rather than a cover."""
    words = int(features.get("words") or 0)
    lines = int(features.get("lines") or 0)
    avg = float(features.get("avg_line_len") or 0)
    long_lines = int(features.get("long_lines") or 0)
    if words >= 60 and lines >= 5 and long_lines >= 2:
        return 1.0
    if words >= 35 and long_lines >= 1:
        return 0.75
    if words >= 15 and avg >= 30:
        return 0.5
    return 0.0


def cover_score(features: dict) -> float:
    """Evidence that the page is a cover: big picture, short display text."""
    coverage = float(features.get("image_coverage") or 0)
    words = int(features.get("words") or 0)
    score = 0.0
    if coverage >= 0.9:
        score += 0.34
    elif coverage >= 0.6:
        score += 0.24
    elif coverage >= 0.35:
        score += 0.12
    if words == 0:
        score += 0.20
    elif words <= 40:
        score += 0.16
    elif words <= 90:
        score += 0.08
    if float(features.get("max_font_ratio") or 0) >= 0.035:
        score += 0.16
    if float(features.get("centered_ratio") or 0) >= 0.5:
        score += 0.08
    if features.get("edge_page"):
        score += 0.18
    if float(features.get("size_deviation") or 0) >= 0.05:
        score += 0.10
    score -= 0.45 * body_text_score(features)
    if words >= 150:
        score -= 0.25
    long_lines = int(features.get("long_lines") or 0)
    if long_lines >= 3:
        score -= 0.10 * min(3, long_lines - 2)
    return score


def looks_like_illustration(features: dict) -> bool:
    coverage = float(features.get("image_coverage") or 0)
    words = int(features.get("words") or 0)
    return coverage >= 0.75 and words <= 5


def looks_like_title_page(features: dict) -> bool:
    words = int(features.get("words") or 0)
    lines = int(features.get("lines") or 0)
    return (
        words <= 60
        and 1 <= lines <= 6
        and float(features.get("max_font_ratio") or 0) >= 0.02
        and float(features.get("centered_ratio") or 0) >= 0.5
        and float(features.get("image_coverage") or 0) < 0.55
        and body_text_score(features) < 0.5
    )


def looks_like_ad(features: dict) -> bool:
    words = int(features.get("words") or 0)
    return (
        bool(features.get("linkish"))
        and words <= 120
        and int(features.get("lines") or 0) <= 14
        and body_text_score(features) < 0.8
        and float(features.get("image_coverage") or 0) < 0.95
    )


def cover_kind(features: dict) -> str:
    """Name the cover after the document it belongs to, not after its colours."""
    return MANGA_COVER if features.get("doc_comic") else BOOK_COVER


def classify_features(features: dict, config: CoverDetectionConfig | None = None) -> tuple[str, float, list]:
    """Return ``(status, confidence, reasons)`` for one page's features.

    One rule sits above all the others: no page leaves the translation on
    evidence below :attr:`CoverDetectionConfig.threshold`. It is reported as
    :data:`UNCERTAIN` and translated anyway.
    """
    config = config or CoverDetectionConfig()
    status, confidence, reasons = _classify(features, config)
    if status in EXEMPT_STATUSES and confidence < float(config.threshold):
        return UNCERTAIN, confidence, list(reasons) + ["اطمینان کمتر از آستانه: ترجمه می‌شود"]
    return status, confidence, reasons


def _classify(features: dict, config: CoverDetectionConfig) -> tuple[str, float, list]:
    words = int(features.get("words") or 0)
    lines = int(features.get("lines") or 0)
    coverage = float(features.get("image_coverage") or 0)

    if not config.enabled:
        return NORMAL_PAGE, 1.0, ["تشخیص خودکار خاموش است"]

    # 0) Advertising and credits pages are short, non-paragraph text with links.
    if config.detect_ads and looks_like_ad(features):
        return AD_PAGE, 0.8, ["پیوند یا اطلاعات ناشر/شبکه‌های اجتماعی"]

    body = body_text_score(features)
    if body >= 1.0:
        return NORMAL_PAGE, 0.95, [f"متن پاراگرافی: {words} واژه در {lines} سطر"]
    if body >= 0.5 and words >= 35:
        # Mixed page (illustration + several lines of story): translate it.
        return NORMAL_PAGE, 0.85, [f"صفحهٔ ترکیبی تصویر و متن: {words} واژه"]

    # 1) A full-page picture with no words in the text layer.
    #
    # Only the first and the last page can be called a cover from this evidence
    # alone. Any other picture-only page may just as well be a comic page whose
    # dialogue lives in the image, so it stays in the translation: OCR will read
    # it, and if there is nothing to read the page is left untouched anyway.
    # (The front-matter window is where the vision check takes over.)
    if looks_like_illustration(features):
        # A display line on a full-page picture is a cover, wherever it sits in
        # the file (an omnibus repeats its covers). Without one it is a plate.
        if features.get("edge_page") or float(features.get("max_font_ratio") or 0) >= 0.035:
            return cover_kind(features), 0.88, ["تصویر تمام‌صفحه با عنوان نمایشی"]
        if features.get("has_text_layer"):
            return ILLUSTRATION_PAGE, 0.8, ["تصویر تمام‌صفحهٔ بدون متن"]
        return NORMAL_PAGE, 0.5, ["تصویر بدون لایهٔ متنی: OCR/ترجمه می‌شود"]

    # 2) Display text on a large picture. A cover is set in display type, or it
    # carries almost no words at all; a scanned page of prose with one line on
    # top of its image is not a cover.
    score = cover_score(features)
    display_type = float(features.get("max_font_ratio") or 0) >= 0.03 or words <= 3
    if words <= 90 and coverage >= 0.45 and display_type:
        if score >= config.uncertain_floor:
            detail = [f"تصویر {coverage:.0%} صفحه", f"{words} واژهٔ کوتاه"]
            if float(features.get("max_font_ratio") or 0) >= 0.035:
                detail.append("عنوان با اندازهٔ نمایشی")
            if features.get("edge_page"):
                detail.append("صفحهٔ ابتدا/انتهای فایل")
            if score < config.threshold:
                detail.append("اطمینان کمتر از آستانه: ترجمه می‌شود")
                return UNCERTAIN, _clamp(score), detail
            return cover_kind(features), _clamp(score), detail
        if coverage >= 0.85 and words <= 25 and config.detect_illustrations:
            return ILLUSTRATION_PAGE, 0.66, [f"تصویر تمام‌صفحه با {words} واژه"]

    # 3) Title page: no big picture, few centred display lines.
    if looks_like_title_page(features):
        confidence = _clamp(0.55 + min(0.35, float(features.get("max_font_ratio") or 0) * 6))
        return TITLE_PAGE, confidence, [f"{lines} سطر کوتاه و وسط‌چین", "بدون ساختار پاراگراف"]

    # 4) Something looks like a cover but the evidence is thin: report, translate.
    if score >= config.uncertain_floor:
        return UNCERTAIN, _clamp(score), ["شواهد ناکافی برای خارج کردن صفحه از ترجمه"]

    return NORMAL_PAGE, 0.9, ["متن عادی"]


def detect_page(page, page_index: int = 0, total_pages: int = 1,
                typical_size: tuple[float, float] | None = None,
                config: CoverDetectionConfig | None = None,
                doc_comic: bool = False) -> PageDecision:
    config = config or CoverDetectionConfig()
    features = page_features(page, page_index, total_pages, typical_size, doc_comic=doc_comic)
    override = config.override(page_index)
    if override:
        status, confidence, reasons, source = override, 1.0, ["وضعیت دستی کاربر"], "manual"
    else:
        status, confidence, reasons = classify_features(features, config)
        source = "structure"
    return PageDecision(page_index, status, confidence, reasons, features, source)


# ---------------------------------------------------------------------------
# Vision confirmation
# ---------------------------------------------------------------------------
VISION_PROMPT = """You are looking at a single page of a PDF that is about to be translated.

Decide what kind of page it is, using the picture AND the layout:
- BOOK_COVER: the cover of a printed book (title, author, publisher, artwork or cloth binding)
- MANGA_COVER: the cover of a comic or manga volume
- TITLE_PAGE: a title/half-title page, mostly typography, little or no illustration
- ILLUSTRATION_PAGE: a full-page picture or plate with no dialogue and no story text
- AD_PAGE: an advertisement, a publisher's catalogue page, a credits or links page
- NORMAL_PAGE: a page of the story, including a comic page with speech bubbles
- UNCERTAIN: you cannot tell

A comic or manga page with panels and dialogue is NORMAL_PAGE, never a cover.
A story page with one full-page illustration and a few lines of prose is NORMAL_PAGE.

Answer with JSON only, no markdown:
{"status": "<one of the labels>", "confidence": <0.0-1.0>, "reason": "<max 20 words>"}"""

VISION_API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def page_jpeg(page, dpi: int = 100, quality: int = 80) -> bytes:
    import pymupdf

    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return pix.tobytes("jpeg", jpg_quality=quality)


def parse_vision_answer(text: str) -> tuple[str, float, str] | None:
    """Read the model's JSON, tolerating a code fence around it."""
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return None
    status = str(data.get("status") or "").strip().upper()
    if status not in STATUSES:
        return None
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.7
    return status, _clamp(confidence), str(data.get("reason") or "")[:160]


# The vision question is answered by the lite models first: they are the ones
# that stay available when the full models answer 503 under load.
VISION_MODELS = ("gemini-flash-lite-latest", "gemini-3.1-flash-lite")


def gemini_vision_classify(page, api_key: str, model: str | None = None,
                           timeout: int = 60) -> tuple[str, float, str] | None:
    """Ask Gemini what this page is. Returns ``None`` on any failure."""
    if not api_key:
        return None
    image = base64.b64encode(page_jpeg(page)).decode("ascii")
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": VISION_PROMPT},
                    {"inlineData": {"mimeType": "image/jpeg", "data": image}},
                ]
            }
        ],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 120},
    }
    for name in ([model] if model else []) + [m for m in VISION_MODELS if m != model]:
        try:
            import requests

            response = requests.post(
                VISION_API.format(model=name),
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key,
                         "User-Agent": "Trarium/2.18.0"},
                json=payload,
                timeout=timeout,
            )
            if response.status_code >= 400:
                continue
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                continue
            text = "".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", []))
            answer = parse_vision_answer(text)
            if answer:
                return answer
        except Exception:
            continue
    return None


def _vision_candidate(features: dict, config: CoverDetectionConfig) -> bool:
    if not (config.vision and config.vision_api_key):
        return False
    image_only = int(features.get("words") or 0) <= 5 and float(features.get("image_coverage") or 0) >= 0.6
    if image_only and features.get("front_matter"):
        return True
    if 0 < int(features.get("words") or 0) <= 90 and float(features.get("image_coverage") or 0) >= 0.45:
        return config.uncertain_floor <= cover_score(features) < config.threshold
    return False


# ---------------------------------------------------------------------------
# Document level
# ---------------------------------------------------------------------------
def _typical_size(doc) -> tuple[float, float] | None:
    """The most common page size of the document, used to spot a scanned cover."""
    counts: dict[tuple[int, int], int] = {}
    for page in doc:
        key = (round(float(page.rect.width)), round(float(page.rect.height)))
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    size, _ = max(counts.items(), key=lambda item: item[1])
    return float(size[0]), float(size[1])


def document_is_comic(doc) -> bool:
    """True when most pages carry no text layer at all: a scanned comic.

    A scanned novel still has an OCR text layer on most of its pages, so this
    only fires for documents that are pictures from beginning to end.
    """
    empty = 0
    total = len(doc)
    for page in doc:
        if len(_page_raw_text(page).strip()) < 20:
            empty += 1
    return total > 0 and empty / total >= 0.7


def detect_pages(doc, config: CoverDetectionConfig | None = None,
                 progress_callback=None) -> list[PageDecision]:
    """Classify every page of ``doc``, independently of the others."""
    config = config or CoverDetectionConfig()
    total = len(doc)
    typical = _typical_size(doc)
    comic = document_is_comic(doc)
    decisions: list[PageDecision] = []
    vision_used = 0
    for page_index in range(total):
        page = doc[page_index]
        decision = detect_page(page, page_index, total, typical, config, doc_comic=comic)
        if decision.source != "manual" and _vision_candidate(decision.features, config) \
                and vision_used < config.max_vision_pages:
            answer = gemini_vision_classify(page, config.vision_api_key or "", config.vision_model)
            vision_used += 1
            if answer:
                status, confidence, reason = answer
                if comic and status == BOOK_COVER:
                    status = MANGA_COVER
                if status != NORMAL_PAGE and confidence < config.threshold:
                    status = UNCERTAIN
                decision = PageDecision(
                    page_index, status, confidence,
                    [f"بینایی ماشین: {reason}"] if reason else ["بینایی ماشین"],
                    decision.features, "vision",
                )
        decisions.append(decision)
        if progress_callback and (page_index + 1) % 20 == 0:
            progress_callback((page_index + 1) / max(total, 1), f"بررسی صفحه {page_index + 1}/{total}")
    return _keep_something_to_translate(decisions)


def _keep_something_to_translate(decisions: list[PageDecision]) -> list[PageDecision]:
    """Never exempt every page that has text.

    A one-page file (a scan of a single page, a short sampler) would otherwise
    be excluded whole and produce an empty translation. The page with the most
    words is put back into the run.
    """
    if not decisions or not all(d.exempt for d in decisions):
        return decisions
    richest = max(decisions, key=lambda d: int(d.features.get("words") or 0))
    if int(richest.features.get("words") or 0) <= 0:
        return decisions  # nothing to translate anyway: an image-only file
    richest.status = NORMAL_PAGE
    richest.confidence = 0.5
    richest.reasons = list(richest.reasons) + ["تنها صفحهٔ دارای متن؛ برای ترجمه نگه داشته شد"]
    return decisions


def summary(decisions: list[PageDecision]) -> dict:
    """Counts for the final report, including the pages left untranslated."""
    counts = {status: 0 for status in STATUSES}
    for decision in decisions:
        counts[decision.status] = counts.get(decision.status, 0) + 1
    exempt = [d.page_index + 1 for d in decisions if d.exempt]
    return {
        "pages_total": len(decisions),
        "translated_pages": len(decisions) - len(exempt),
        "exempt_pages": exempt,
        "book_covers": counts[BOOK_COVER],
        "manga_covers": counts[MANGA_COVER],
        "title_pages": counts[TITLE_PAGE],
        "illustration_pages": counts[ILLUSTRATION_PAGE],
        "ad_pages": counts[AD_PAGE],
        "uncertain_pages": counts[UNCERTAIN],
        "normal_pages": counts[NORMAL_PAGE],
    }


def parse_page_status(spec: str | None) -> dict:
    """``"1=MANGA_COVER,16=NORMAL_PAGE"`` -> ``{0: "MANGA_COVER", 15: "NORMAL_PAGE"}``."""
    result: dict[int, str] = {}
    if not spec:
        return result
    for part in str(spec).split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        number, status = part.split("=", 1)
        status = status.strip().upper()
        try:
            index = int(number.strip()) - 1
        except ValueError:
            continue
        if index >= 0 and status in STATUSES:
            result[index] = status
    return result
