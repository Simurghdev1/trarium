"""OCR.space OCR provider (free tier only).

OCR.space is a hosted OCR API: https://ocr.space/ocrapi

    POST https://api.ocr.space/parse/image
         apikey: <key>
         language=eng|chs|ara|...
         isOverlayRequired=true
         OCREngine=2
         file=<image or pdf>

Free plan facts (https://ocr.space/ocrapi/freekey):
* 25,000 requests / month, files up to 1 MB, PDFs up to 3 pages.
* ``Engine 1`` and ``Engine 2`` are available; ``Engine 3`` is reserved for
  PRO/paid plans, so this module never sends it.
* The documented demo key ``helloworld`` exists but is shared and throttled, so
  a real key is required for anything beyond a smoke test.

Design notes
------------
* Pages are rendered to JPEG (under 1 MB, the free-plan limit) and sent one at
  a time. Coordinates from ``TextOverlay`` are in the pixels of *that* image, so
  they are scaled back to PDF points before they reach the layout pipeline.
* The user's file is only read; nothing is written next to it.
* Any paid capability is refused: no engine 3, no paid endpoints, no fallbacks
  to other services.
"""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

import requests

ENDPOINT = "https://api.ocr.space/parse/image"

# Key lookup order: explicit argument, Trarium's own variable, then the two
# names people commonly export.
ENV_KEY = "TRARIUM_OCR_SPACE_KEY"
ALT_ENV_KEYS = ("OCR_SPACE_API_KEY", "OCRSPACE_API_KEY")

# Documented shared demo key; rate limited, only useful for a first request.
DEMO_KEY = "helloworld"

# Engines that the free plan can use. Engine 3 is paid and never sent.
FREE_ENGINES = (1, 2)

# OCR.space language codes for the languages the pipeline offers.
LANGUAGE_MAP = {
    "eng": "eng",
    "en": "eng",
    "chi_sim": "chs",
    "chs": "chs",
    "zh": "chs",
    "zh-cn": "chs",
    "chi_tra": "cht",
    "ja": "jpn",
    "jpn": "jpn",
    "ko": "kor",
    "ara": "ara",
    "ar": "ara",
    "fa": "ara",
    "fas": "ara",
    "per": "ara",
    "rus": "rus",
    "deu": "ger",
    "ger": "ger",
    "fra": "fre",
    "fre": "fre",
    "spa": "spa",
    "tur": "tur",
    "ita": "ita",
}

# Free plan refuses files above 1 MB.
MAX_BYTES = 1_000_000


class OcrSpaceError(RuntimeError):
    """Raised when OCR.space rejects a request or returns an error."""


def api_key(explicit: str | None = None) -> str:
    """Resolve the key to use, falling back to the shared demo key."""
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    for name in (ENV_KEY, *ALT_ENV_KEYS):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return DEMO_KEY


def has_real_key(explicit: str | None = None) -> bool:
    """True when a key other than the shared demo key is configured."""
    return api_key(explicit) != DEMO_KEY


def is_available(explicit: str | None = None) -> bool:
    """OCR.space needs no local install, only a key."""
    return has_real_key(explicit)


def availability_hint() -> str:
    if is_available():
        return "OCR.space آماده است (کلید رایگان ثبت شده)."
    return (
        "برای OCR.space یک کلید رایگان از https://ocr.space/ocrapi/freekey بگیرید و در "
        f"متغیر {ENV_KEY} یا کادر «کلید OCR.space» بگذارید. کلید عمومی helloworld "
        "محدود است و برای استفادهٔ واقعی کافی نیست."
    )


def language_codes(languages: str | list[str] | None) -> str:
    """Map Trarium language labels to OCR.space codes (single code per call)."""
    if not languages:
        return "eng"
    items = [languages] if isinstance(languages, str) else list(languages)
    for raw in items:
        for part in str(raw).replace("+", " ").split():
            code = LANGUAGE_MAP.get(part.strip().lower())
            if code:
                return code
    return "eng"


def _payload(language: str, engine: int) -> dict:
    if engine not in FREE_ENGINES:
        raise OcrSpaceError(
            f"موتور {engine} در پلن رایگان OCR.space نیست؛ فقط {FREE_ENGINES} مجاز است."
        )
    return {
        "language": language,
        "isOverlayRequired": "true",
        "OCREngine": str(engine),
        "scale": "true",
        "detectOrientation": "true",
        # Never ask for paid extras (searchable PDF / table output are PRO).
    }


def ocr_image_bytes(
    data: bytes,
    language: str = "eng",
    engine: int = 2,
    key: str | None = None,
    timeout: int = 120,
    retries: int = 3,
    file_name: str = "page.jpg",
    mime: str = "image/jpeg",
) -> dict:
    """Send one image and return the parsed JSON response."""
    if len(data) > MAX_BYTES:
        raise OcrSpaceError(
            f"حجم تصویر ({len(data)/1024:.0f} KB) از سقف ۱ مگابایت پلن رایگان بیشتر است."
        )
    headers = {"apikey": api_key(key)}
    payload = _payload(language, engine)
    last_error = ""
    for attempt in range(retries):
        try:
            response = requests.post(
                ENDPOINT,
                headers=headers,
                data=payload,
                files={"file": (file_name, data, mime)},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = f"شبکه: {exc}"
        else:
            if response.status_code == 200:
                body = response.json()
                if body.get("IsErroredOnProcessing"):
                    messages = body.get("ErrorMessage") or ["خطای نامشخص"]
                    raise OcrSpaceError("OCR.space: " + " | ".join(map(str, messages)))
                return body
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if response.status_code not in (429, 500, 502, 503, 504):
                break
        time.sleep(1.5 * (attempt + 1))
    raise OcrSpaceError(f"درخواست OCR.space ناموفق بود. {last_error}")


def build_blocks(parsed: dict, page_width: float, image_width: int) -> list[dict]:
    """Convert an OCR.space overlay into Trarium layout blocks (PDF points)."""
    scale = (page_width / image_width) if image_width else 1.0
    results = parsed.get("ParsedResults") or []
    blocks: list[dict] = []
    for result in results:
        overlay = (result.get("TextOverlay") or {}).get("Lines") or []
        for line in overlay:
            text = str(line.get("LineText") or "").strip()
            words = line.get("Words") or []
            if not text or not words:
                continue
            left = min(float(w.get("Left", 0)) for w in words)
            top = min(float(w.get("Top", 0)) for w in words)
            right = max(float(w.get("Left", 0)) + float(w.get("Width", 0)) for w in words)
            bottom = max(float(w.get("Top", 0)) + float(w.get("Height", 0)) for w in words)
            bbox = (left * scale, top * scale, right * scale, bottom * scale)
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            blocks.append(
                {
                    "bbox": bbox,
                    "text": text,
                    "font_size": max(6.0, (bbox[3] - bbox[1]) * 0.78),
                }
            )
    blocks.sort(key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
    return merge_lines(blocks)


def merge_lines(blocks: list[dict], gap_factor: float = 1.35, max_lines: int = 4) -> list[dict]:
    """Join consecutive lines of one paragraph, keeping each line's own box.

    OCR.space returns one box per text line. Sending every line separately made
    the translator hit rate limits, so lines of one paragraph are merged for
    translation — but the individual boxes are preserved in ``lines`` so the PDF
    writer can put the translation back on exactly those lines instead of
    blanketing a table or a chart with one huge white rectangle.
    """
    merged: list[dict] = []
    for block in blocks:
        entry = {
            "bbox": tuple(block["bbox"]),
            "text": block["text"],
            "font_size": block.get("font_size", 8.0),
            "lines": [{"bbox": tuple(block["bbox"]), "text": block["text"]}],
        }
        if merged:
            prev = merged[-1]
            px0, py0, px1, py1 = prev["bbox"]
            x0, y0, x1, y1 = entry["bbox"]
            last = prev["lines"][-1]["bbox"]
            last_h = max(1.0, last[3] - last[1])
            this_h = max(1.0, y1 - y0)
            height = max(1.0, py1 - py0)
            overlap = min(px1, x1) - max(px0, x0)
            width = max(1.0, min(px1 - px0, x1 - x0))
            same_column = overlap > 0.5 * width
            # OCR.space pads a line box downwards, so two lines of one title often
            # overlap by a third of a line; that is still the next line, not a
            # different block. Rejecting it split a three-line manga title.
            # The gap is judged against one line, not the whole block so far: a
            # two-line title used to reach down and swallow the lettering on the
            # cover art below it (Josep Moore p5).
            close = -0.6 * min(last_h, this_h) <= (y0 - last[3]) <= min(gap_factor, 1.0) * min(last_h, this_h)
            # A line two times taller is a different text (the artwork lettering
            # under a printed title); merging them glued the title to the artwork.
            same_size = max(last_h, this_h) / min(last_h, this_h) <= 1.8
            if same_column and close and same_size and len(prev["lines"]) < max_lines:
                prev["bbox"] = (min(px0, x0), min(py0, y0), max(px1, x1), max(py1, y1))
                joiner = "" if _is_cjk(prev["lines"][-1]["text"][-1:]) else " "
                prev["text"] = prev["text"] + joiner + entry["text"]
                prev["font_size"] = max(prev["font_size"], entry["font_size"])
                prev["lines"].append(entry["lines"][0])
                continue
        merged.append(entry)
    for block in merged:
        if len(block["lines"]) < 2:
            block.pop("lines")
    return merged


def _is_cjk(char: str) -> bool:
    return bool(char) and "\u3000" <= char <= "\u9fff"


def _render_page(page, dpi: int = 200) -> tuple[bytes, int]:
    """Render a page to JPEG small enough for the free plan, return (bytes, width)."""
    from PIL import Image

    for attempt_dpi in (dpi, int(dpi * 0.8), int(dpi * 0.65), 120):
        pix = page.get_pixmap(dpi=attempt_dpi, alpha=False)
        image = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)
        for quality in (80, 65, 50, 35):
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            data = buffer.getvalue()
            if len(data) <= MAX_BYTES:
                return data, pix.w
    raise OcrSpaceError(
        "تصویر صفحه حتی با کیفیت پایین هم زیر ۱ مگابایت نشد؛ پلن رایگان OCR.space آن را نمی‌پذیرد."
    )


def ocr_page(page, language: str = "eng", dpi: int = 200, key: str | None = None) -> list[dict]:
    """OCR one PyMuPDF page and return layout blocks in PDF points."""
    code = language_codes(language)
    data, width = _render_page(page, dpi=dpi)
    parsed = ocr_image_bytes(data, language=code, key=key)
    blocks = build_blocks(parsed, page_width=page.rect.width, image_width=width)
    # A busy manga page sometimes comes back empty from the free engine; a
    # smaller render is accepted more often and still reads the bubble text.
    for lower in (150, 120):
        if blocks or lower >= dpi:
            break
        data, width = _render_page(page, dpi=lower)
        parsed = ocr_image_bytes(data, language=code, key=key)
        blocks = build_blocks(parsed, page_width=page.rect.width, image_width=width)
    return blocks


def ocr_document_blocks(
    input_pdf: str | os.PathLike,
    language: str | list[str] | None = None,
    dpi: int = 200,
    key: str | None = None,
    max_pages: int | None = None,
    progress: "callable | None" = None,
) -> tuple[dict[int, list[dict]], dict]:
    """OCR every page of a PDF through OCR.space.

    Returns ``({page_index: blocks}, meta)`` with 0-based page indexes.
    """
    import pymupdf

    code = language_codes(language)
    blocks: dict[int, list[dict]] = {}
    meta = {"engine": "ocr.space", "language": code, "pages": 0, "requests": 0, "chars": 0}
    with pymupdf.open(input_pdf) as doc:
        total = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        for index in range(total):
            if progress:
                progress(index, total)
            page_blocks = ocr_page(doc[index], language=code, dpi=dpi, key=key)
            blocks[index] = page_blocks
            meta["pages"] += 1
            meta["requests"] += 1
            meta["chars"] += sum(len(b["text"]) for b in page_blocks)
    if not any(blocks.values()):
        raise OcrSpaceError(
            "OCR.space هیچ متنی برنگرداند؛ زبان یا کیفیت تصویر را بررسی کنید."
        )
    return blocks, meta
