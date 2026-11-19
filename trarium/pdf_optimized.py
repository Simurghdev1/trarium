"""PDF translation with optional OCR, resilient batching and layout preservation."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Callable
from dataclasses import dataclass, field

import requests
from rich.console import Console

from trarium.utils import clean_translation as _clean_translation
from trarium.pdf_layout import LayoutItem, load_font as _layout_font, plan_page as _plan_page
from trarium.page_detector import (
    CoverDetectionConfig,
    detect_pages as _detect_pages,
    summary as _page_summary,
    FA_LABELS as _PAGE_LABELS,
)

console = Console()

ATTRIBUTION_TEXT = "ترجمه شده توسط تراریوم"

@dataclass
class PDFTranslationStats:
    total_blocks: int = 0
    translated_blocks: int = 0
    failed_blocks: int = 0
    write_failures: int = 0
    pages_with_text: int = 0
    pages_written: int = 0
    source_chars: int = 0
    translated_chars: int = 0
    first_write_error: str = ""
    first_translation_error: str = ""
    cache_hits: int = 0
    checkpoint_hits: int = 0
    api_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    protected_spans: int = 0
    ocr_pdf_path: str = ""
    reviewed_blocks: int = 0
    skipped_artwork: int = 0
    skipped_noise_pages: int = 0
    qc_issues: list = field(default_factory=list)
    qc_ok: bool = True
    style: str = ""
    translation_issues: int = 0
    translation_issue_kinds: dict = field(default_factory=dict)
    repaired_blocks: int = 0
    layout_moved: int = 0
    layout_shrunk: int = 0
    layout_overflow: int = 0
    page_decisions: list = field(default_factory=list)
    page_status_counts: dict = field(default_factory=dict)
    detected_exempt_pages: list = field(default_factory=list)
_FONT_DIR = Path(__file__).parent / "fonts"
if not _FONT_DIR.exists():
    _FONT_DIR = Path(__file__).parent.parent / "trarium" / "fonts"
_FONT_PATH = _FONT_DIR / "Sahel.ttf"

# Vision OCR tokens scale with image pixels, not file size. A 300-dpi PNG of an
# A4 page is ~8.7 MP; the same page at 180 dpi capped to 1700 px is ~2 MP, which
# keeps every glyph readable at a fraction of the token cost.
OCR_DPI = 180
OCR_JPEG_QUALITY = 82
OCR_MAX_SIDE = 1700

_PADDLE_ENGINES: dict[str, object] = {}
_SURYA_ENGINE = None

OPENROUTER_VISION_MODELS = {
    "Auto (رایگان)": "openrouter/free",
    "Nemotron Nano 12B 2 VL (OCR)": "nvidia/nemotron-nano-12b-v2-vl:free",
    "Nemotron 3 Nano Omni (جدیدتر)": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "Mistral Small 3.1 24B": "mistralai/mistral-small-3.1-24b-instruct:free",
}


def _has_paddleocr() -> bool:
    try:
        import paddleocr  # noqa: F401
        return True
    except Exception:
        return False


def _has_surya() -> bool:
    try:
        import surya  # noqa: F401
        return True
    except Exception:
        return False


def _has_easyocr() -> bool:
    try:
        import easyocr  # noqa: F401
        return True
    except Exception:
        return False


def _is_tesseract_installed() -> bool:
    try:
        from trarium.install_tesseract import _find_tesseract
        return _find_tesseract() is not None
    except Exception:
        return False


def _get_paddle_engine(lang: str):
    """Create/reuse PaddleOCR for both legacy 2.x and current 3.x APIs."""
    import inspect
    import logging
    import paddleocr as paddle_pkg
    from paddleocr import PaddleOCR

    key = lang or "en"
    if key in _PADDLE_ENGINES:
        return _PADDLE_ENGINES[key]

    logging.getLogger("paddleocr").setLevel(logging.ERROR)
    version_text = str(getattr(paddle_pkg, "__version__", "3.0"))
    match = re.match(r"(\d+)\.(\d+)", version_text)
    version = (int(match.group(1)), int(match.group(2))) if match else (3, 0)

    if version >= (3, 0):
        # These are deliberately disabled for clean book pages to reduce CPU time.
        engine = PaddleOCR(
            lang=key,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    else:
        kwargs = {"lang": key, "use_angle_cls": True}
        # Do not pass show_log: it broke on newer PaddleOCR versions.
        try:
            params = inspect.signature(PaddleOCR).parameters
            kwargs = {k: v for k, v in kwargs.items() if k in params}
        except Exception:
            pass
        engine = PaddleOCR(**kwargs)

    _PADDLE_ENGINES[key] = engine
    return engine


def _paddle_language(lang: str) -> str:
    first = re.split(r"[+,]", lang or "eng")[0].strip().lower()
    return {
        "fas": "fa", "persian": "fa", "fa": "fa",
        "ara": "ar", "arabic": "ar", "ar": "ar",
        "eng": "en", "english": "en", "en": "en",
        "jpn": "japan", "japanese": "japan", "ja": "japan",
        "chi_sim": "ch", "ch": "ch",
    }.get(first, first or "en")


def _pixmap_to_rgb_numpy(page, dpi: int = 300):
    import numpy as np

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    if pix.n == 1:
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
    else:
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    return image, pix


def _normalize_box(box):
    if hasattr(box, "tolist"):
        box = box.tolist()
    if not box:
        return None
    try:
        if len(box) == 4 and not isinstance(box[0], (list, tuple)):
            x0, y0, x1, y1 = map(float, box)
            return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        if len(box) >= 4:
            return [(float(p[0]), float(p[1])) for p in box[:4]]
    except (TypeError, ValueError, IndexError):
        return None
    return None


def _blocks_from_paddle_result(result, scale: float) -> list[dict]:
    blocks: list[dict] = []

    def add(box, text, score):
        text = str(text or "").strip()
        try:
            score = float(score)
        except (TypeError, ValueError):
            return
        points = _normalize_box(box)
        if len(text) < 2 or score < 0.35 or not points:
            return
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x0, x1 = min(xs) / scale, max(xs) / scale
        y0, y1 = min(ys) / scale, max(ys) / scale
        if x1 <= x0 or y1 <= y0:
            return
        blocks.append({
            "bbox": (x0, y0, x1, y1),
            "text": text,
            "font_size": max((y1 - y0) * 0.75, 8),
            "score": score,
        })

    for item in result or []:
        rec_texts = getattr(item, "rec_texts", None)
        rec_scores = getattr(item, "rec_scores", None)
        rec_boxes = getattr(item, "rec_boxes", None)
        rec_polys = getattr(item, "rec_polys", None)
        if isinstance(item, dict):
            rec_texts = item.get("rec_texts", rec_texts)
            rec_scores = item.get("rec_scores", rec_scores)
            rec_boxes = item.get("rec_boxes", rec_boxes)
            rec_polys = item.get("rec_polys", rec_polys)

        if rec_texts is not None and rec_scores is not None and (rec_boxes is not None or rec_polys is not None):
            boxes = rec_boxes if rec_boxes is not None else rec_polys
            for box, text, score in zip(boxes, rec_texts, rec_scores):
                add(box, text, score)
            continue

        # PaddleOCR 2.x result: [[[points], (text, score)], ...]
        if isinstance(item, (list, tuple)):
            for line in item:
                try:
                    box, pair = line
                    text, score = pair
                except (TypeError, ValueError):
                    continue
                add(box, text, score)

    blocks.sort(key=lambda b: (round(b["bbox"][1] / 4) * 4, b["bbox"][0]))
    return blocks


def extract_ocr_blocks_paddleocr(page, lang: str = "eng") -> list[dict]:
    """Run PaddleOCR locally and return blocks in PDF-point coordinates."""
    if not _has_paddleocr():
        raise RuntimeError("PaddleOCR نصب نیست. برای OCR اختیاری نصبش کنید.")

    import paddleocr as paddle_pkg

    image, _ = _pixmap_to_rgb_numpy(page, dpi=300)
    engine = _get_paddle_engine(_paddle_language(lang))
    scale = 300 / 72

    version_text = str(getattr(paddle_pkg, "__version__", "3.0"))
    match = re.match(r"(\d+)\.(\d+)", version_text)
    version = (int(match.group(1)), int(match.group(2))) if match else (3, 0)

    if version >= (3, 0) and hasattr(engine, "predict"):
        result = engine.predict(image)
    else:
        result = engine.ocr(image, cls=True) if hasattr(engine, "ocr") else engine.predict(image)
    return _blocks_from_paddle_result(result, scale)


def extract_ocr_blocks_tesseract(page, lang: str = "eng") -> list[dict]:
    from trarium.install_tesseract import _find_tesseract
    tess_path = _find_tesseract()
    if not tess_path:
        raise RuntimeError("Tesseract پیدا نشد.")
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("برای Tesseract به pytesseract و Pillow نیاز است.") from exc

    pix = page.get_pixmap(dpi=300, alpha=False)
    image = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)
    try:
        pytesseract.pytesseract.tesseract_cmd = tess_path
        data = pytesseract.image_to_data(image, lang=lang, config="--psm 6", output_type=pytesseract.Output.DICT)
    finally:
        image.close()

    scale = 300 / 72
    lines: dict[tuple[int, int, int, int], dict] = {}
    for i, word in enumerate(data.get("text", [])):
        word = str(word or "").strip()
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1
        if len(word) < 1 or conf < 45:
            continue
        key = (
            int(data["block_num"][i]),
            int(data["par_num"][i]),
            int(data["line_num"][i]),
            int(data["page_num"][i]),
        )
        x = int(data["left"][i]) / scale
        y = int(data["top"][i]) / scale
        w = int(data["width"][i]) / scale
        h = int(data["height"][i]) / scale
        item = lines.setdefault(key, {"words": [], "bbox": [x, y, x + w, y + h], "h": h})
        item["words"].append(word)
        item["bbox"] = [
            min(item["bbox"][0], x), min(item["bbox"][1], y),
            max(item["bbox"][2], x + w), max(item["bbox"][3], y + h),
        ]
        item["h"] = max(item["h"], h)

    blocks = []
    for item in lines.values():
        text = " ".join(item["words"]).strip()
        if len(text) < 2:
            continue
        x0, y0, x1, y1 = item["bbox"]
        blocks.append({"bbox": (x0, y0, x1, y1), "text": text, "font_size": max(item["h"] * 0.75, 8)})
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
    return blocks


def extract_ocr_blocks_easyocr(page, languages=None) -> list[dict]:
    try:
        import easyocr
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("EasyOCR نصب نیست.") from exc
    languages = languages or ["en"]
    import numpy as np

    reader = easyocr.Reader(languages, gpu=False, verbose=False)
    pix = page.get_pixmap(dpi=300, alpha=False)
    # easyocr rejects PIL images ("Invalid input type"); it needs a numpy array,
    # bytes or a path. Passing the PIL image made the whole engine unusable.
    array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    try:
        results = reader.readtext(array, detail=1, paragraph=False)
    finally:
        array = None
    scale = 300 / 72
    blocks = []
    for box, text, score in results:
        if float(score) < 0.35 or not str(text).strip():
            continue
        pts = _normalize_box(box)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        blocks.append({
            "bbox": (min(xs) / scale, min(ys) / scale, max(xs) / scale, max(ys) / scale),
            "text": str(text).strip(),
            "font_size": max((max(ys) - min(ys)) / scale * 0.75, 8),
            "score": float(score),
        })
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
    return blocks


def _get_surya_engine():
    global _SURYA_ENGINE
    if _SURYA_ENGINE is None:
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor
        foundation = FoundationPredictor()
        _SURYA_ENGINE = RecognitionPredictor(foundation)
    return _SURYA_ENGINE


def extract_ocr_blocks_surya(page, lang: str = "eng") -> list[dict]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow نصب نیست.") from exc
    try:
        from surya.detection import DetectionPredictor
    except ImportError:
        DetectionPredictor = None

    pix = page.get_pixmap(dpi=300, alpha=False)
    image = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)
    try:
        recognition = _get_surya_engine()
        langs = []
        for raw in re.split(r"[+,]", lang or "eng"):
            raw = raw.strip().lower()
            langs.append("fa" if raw in {"fas", "fa", "persian"} else "en" if raw in {"eng", "en", "english"} else raw)
        langs = [x for x in langs if x] or ["en"]
        # Surya API differs by release; keep this optional and fail clearly.
        if DetectionPredictor is None:
            predictions = recognition([image], [langs])
        else:
            detection = DetectionPredictor()
            det = detection([image])
            predictions = recognition([image], [langs], det)
    finally:
        image.close()

    scale = 300 / 72
    pred = predictions[0] if predictions else None
    blocks = []
    for line in getattr(pred, "text_lines", []) or []:
        text = str(getattr(line, "text", "") or "").strip()
        bbox = getattr(line, "bbox", None)
        if len(text) < 2 or not bbox or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = map(float, bbox)
        blocks.append({
            "bbox": (x0 / scale, y0 / scale, x1 / scale, y1 / scale),
            "text": text,
            "font_size": max((y1 - y0) / scale * 0.75, 8),
        })
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
    return blocks


def _vision_prompt(multi: bool = False, page_nums: list[int] | None = None) -> str:
    if not multi:
        return (
            "Extract all readable text from this scanned book page. Return ONLY a JSON array. "
            'Each item must be {"text":"...","bbox":[x0,y0,x1,y1]}. '
            "bbox is normalized left,top,right,bottom from 0 to 1. Group text by paragraph/line, preserve reading order, and keep original language."
        )
    return (
        f"Extract all readable text from these {len(page_nums or [])} scanned book pages. Return ONLY a JSON object. "
        f"Use these exact page keys: {page_nums}. Each value is a JSON array of items with "
        '{"text":"...","bbox":[x0,y0,x1,y1]}. '
        "bbox is normalized left,top,right,bottom from 0 to 1. Preserve reading order and original language."
    )


def _vision_image_data_url(page, dpi: int = OCR_DPI) -> str:
    """Render one page for vision OCR as a compact JPEG data URL."""
    import pymupdf

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    longest = max(pix.width, pix.height)
    if longest > OCR_MAX_SIDE:
        zoom = (OCR_MAX_SIDE / longest) * (dpi / 72.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    try:
        blob = pix.tobytes("jpeg", jpg_quality=OCR_JPEG_QUALITY)
        mime = "image/jpeg"
    except Exception:
        blob = pix.tobytes("png")
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(blob).decode()}"


def _vision_image_size(page, dpi: int = OCR_DPI) -> tuple[int, int]:
    """Pixel size of the image actually sent for OCR (mirrors _vision_image_data_url)."""
    width = page.rect.width / 72.0 * dpi
    height = page.rect.height / 72.0 * dpi
    longest = max(width, height)
    if longest > OCR_MAX_SIDE:
        scale = OCR_MAX_SIDE / longest
        width, height = width * scale, height * scale
    return max(1, int(round(width))), max(1, int(round(height)))


def _openrouter_vision_request(api_key: str, model_name: str, image_data_urls: list[str], prompt: str, num_pages: int = 1) -> str:
    from trarium.translators.openrouter_http import post_json
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8501",
        "X-OpenRouter-Title": "Trarium OCR",
    }
    content = [{"type": "text", "text": prompt}]
    for data_url in image_data_urls:
        content.append({"type": "image_url", "image_url": {"url": data_url}})
    # Reasoning models spend the whole budget on hidden reasoning and then return
    # an empty content field, so a 1800-token cap produced "no text" on real
    # pages. Start higher and retry once with double the room.
    budget = max(4096, min(16000, 4096 * max(1, num_pages)))
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.0,
        "max_tokens": budget,
    }
    last_reason = "پاسخ خالی"
    for attempt in range(2):
        response = post_json("https://openrouter.ai/api/v1/chat/completions", headers, payload, timeout=240)
        data = response.data
        choices = (data.get("choices") if isinstance(data, dict) else None) or []
        text = None
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            text = message.get("content")
            last_reason = str(choices[0].get("finish_reason") or "")
            if not text and message.get("reasoning"):
                last_reason = f"reasoning-only ({last_reason or 'stop'})"
        if text:
            return str(text).strip()
        if attempt == 0:
            payload["max_tokens"] = min(24000, budget * 2)
    raise RuntimeError(f"مدل Vision متن برنگرداند ({last_reason}).")


def _parse_vision_json(text: str):
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        starts = [i for i in (cleaned.find("["), cleaned.find("{")) if i >= 0]
        if not starts:
            raise
        start = min(starts)
        end = max(cleaned.rfind("]"), cleaned.rfind("}"))
        if end <= start:
            raise
        return json.loads(cleaned[start:end + 1])


def _bbox_to_blocks(items, pw: float, ph: float, img_w: float | None = None, img_h: float | None = None) -> list[dict]:
    blocks = []
    if not isinstance(items, list):
        return blocks
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "") or "").strip()
        bbox = item.get("bbox")
        if len(text) < 2 or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        try:
            raw = [float(v) for v in bbox]
        except (TypeError, ValueError):
            continue
        if max(abs(v) for v in raw) > 1.5:
            # Several vision models answer in image pixels even though the prompt
            # asks for 0..1 boxes. Clamping those values to 1.0 stacked every
            # block in one corner of the page, so rescale against the image.
            scale_x = (img_w or pw) or 1.0
            scale_y = (img_h or ph) or 1.0
            raw = [raw[0] / scale_x, raw[1] / scale_y, raw[2] / scale_x, raw[3] / scale_y]
        x0, y0, x1, y1 = [max(0.0, min(1.0, v)) for v in raw]
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if x1 <= x0 or y1 <= y0:
            continue
        blocks.append({
            "bbox": (x0 * pw, y0 * ph, x1 * pw, y1 * ph),
            "text": text,
            "font_size": max((y1 - y0) * ph * 0.25, 8),
        })
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
    return blocks


def extract_ocr_blocks_openrouter(page, api_key: str | None = None, model_name: str | None = None) -> list[dict]:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OpenRouter API Key برای Vision OCR وارد نشده است.")
    model_name = model_name or "openrouter/free"
    response = _openrouter_vision_request(api_key, model_name, [_vision_image_data_url(page)], _vision_prompt())
    img_w, img_h = _vision_image_size(page)
    return _bbox_to_blocks(_parse_vision_json(response), page.rect.width, page.rect.height, img_w, img_h)


def extract_ocr_blocks_batch_openrouter(pages: list[tuple[int, object]], api_key: str | None = None,
                                       model_name: str | None = None, batch_size: int = 3) -> dict[int, list[dict]]:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OpenRouter API Key برای Vision OCR وارد نشده است.")
    model_name = model_name or "openrouter/free"
    batch_size = max(1, min(int(batch_size), 6))
    result: dict[int, list[dict]] = {}

    for start in range(0, len(pages), batch_size):
        batch = pages[start:start + batch_size]
        nums = [n for n, _ in batch]
        images = [_vision_image_data_url(page) for _, page in batch]
        try:
            response = _openrouter_vision_request(api_key, model_name, images, _vision_prompt(len(batch) > 1, nums), len(batch))
            parsed = _parse_vision_json(response)
            if len(batch) == 1:
                img_w, img_h = _vision_image_size(batch[0][1])
                result[nums[0]] = _bbox_to_blocks(parsed, batch[0][1].rect.width, batch[0][1].rect.height, img_w, img_h)
            else:
                for i, num in enumerate(nums):
                    items = parsed.get(str(num), []) if isinstance(parsed, dict) else []
                    if not items and isinstance(parsed, dict):
                        items = parsed.get(str(i + 1), [])
                    img_w, img_h = _vision_image_size(batch[i][1])
                    result[num] = _bbox_to_blocks(items, batch[i][1].rect.width, batch[i][1].rect.height, img_w, img_h)
        except Exception as exc:
            console.print(f"  ⚠️ Vision OCR batch {nums}: {exc}")
            for num in nums:
                result[num] = []
    return result


def extract_ocr_blocks_pdf24(page, lang: str = "eng") -> list[dict]:
    """OCR a single page object through the PDF24 command-line tool.

    PDF24 only accepts whole files, so the page is written to a temporary PDF
    first; the caller's document is untouched either way.
    """
    import pymupdf

    from trarium.ocr_pdf24 import ocr_document_blocks

    tmp = pymupdf.open()
    tmp.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    with tempfile.TemporaryDirectory(prefix="trarium-pdf24-page-") as workdir:
        single = Path(workdir) / "page.pdf"
        tmp.save(str(single))
        tmp.close()
        blocks, _ocr_path = ocr_document_blocks(single, language=lang, keep_dir=workdir)
    return list(blocks.get(0, []))


def extract_ocr_blocks(page, lang: str = "eng", api_key: str | None = None,
                       vision_model: str | None = None, ocr_method: str = "auto") -> list[dict]:
    """OCR one page with an explicit engine or sensible auto fallback."""
    methods = [ocr_method] if ocr_method != "auto" else ["paddleocr", "surya", "easyocr", "openrouter"]
    for method in methods:
        try:
            if method == "paddleocr" and _has_paddleocr():
                blocks = extract_ocr_blocks_paddleocr(page, lang=lang)
            elif method == "surya" and _has_surya():
                blocks = extract_ocr_blocks_surya(page, lang=lang)
            elif method == "easyocr" and _has_easyocr():
                easy_langs = ["en"] if "fas" not in lang and "fa" not in lang else ["fa", "en"]
                blocks = extract_ocr_blocks_easyocr(page, easy_langs)
            elif method == "openrouter" and api_key:
                blocks = extract_ocr_blocks_openrouter(page, api_key, vision_model)
            elif method == "tesseract" and _is_tesseract_installed():
                blocks = extract_ocr_blocks_tesseract(page, lang=lang)
            elif method == "pdf24":
                blocks = extract_ocr_blocks_pdf24(page, lang=lang)
            elif method == "ocrspace":
                blocks = extract_ocr_blocks_ocrspace(page, lang=lang)
            else:
                continue
            if blocks:
                return blocks
        except Exception as exc:
            console.print(f"  ⚠️ {method}: {exc}")
    raise RuntimeError("OCR با روش انتخاب‌شده موفق نشد.")


def extract_ocr_blocks_ocrspace(page, lang: str = "eng", key: str | None = None) -> list[dict]:
    """Per-page OCR through OCR.space (free tier only)."""
    from trarium.ocr_space import ocr_page

    return ocr_page(page, language=lang, key=key)


def _is_garbled(text: str) -> bool:
    if not text or len(text.strip()) < 2:
        return True
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 12:
        latin = sum(c.isascii() and c.isalpha() for c in letters)
        arabic = sum("\u0600" <= c <= "\u06ff" for c in letters)
        if (latin + arabic) / len(letters) < 0.65:
            return True
    return False


def _looks_garbled(text: str) -> bool:
    """True for OCR noise such as "zZzZZzzz TZZJ wazzr jypn".

    Ornamental borders and decorative lettering are scanned with a text layer that
    is usually meaningless; translating it prints nonsense on top of the art, so
    it is left exactly as it is.
    """
    words = re.findall(r"[A-Za-z]{2,}", str(text or ""))
    if len(words) < 4:
        return False
    vowel_less = [w for w in words if not re.search(r"[aeiouyAEIOUY]", w)]
    return len(vowel_less) / len(words) > 0.5


def _box_tuple(value) -> tuple[float, float, float, float]:
    try:
        x0, y0, x1, y1 = [float(v) for v in value]
        return x0, y0, x1, y1
    except Exception:
        return 0.0, 0.0, 0.0, 0.0


def _normalise_layout_blocks(blocks: list[dict], page_rect=None) -> list[dict]:
    """Normalize OCR/native blocks and merge adjacent OCR lines into paragraph-sized blocks."""
    normal = []
    for b in blocks or []:
        text = str(b.get("text", "") or "").strip()
        if not text:
            continue
        box = _box_tuple(b.get("bbox", (0, 0, 0, 0)))
        x0, y0, x1, y1 = box
        if x1 <= x0 or y1 <= y0:
            continue
        item = dict(b)
        item["bbox"] = box
        item["text"] = text
        item["font_size"] = float(b.get("font_size") or max(8.0, (y1-y0)*0.75))
        normal.append(item)
    if not normal:
        return []

    # Merge line-level OCR boxes into paragraphs. Native PDF blocks are usually already paragraphs,
    # but the same helper is safe for both cases and dramatically reduces API calls for OCR PDFs.
    heights = sorted(max(1.0, b["bbox"][3]-b["bbox"][1]) for b in normal)
    median_h = heights[len(heights)//2] if heights else 12.0
    max_gap = max(5.0, median_h * 1.65)
    normal.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))

    merged=[]
    for item in normal:
        x0,y0,x1,y1=item["bbox"]
        if not merged:
            merged.append(item); continue
        prev=merged[-1]
        px0,py0,px1,py1=prev["bbox"]
        gap=y0-py1
        overlap=max(0.0, min(x1,px1)-max(x0,px0))
        min_width=max(1.0,min(x1-x0,px1-px0))
        horizontal_overlap=overlap/min_width
        same_column = horizontal_overlap >= 0.20 or abs(x0-px0) <= max(18.0, median_h*2.5)
        # Avoid swallowing titles / footers separated by a large vertical gap.
        # A block that already carries its own line boxes (OCR.space) is a finished
        # paragraph; merging it again would throw those boxes away.
        if 0 <= gap <= max_gap and same_column and not prev.get("lines"):
            prev["text"] = (prev["text"] + " " + item["text"]).strip()
            prev["bbox"] = (min(px0,x0), min(py0,y0), max(px1,x1), max(py1,y1))
            prev["font_size"] = (prev.get("font_size", 11.0) + item.get("font_size", 11.0))/2
        else:
            merged.append(item)
    return merged


def _word_blocks_fallback(page) -> list[dict]:
    """Fallback extractor for OCR text layers whose dict blocks are absent/oddly encoded."""
    words = page.get_text("words") or []
    if not words:
        return []
    grouped = {}
    for w in words:
        if len(w) < 7:
            continue
        x0,y0,x1,y1,text,block_no,line_no,*rest = w
        text=str(text or "").strip()
        if not text:
            continue
        key=(int(block_no), int(line_no))
        grouped.setdefault(key, []).append((float(x0),float(y0),float(x1),float(y1),text))
    lines=[]
    for items in grouped.values():
        items.sort(key=lambda x:x[0])
        lines.append({
            "bbox":(min(x[0] for x in items),min(x[1] for x in items),max(x[2] for x in items),max(x[3] for x in items)),
            "text":" ".join(x[4] for x in items).strip(),
            "font_size":max(8.0, max(x[3]-x[1] for x in items)*0.75),
        })
    return _normalise_layout_blocks(lines, page.rect)


def extract_text_blocks(page, strict: bool = False) -> list[dict]:
    """Extract native/OCR text-layer blocks. Falls back to words for OCR-generated PDFs."""
    import pymupdf
    data = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    blocks=[]
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        text_parts=[]; sizes=[]
        for line in block.get("lines", []):
            parts=[]
            for span in line.get("spans", []):
                value=str(span.get("text", "") or "")
                if value:
                    parts.append(value)
                try: sizes.append(float(span.get("size",12)))
                except Exception: pass
            line_text="".join(parts).strip()
            if line_text: text_parts.append(line_text)
        text=" ".join(text_parts).strip()
        if not text: continue
        if strict and _is_garbled(text): continue
        blocks.append({
            "bbox": tuple(block.get("bbox", (0,0,0,0))),
            "text": text,
            "font_size": sum(sizes)/len(sizes) if sizes else 12,
        })
    if blocks:
        # Native blocks are generally paragraph-level; do not aggressively merge named blocks.
        blocks.sort(key=lambda b:(b["bbox"][1],b["bbox"][0]))
        return blocks
    return _word_blocks_fallback(page)

def _reshape(text: str) -> str:
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        try:
            from bidi.algorithm import get_display
            return get_display(text)
        except Exception:
            return text


def _expand_rect(rect, page_rect, text: str, original_size: float) -> object:
    import pymupdf
    r=pymupdf.Rect(rect)
    # OCR often reports the height of a single source line; translated Persian can need 2-4 lines.
    # Keep the box as tight as the translation allows: every extra point here is
    # visible paper that gets painted over.
    extra_h=max(4.0, min(page_rect.height*0.06, len(text.split())*0.16 + r.height*0.45))
    xpad=min(3.0, max(0.8, r.width*0.008))
    candidate=pymupdf.Rect(max(page_rect.x0+2,r.x0-xpad), max(page_rect.y0+1,r.y0-extra_h*0.12),
                           min(page_rect.x1-2,r.x1+xpad), min(page_rect.y1-1,r.y1+extra_h))
    return candidate


def _htmlbox_css(font_size: float, target_lang: str, line_height: float = 1.18) -> str:
    rtl=target_lang in {"fa","ar","he","ur"}
    align="right" if rtl else "left"
    direction="rtl" if rtl else "ltr"
    # ``unicode-bidi: plaintext`` lets each run pick its own direction from its
    # first strong character, so an English-only legend entry stays LTR while a
    # Persian sentence stays RTL, and mixed lines keep numbers/parentheses in order.
    return (f"@font-face{{font-family:Sahel;src:url('Sahel.ttf');}} *{{font-family:Sahel;"
            f"font-size:{font_size:.2f}pt;line-height:{line_height:.2f};}}"
            f" body{{margin:0;padding:0;direction:{direction};text-align:{align};"
            f"unicode-bidi:plaintext;white-space:normal;}}")


def _preflight_htmlbox(rect, text: str, fontsize: float, target_lang: str, page_rect) -> tuple[bool,float]:
    """Dry-run insertion on an in-memory page so we never redact source text before knowing the translation fits."""
    import pymupdf, html as html_mod
    tmp=pymupdf.open()
    try:
        pg=tmp.new_page(width=page_rect.width,height=page_rect.height)
        arch=pymupdf.Archive(str(_FONT_DIR))
        for scale_low in (0.45,0.30,0.20):
            spare,scale=pg.insert_htmlbox(rect, f"<body>{html_mod.escape(text).replace(chr(10),'<br>')}</body>",
                                          css=_htmlbox_css(fontsize,target_lang), archive=arch, scale_low=scale_low)
            if spare >= 0:
                return True,float(scale or 1.0)
        return False,0.0
    except Exception:
        return False,0.0
    finally:
        tmp.close()


def _sample_background(page, rect) -> tuple[float, float, float]:
    """Median colour of the paper under a text box.

    A pure white fill is wrong on tinted or scanned paper: it stamps a visible
    white rectangle around every translated sentence. The median of the pixels
    around the box is the paper colour, because text glyphs are a minority.
    """
    import pymupdf

    try:
        clip = pymupdf.Rect(rect)
        clip.x0 = max(page.rect.x0, clip.x0 - 8)
        clip.x1 = min(page.rect.x1, clip.x1 + 8)
        clip.y0 = max(page.rect.y0, clip.y0 - 4)
        clip.y1 = min(page.rect.y1, clip.y1 + 4)
        if clip.width < 2 or clip.height < 2:
            return (1.0, 1.0, 1.0)
        pix = page.get_pixmap(clip=clip, dpi=36)
        n = pix.n
        width, height = pix.width, pix.height
        # Only the ring of paper around the text is used: pixels inside the box
        # are glyphs, and they would darken the sample.
        margin = max(2, int(round(6 * 36 / 72)))
        channels = ([], [], [])
        for y in range(0, height, 2):
            edge_row = y < margin or y >= height - margin
            for x in range(0, width, 2):
                if not edge_row and margin <= x < width - margin:
                    continue
                offset = (y * width + x) * n
                for c in range(3):
                    channels[c].append(pix.samples[offset + c])
        if not channels[0]:
            return (1.0, 1.0, 1.0)
        values = []
        for channel in channels:
            channel.sort()
            values.append(channel[len(channel) // 2] / 255.0)
        return tuple(values)
    except Exception:
        return (1.0, 1.0, 1.0)


def _paper_bands(page, rect, band: float = 6.0) -> list[tuple]:
    """Paper colour just left and right of a text box, one sample per thin band.

    Returns (y0, y1, spread, paper, colour) per band: ``spread`` is how far the
    left and right paper samples disagree (0-255), ``paper`` the colour without
    ink pixels, ``colour`` the fill used for the cover.
    """
    import pymupdf

    box = pymupdf.Rect(rect)
    if box.is_empty:
        return []
    clip = pymupdf.Rect(max(page.rect.x0, box.x0 - 14), max(page.rect.y0, box.y0 - 4),
                        min(page.rect.x1, box.x1 + 14), min(page.rect.y1, box.y1 + 4))
    dpi = 36
    try:
        pix = page.get_pixmap(clip=clip, dpi=dpi)
    except Exception:
        return []
    scale = dpi / 72.0
    width, height, n = pix.width, pix.height, pix.n
    margin = max(2, int(round(6 * scale)))
    left_x0, left_x1 = margin, max(margin + 2, int(round(8 * scale)))
    right_x1 = width - margin
    right_x0 = max(left_x1 + 1, right_x1 - max(2, int(round(8 * scale))))

    def _median(row):
        return tuple(sorted(channel)[len(row) // 2] for channel in zip(*row))

    bands = []
    steps = max(1, int(round(box.height / band)))
    for step in range(steps):
        y0 = box.y0 + step * band
        y1 = min(box.y1, y0 + band)
        if y1 <= y0:
            break
        py = int(round((y0 - clip.y0) * scale)) + 1
        if py < 1 or py >= height - 1:
            continue
        left, right = [], []
        for x in range(left_x0, min(left_x1, width)):
            offset = (py * width + x) * n
            left.append((pix.samples[offset], pix.samples[offset + 1], pix.samples[offset + 2]))
        for x in range(max(0, right_x0), right_x1):
            offset = (py * width + x) * n
            right.append((pix.samples[offset], pix.samples[offset + 1], pix.samples[offset + 2]))
        row = left + right
        if not row:
            continue
        # Ink (black letters, outlines) is ignored when judging the paper: a
        # neighbouring line of bold text must not make plain paper look busy.
        left_p = [px for px in left if max(px) > 90]
        right_p = [px for px in right if max(px) > 90]
        spread = 0
        if left_p and right_p:
            ml, mr = _median(left_p), _median(right_p)
            spread = max(abs(ml[i] - mr[i]) for i in range(3))
        paper = left_p + right_p
        bands.append((y0, y1, spread, _median(paper) if paper else None, _median(row)))
    return bands


def _light_share(page, rect) -> float:
    """Share of near-white, colourless pixels inside a box (paper or bubble)."""
    import pymupdf

    try:
        pix = page.get_pixmap(clip=pymupdf.Rect(rect) & page.rect, dpi=72)
    except Exception:
        return 1.0
    n, samples = pix.n, pix.samples
    total = light = 0
    for k in range(0, len(samples) - 2, n):
        r, g, b = samples[k], samples[k + 1], samples[k + 2]
        total += 1
        if min(r, g, b) >= 200 and max(r, g, b) - min(r, g, b) < 40:
            light += 1
    return light / total if total else 1.0


def _is_plain_paper(page, rect, tolerance: int = 60) -> bool:
    """True when a box sits on plain paper or inside a bubble, not on artwork.

    Covering a box band by band only works on paper: on an illustration every
    band takes a different colour and the cover turns into coloured stripes
    across the drawing (Josep Moore, pages 10-12). Such a box is left exactly
    as it was. A box whose surroundings look busy but whose inside is mostly
    white (a bubble drawn next to a picture) is still plain.
    """
    bands = _paper_bands(page, rect)
    if not bands:
        return True
    busy = False
    # One band hitting an anti-aliased letter edge is noise; artwork disagrees
    # on many bands at once.
    bad = sum(1 for b in bands if b[2] > tolerance)
    if bad >= max(2, 0.34 * len(bands)):
        busy = True
    colours = [b[3] for b in bands if b[3] is not None]
    if not busy and len(colours) >= 3:
        lo_i, hi_i = int(len(colours) * 0.15), int(len(colours) * 0.85)
        for c in range(3):
            values = sorted(col[c] for col in colours)
            if values[min(hi_i, len(values) - 1)] - values[lo_i] > tolerance:
                busy = True
                break
    if not busy:
        return True
    return _light_share(page, rect) >= 0.55


def _fill_paper(page, rect, band: float = 6.0) -> None:
    """Cover a text box with the paper it sits on, band by band.

    One flat rectangle shows its own edge on a gradient page, so each thin band
    takes the colour of the paper just outside the text at that height.
    """
    import pymupdf

    box = pymupdf.Rect(rect)
    # Neighbouring bands of the same colour are drawn as one rectangle: on a
    # white page thirteen touching strips showed their seams as grey lines.
    runs: list[list] = []
    for y0, y1, _, _, colour in _paper_bands(page, rect, band):
        if runs and max(abs(a - b) for a, b in zip(runs[-1][2], colour)) <= 3:
            runs[-1][1] = y1
        else:
            runs.append([y0, y1, colour])
    for y0, y1, colour in runs:
        color = tuple(v / 255.0 for v in colour)
        page.draw_rect(pymupdf.Rect(box.x0, y0, box.x1, y1 + 0.4), color=None, fill=color, width=0, overlay=True)


def _close_cut_ink(page, rect, max_ratio: float = 0.9, dpi: int = 96):
    """Grow a cover box over a text line it cuts in half.

    Vision-OCR boxes are guesses: on Josep Moore p12 the title box ended in the
    middle of the next English line, so the top half of that line was covered
    and the bottom half stayed visible as a broken stripe. When the row right at
    the box edge has ink and a clean (ink-free) row is reached within
    ``max_ratio`` x box height, the box is extended to that clean row. If no clean
    row comes (artwork) or the strip is not paper, the box is left unchanged.
    """
    import pymupdf

    box = pymupdf.Rect(rect) & page.rect
    if box.is_empty or box.height <= 0:
        return box
    ext = box.height * max_ratio
    clip = pymupdf.Rect(box.x0, max(page.rect.y0, box.y0 - ext), box.x1,
                        min(page.rect.y1, box.y1 + ext))
    try:
        pix = page.get_pixmap(clip=clip, dpi=dpi)
    except Exception:
        return box
    w, h, n, sm = pix.width, pix.height, pix.n, pix.samples
    if not w or not h:
        return box
    scale = h / clip.height

    def row_ink(r: int) -> bool:
        base = r * pix.stride
        dark = 0
        for k in range(base, base + w * n - 2, n):
            if min(sm[k], sm[k + 1], sm[k + 2]) < 110:
                dark += 1
                if dark >= 2:
                    return True
        return False

    def to_row(y: float) -> int:
        return max(0, min(h - 1, int((y - clip.y0) * scale)))

    new = pymupdf.Rect(box)
    r = to_row(box.y1 + 2)
    if row_ink(r):
        while r < h - 1 and row_ink(r):
            r += 1
        if r < h - 1:
            strip = pymupdf.Rect(box.x0, box.y1, box.x1, clip.y0 + r / scale + 1)
            if _is_plain_paper(page, strip):
                new.y1 = strip.y1
    r = to_row(box.y0 - 1.5)
    if row_ink(r):
        while r > 0 and row_ink(r):
            r -= 1
        if r > 0:
            strip = pymupdf.Rect(box.x0, clip.y0 + r / scale - 1, box.x1, box.y0)
            if _is_plain_paper(page, strip):
                new.y0 = strip.y0
    return new & page.rect


def _artwork_rects(page, min_cover: float = 0.7) -> list:
    """Image boxes that are artwork, not the page's own background scan."""
    import pymupdf

    page_area = max(1.0, page.rect.width * page.rect.height)
    boxes = []
    try:
        for info in page.get_image_info():
            box = pymupdf.Rect(info.get("bbox") or (0, 0, 0, 0))
            if box.is_empty:
                continue
            if box.width * box.height / page_area >= min_cover:
                continue  # the page background itself
            boxes.append(box)
    except Exception:
        return []
    return boxes


def _has_page_image(page, min_cover: float = 0.85) -> bool:
    """True when the page is a scan: its text is printed inside a page-sized image.

    Such a page has a hidden OCR text layer, so ``get_text()`` is not empty, but
    redacting the glyphs does not remove the letters burnt into the scan.
    """
    page_area = max(1.0, page.rect.width * page.rect.height)
    try:
        for info in page.get_image_info():
            box = info.get("bbox") or (0, 0, 0, 0)
            width = max(0.0, float(box[2]) - float(box[0]))
            height = max(0.0, float(box[3]) - float(box[1]))
            if width * height / page_area >= min_cover:
                return True
    except Exception:
        return False
    return False


def _block_on_artwork(page, rect, artwork=None) -> bool:
    """True when a text box sits on an illustration.

    Painting a translation there covers the drawing and the original letters
    stay burned into the image, so the block is left exactly as it was.
    """
    import pymupdf

    box = pymupdf.Rect(rect)
    area = max(1.0, box.width * box.height)
    covers = 0.0
    for art in artwork if artwork is not None else _artwork_rects(page):
        overlap = pymupdf.Rect(art) & box
        if overlap.is_empty:
            continue
        covers += overlap.width * overlap.height
    return covers / area >= 0.4


def _needs_translation(text: str) -> bool:
    """False for labels that carry no words (percentages, axis ticks, page numbers).

    Translating or redrawing those only produced white boxes over charts, so they
    are left exactly as they are on the original page.
    """
    letters = [c for c in str(text or "") if c.isalpha()]
    return len(letters) >= 2


def _split_by_weights(text: str, weights: list[float]) -> list[str]:
    """Split a translated paragraph across the source lines, by source length."""
    words = text.split()
    if not words:
        return ["" for _ in weights]
    total = float(sum(weights)) or 1.0
    pieces, used, running = [], 0, 0.0
    for weight in weights:
        running += float(weight)
        target = round(len(words) * running / total)
        if used < len(words):
            target = max(target, used + 1)
        target = min(target, len(words))
        pieces.append(" ".join(words[used:target]))
        used = target
    if used < len(words):
        pieces[-1] = (pieces[-1] + " " + " ".join(words[used:])).strip()
    return pieces


def _write_on_lines(page, lines: list[dict], text: str, original_size: float, target_lang: str,
                    background: tuple[float, float, float] | None = None) -> tuple[bool,str]:
    """Write a paragraph back onto its own source lines.

    Each line gets its exact original box, so tables, charts and images keep
    their place and no wide white rectangle is painted over them.
    """
    import pymupdf, html as html_mod
    weights = [max(1, len(str(line.get("text", "") or "").strip())) for line in lines]
    pieces = _split_by_weights(text, weights)
    page_rect = page.rect
    archive = pymupdf.Archive(str(_FONT_DIR))
    written = 0
    for line, piece in zip(lines, pieces):
        piece = piece.strip()
        if not piece:
            continue
        box = pymupdf.Rect(line["bbox"])
        box = pymupdf.Rect(max(page_rect.x0 + 1, box.x0), max(page_rect.y0 + 1, box.y0),
                           min(page_rect.x1 - 1, box.x1), min(page_rect.y1 - 1, box.y1))
        if box.width < 4 or box.height < 3:
            continue
        size = max(6.0, min(float(original_size or 11), box.height * 0.95))
        # One line at a time: the paper has a gradient, so a per-line sample hides
        # the patch far better than one flat colour over the whole paragraph.
        line_fill = _sample_background(page, box)
        try:
            page.add_redact_annot(box, fill=line_fill or (1, 1, 1))
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
            spare, _ = page.insert_htmlbox(
                box, f"<body>{html_mod.escape(piece)}</body>",
                # A tight line box needs a tight line height, otherwise the text of
                # one line spills into the next box and the spans overlap.
                css=_htmlbox_css(size, target_lang, line_height=0.98), archive=archive, scale_low=0.35,
            )
            if spare >= 0:
                written += 1
        except Exception:
            continue
    if written:
        return True, ""
    return False, "نوشتن خط‌به‌خط روی متن اصلی جا نشد"


def _wrapped_line_count(display_text: str, size: float, width: float, font) -> int:
    lines, current = 1, 0.0
    for word in display_text.split():
        piece = font.text_length(word + " ", size)
        if current and current + piece > width:
            lines += 1
            current = piece
        else:
            current += piece
    return lines


def _grow_light_box(page, rect, vertical_limit: float = 2.2, horizontal_limit: float = 0.0):
    """Grow a scanned text box into the blank space around it.

    Manga bubbles hold one short line of English; the Persian is longer and a
    tight OCR box would shrink it to a speck. The bubble itself is blank, so the
    box may follow that blank area as long as it stays inside it.
    """
    import pymupdf

    page_rect = page.rect
    win = pymupdf.Rect(rect.x0 - 12, rect.y0 - rect.height, rect.x1 + 12, rect.y1 + rect.height) & page_rect
    if win.is_empty:
        return rect
    dpi = 72
    pix = page.get_pixmap(dpi=dpi, clip=win, colorspace=pymupdf.csGRAY, alpha=False)
    if not pix.samples:
        return rect
    scale = dpi / 72.0

    def row_light(y: float) -> bool:
        py = int((y - win.y0) * scale)
        if py < 0 or py >= pix.height:
            return False
        row = pix.samples[py * pix.stride: py * pix.stride + pix.width]
        # Any ink stops the growth: a bubble outline is one thin dark line, and a
        # mean over the row would happily step straight over it.
        return sum(row) / max(1, len(row)) > 200 and min(row) > 110

    def col_light(x: float) -> bool:
        px = int((x - win.x0) * scale)
        if px < 0 or px >= pix.width:
            return False
        col = [pix.samples[y * pix.stride + px] for y in range(0, pix.height, max(1, pix.height // 60))]
        return sum(col) / max(1, len(col)) > 200 and min(col) > 110

    top, bottom = rect.y0, rect.y1
    limit = rect.height * vertical_limit
    while bottom - top < limit and row_light(bottom + 1):
        bottom += 1
    while bottom - top < limit and row_light(top - 1):
        top -= 1
    left, right = rect.x0, rect.x1
    grew = 0.0
    while grew < rect.width * horizontal_limit and col_light(left - 1):
        left -= 1
        grew += 1
    grew = 0.0
    while grew < rect.width * horizontal_limit and col_light(right + 1):
        right += 1
        grew += 1
    grown = pymupdf.Rect(left, top, right, bottom) & page_rect
    return grown if not grown.is_empty else rect


def _centred_growth(cover, grown):
    """Keep a grown writing box centred on the source text.

    The translation is written from the top of its box. A title with blank
    paper above it grew mostly upwards, so the Persian landed at the very top
    of the page, far above the erased English (Josep Moore p12). The box may
    still grow, but no further up than it grew down (a quarter line at least).
    """
    import pymupdf

    cover = pymupdf.Rect(cover)
    grown = pymupdf.Rect(grown)
    down = max(0.0, grown.y1 - cover.y1)
    up_allowed = max(down, cover.height * 0.25)
    top = max(grown.y0, cover.y0 - up_allowed)
    out = pymupdf.Rect(grown.x0, min(top, cover.y0), grown.x1, grown.y1)
    return out if not out.is_empty else cover


def _base_size(original_size: float, size_cap: float | None) -> float:
    """The size a block is set at before anything is measured.

    Body text stays within a narrow band around the document's median size, while
    a heading keeps a visible step above it instead of being flattened.
    """
    base_size = max(7.0, min(float(original_size or 11), 18.0))
    if size_cap:
        cap = float(size_cap)
        if base_size > cap * 1.35:
            base_size = min(base_size, cap * 1.6)
        else:
            base_size = max(8.0, min(base_size, cap * 1.15))
    return base_size


def _fit_text_size(text: str, box, size: float, min_size: float = 6.0) -> float:
    """Largest size at which the translation still fits inside its own box.

    Persian runs longer than English, so keeping the source size forces the text
    to wrap into the next line's space and the lines end up on top of each other.
    """
    import pymupdf
    from trarium.pdf_layout import fit_size, load_font

    font = load_font(str(_FONT_PATH))
    if font is None:
        return size
    chosen = fit_size(str(text or ""), font, max(4.0, pymupdf.Rect(box).width - 1.5),
                      pymupdf.Rect(box).height, size, min_size)
    return chosen.size


def _review_translations(translator, entries, translated: dict, target_lang: str) -> int:
    """Ask the model to repair suspicious blocks (empty, duplicated, half-English)."""
    suspects = []
    for page_num, block_idx, source in entries:
        value = translated.get((page_num, block_idx))
        if not value:
            continue
        ratio = len(value) / max(1, len(source))
        mixed = bool(re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", value))
        if mixed or ratio < 0.2 or ratio > 3.2 or value.strip() == source.strip():
            suspects.append((page_num, block_idx, source, value))
    fixed = 0
    for page_num, block_idx, source, value in suspects[:20]:
        prompt = (
            "این قطعه از یک سند است. ترجمهٔ فارسی زیر را بررسی کن؛ اگر ناقص، نادرست یا نیمه‌انگلیسی است "
            "ترجمهٔ کامل و روان را فقط به فارسی بنویس و اگر درست است همان را بدون تغییر برگردان.\n"
            f"متن اصلی:\n{source}\n\nترجمهٔ فعلی:\n{value}"
        )
        try:
            answer = (translator.translate(prompt, target_lang) or "").strip()
        except Exception:
            continue
        if answer and answer != value and not re.search(r"[\u4e00-\u9fff]", answer):
            translated[(page_num, block_idx)] = answer
            fixed += 1
    return fixed


def _write_text(page, rect, text, original_size: float, target_lang: str, scanned: bool | None = None,
                lines: list[dict] | None = None, size_cap: float | None = None,
                background: tuple[float, float, float] | None = None,
                cover: tuple[float, float, float, float] | None = None,
                plan_rect: tuple[float, float, float, float] | None = None,
                planned_size: float | None = None,
                erase: bool = True) -> tuple[bool,str]:
    import pymupdf, html as html_mod
    if not text or not text.strip():
        return False,"متن ترجمه خالی است"
    if not _FONT_PATH.exists():
        return False,f"فونت Sahel پیدا نشد: {_FONT_PATH}"
    page_rect = page.rect
    # The box that is erased is always the box the source text occupies; the box
    # the translation is written into may be larger (a planned, measured box).
    cover_box = (pymupdf.Rect(cover) if cover is not None else pymupdf.Rect(rect)) & page_rect
    planned = (pymupdf.Rect(plan_rect) & page_rect) if plan_rect is not None else None
    # A scanned page has no text layer: redaction cannot remove glyphs that live
    # inside the page image, so the original text would stay visible under the
    # translation. There the box must stay exactly on the text, otherwise the
    # white fill spills over charts and artwork.
    if scanned is None:
        scanned = not page.get_text().strip() or _has_page_image(page)
    if scanned:
        source = pymupdf.Rect(max(page_rect.x0+1, rect[0]), max(page_rect.y0+1, rect[1]),
                              min(page_rect.x1-1, rect[2]), min(page_rect.y1-1, rect[3]))
        if source.width > 0 and source.height > 0:
            cover_box = source
        # Cover exactly the old letters, but write into the blank space around
        # them: a manga bubble holds one short English line and the Persian needs
        # more room than the OCR box gives it.
        if planned is not None and not planned.is_empty:
            inset = planned
        else:
            inset = _centred_growth(cover_box, _grow_light_box(page, cover_box))
    else:
        inset = planned if (planned is not None and not planned.is_empty) else _expand_rect(rect, page_rect, text, float(original_size or 11))
    # On a scan the letters are printed inside the page image, so the box has to
    # be covered; it is covered with the paper's own colour in thin bands instead
    # of one white rectangle, which is what users see as a "white box".
    cover_fill = None
    base_size = float(planned_size) if planned_size else _base_size(original_size, size_cap)
    if scanned and not planned_size:
        # Measured before the dry run, so a tight scanned box is not rejected by
        # the preflight only because the source size was too large.
        base_size = _fit_text_size(text, inset, base_size, min_size=6.5)
    fits,_=_preflight_htmlbox(inset,text,base_size,target_lang,page_rect)
    if not fits:
        return False,"متن ترجمه داخل محدوده صفحه جا نشد"

    # Redact only after the dry-run succeeded. This prevents the old bug where a failed write erased source text.
    try:
        # fill=None keeps the underlying page/illustration visible, and the flags
        # stop PyMuPDF from rasterising or deleting images that intersect the box.
        if erase:
            page.add_redact_annot(cover_box, fill=cover_fill)
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
            )
            if scanned:
                # A hair bigger than the text box: OCR boxes stop above descenders, and
                # an uncovered sliver of the original line looks like a broken rule.
                pad = pymupdf.Rect(cover_box.x0 - 1, cover_box.y0 - 1.5, cover_box.x1 + 1, cover_box.y1 + 2)
                pad &= page.rect
                pad = _close_cut_ink(page, pad)
                _fill_paper(page, pad)
        arch=pymupdf.Archive(str(_FONT_DIR))
        html=f"<body>{html_mod.escape(text).replace(chr(10),'<br>')}</body>"
        # Shrink just enough that the paragraph stays inside its own box: big
        # enough to read, small enough to never sit on the next line.
        if not planned_size:
            base_size = _fit_text_size(text, inset, base_size, min_size=6.5)
        spare,scale=page.insert_htmlbox(inset,html,css=_htmlbox_css(base_size,target_lang,line_height=1.02),archive=arch,scale_low=0.20)
        if spare < 0:
            return False,"درج متن بعد از پاک‌سازی شکست خورد"
        return True,""
    except Exception as exc:
        return False,str(exc)

def _append_pdf_attribution_page(doc, target_lang: str = "fa") -> None:
    import pymupdf
    # Match the document's own page size: a hard-coded A4 page next to Letter
    # content printed with white margins and different paper.
    if doc.page_count:
        last = doc[-1].rect
        page = doc.new_page(width=last.width, height=last.height)
    else:
        page = doc.new_page()
    width, height = page.rect.width, page.rect.height
    rect = pymupdf.Rect(50, height*0.40, width-50, height*0.60)
    if _FONT_PATH.exists() and hasattr(page, "insert_htmlbox"):
        archive = pymupdf.Archive(str(_FONT_DIR))
        css = "@font-face{font-family:Sahel;src:url('Sahel.ttf')} body{font-family:Sahel;font-size:24pt;text-align:center;direction:rtl;color:#c20c0c;margin:0;}"
        spare, scale = page.insert_htmlbox(rect, f"<body>{ATTRIBUTION_TEXT}</body>", css=css, archive=archive, scale_low=0.5)
        if spare < 0:
            raise RuntimeError("ساخت صفحهٔ انتساب ترجمه شکست خورد.")
    else:
        page.insert_textbox(rect, ATTRIBUTION_TEXT, fontsize=24, color=(0.78,0.03,0.03), align=pymupdf.TEXT_ALIGN_CENTER)


def translate_pdf_optimized(
    input_path: str,
    translator,
    target_lang: str = "fa",
    output_path: str | None = None,
    batch_size: int = 3,
    ocr_lang: str = "eng",
    api_key: str | None = None,
    vision_model: str | None = None,
    skip_pages: set[int] | None = None,
    precomputed_ocr: dict[int, list[dict]] | None = None,
    progress_callback: Callable | None = None,
    on_empty_page: Callable | None = None,
    enable_ocr: bool = False,
    ocr_method: str = "auto",
    translation_workers: int = 4,
    translation_batch_chars: int = 14000,
    return_stats: bool = False,
    safe_optimization: bool = True,
    protect_nontranslatable: bool = True,
    adaptive_batch: bool = True,
    use_checkpoint: bool = True,
    use_cache: bool = True,
    self_review: bool = False,
    final_check: bool = False,
    uniform_font: bool = True,
    skip_text_on_image: bool = True,
    match_background: bool = True,
    force_ocr_pages: set[int] | None = None,
    style: str | None = None,
    translation_qc: bool = True,
    qc_repair: bool = False,
    cover_detection: bool = True,
    cover_threshold: float = 0.72,
    manual_page_status: dict | None = None,
    vision_api_key: str | None = None,
    cover_decisions: list | None = None,
    viewer_safe_images: bool = True,
    short_prompt: str = "",
) -> str | tuple[str, PDFTranslationStats]:
    """Translate a PDF while preserving page positions; OCR is opt-in."""
    import pymupdf

    input_path = str(input_path)
    if output_path is None:
        p = Path(input_path)
        output_path = str(p.with_name(f"{p.stem}_translated{p.suffix}"))
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise ValueError("مسیر خروجی نباید با فایل ورودی یکسان باشد.")
    skip_pages = set(skip_pages or set())
    precomputed_ocr = precomputed_ocr or {}
    if short_prompt:
        translator = _ShortPromptTranslator(translator, short_prompt)

    # The chosen style is one short line in the system prompt, not a paragraph:
    # it would otherwise be paid for on every block of the book.
    if style:
        try:
            from trarium.styles import apply as _apply_style

            _apply_style(translator, style)
        except Exception:
            pass

    doc = pymupdf.open(input_path)
    try:
        total_pages = len(doc)

        # 0) Which pages are not text at all? Covers, title pages, plates and
        # advertisements are found before a single character is OCR'd or
        # translated, and are then left exactly as they are: their pages are
        # never redacted, never rewritten and never re-rendered.
        # A caller that already classified the pages (to keep OCR away from a
        # cover, for instance) hands its decisions over here.
        page_decisions: list = list(cover_decisions or [])
        if cover_detection and not page_decisions:
            page_decisions = _detect_pages(
                doc,
                CoverDetectionConfig(
                    enabled=True,
                    threshold=float(cover_threshold),
                    manual=dict(manual_page_status or {}),
                    vision=bool(vision_api_key),
                    vision_api_key=vision_api_key,
                ),
                progress_callback=(lambda p, m: progress_callback(p * 0.10, m)) if progress_callback else None,
            )
        if cover_detection:
            # A page without a text layer is only a picture: without a second
            # opinion it is impossible to tell a cover from a scanned page of
            # prose, and a run that uses OCR can read it. Such a page therefore
            # stays in the run unless the vision model confirmed the cover.
            ocr_will_run = bool(enable_ocr or precomputed_ocr or force_ocr_pages)
            detected = set()
            for decision in page_decisions:
                if not decision.exempt:
                    continue
                if (
                    ocr_will_run
                    and decision.source != "vision"
                    and int(decision.features.get("words") or 0) == 0
                    and not decision.features.get("has_text_layer")
                ):
                    decision.status = "NORMAL_PAGE"
                    decision.confidence = 0.5
                    decision.reasons = list(decision.reasons) + [
                        "بدون لایهٔ متنی و با OCR فعال: ترجمه می‌شود"
                    ]
                    continue
                detected.add(decision.page_index)
            if detected:
                skip_pages |= detected
            _counts = _page_summary(page_decisions)
            _parts = [
                f"{_PAGE_LABELS[k]}: {_counts[key]}"
                for key, k in (
                    ("book_covers", "BOOK_COVER"),
                    ("manga_covers", "MANGA_COVER"),
                    ("title_pages", "TITLE_PAGE"),
                    ("illustration_pages", "ILLUSTRATION_PAGE"),
                    ("ad_pages", "AD_PAGE"),
                    ("uncertain_pages", "UNCERTAIN"),
                )
                if _counts[key]
            ]
            if _parts:
                console.print("  🖼️ تشخیص خودکار صفحه‌ها: " + "، ".join(_parts))
            if detected:
                console.print(
                    "  🔒 دست‌نخورده می‌مانند: صفحهٔ "
                    + "، ".join(str(i + 1) for i in sorted(detected))
                )

        all_blocks: list[list[dict]] = [[] for _ in range(total_pages)]
        scanned_pages: list[tuple[int, object]] = []

        # 1) Native text first. OCR only for pages that actually need it.
        force_ocr = set(force_ocr_pages or set())
        for page_num in range(total_pages):
            if page_num in skip_pages:
                continue
            blocks = extract_text_blocks(doc[page_num])
            if page_num in force_ocr:
                # The page's own text layer is noise (ornament, old imprint): throw
                # it away so OCR can read the printed letters instead.
                blocks = []
            if blocks:
                all_blocks[page_num] = blocks
            elif enable_ocr or page_num in force_ocr:
                scanned_pages.append((page_num, doc[page_num]))
            if progress_callback and (page_num + 1) % 10 == 0:
                progress_callback((page_num + 1) / max(total_pages, 1) * 0.15, f"تحلیل صفحه {page_num + 1}/{total_pages}...")

        if scanned_pages:
            if precomputed_ocr:
                for page_num, _ in scanned_pages:
                    all_blocks[page_num] = _normalise_layout_blocks(list(precomputed_ocr.get(page_num, [])), doc[page_num].rect)
            elif ocr_method == "pdf24":
                # PDF24 works on the whole document (official pdf24-Ocr.exe CLI), so it
                # runs once and only its text layer is used for the scanned pages.
                from trarium.ocr_pdf24 import ocr_document_blocks

                if progress_callback:
                    progress_callback(0.15, "OCR با PDF24...")
                doc_blocks, ocr_pdf_path = ocr_document_blocks(input_path, language=ocr_lang, dpi=300)
                pdf24_ocr_path = str(ocr_pdf_path)
                for page_num, _ in scanned_pages:
                    all_blocks[page_num] = _normalise_layout_blocks(
                        list(doc_blocks.get(page_num, [])), doc[page_num].rect
                    )
            elif ocr_method == "ocrspace":
                # OCR.space is a hosted API (free plan: 1 MB / 3 pages per request),
                # so each scanned page is rendered and sent on its own.
                from trarium.ocr_space import ocr_document_blocks as _ocr_space_blocks

                if progress_callback:
                    progress_callback(0.15, "OCR با OCR.space...")
                space_blocks, _space_meta = _ocr_space_blocks(
                    input_path, language=ocr_lang, dpi=200
                )
                for page_num, _ in scanned_pages:
                    all_blocks[page_num] = _normalise_layout_blocks(
                        list(space_blocks.get(page_num, [])), doc[page_num].rect
                    )
            elif ocr_method == "openrouter":
                ocr_map = extract_ocr_blocks_batch_openrouter(scanned_pages, api_key, vision_model, batch_size)
                for page_num, _ in scanned_pages:
                    all_blocks[page_num] = _normalise_layout_blocks(ocr_map.get(page_num, []), doc[page_num].rect)
            else:
                for i, (page_num, page) in enumerate(scanned_pages, 1):
                    try:
                        all_blocks[page_num] = _normalise_layout_blocks(extract_ocr_blocks(page, lang=ocr_lang, api_key=api_key, vision_model=vision_model, ocr_method=ocr_method), page.rect)
                    except Exception as exc:
                        console.print(f"  ⚠️ OCR صفحه {page_num + 1}: {exc}")
                    if progress_callback:
                        progress_callback(0.15 + 0.25 * i / max(len(scanned_pages), 1), f"OCR صفحه {page_num + 1}/{total_pages}...")

        pdf24_ocr_path = locals().get("pdf24_ocr_path", "")
        # Pages whose text layer is OCR noise (ornamental pages) are left alone.
        garbled_pages = set()
        for page_num, blocks in enumerate(all_blocks):
            texts = [str(b.get("text", "") or "") for b in blocks]
            if texts and sum(_looks_garbled(t) for t in texts) / len(texts) >= 0.5:
                garbled_pages.add(page_num)
        stats_pages_skipped = len(garbled_pages)
        entries = []
        for page_num, blocks in enumerate(all_blocks):
            if page_num in skip_pages:
                continue
            for idx, block in enumerate(blocks):
                if page_num in garbled_pages:
                    continue
                text = str(block.get("text", "") or "").strip()
                if safe_optimization:
                    text = safe_normalize_text(text)
                if text:
                    entries.append((page_num, idx, text))

        if not entries:
            raise ValueError("هیچ متن قابل ترجمه‌ای پیدا نشد. OCR را فعال کنید یا PDF را با یک OCR خارجی پردازش کنید.")

        stats = PDFTranslationStats(ocr_pdf_path=pdf24_ocr_path,
            total_blocks=len(entries),
            pages_with_text=sum(bool(blocks) for blocks in all_blocks),
            page_decisions=[d.as_dict() for d in page_decisions],
            page_status_counts=_page_summary(page_decisions) if page_decisions else {},
            detected_exempt_pages=[d.page_index + 1 for d in page_decisions if d.exempt],
        )

        # 2) Translate in multi-page batches. Never abort the whole document for empty blocks.
        translated, failures, first_translation_error, _tm_stats = _translate_blocks_resilient(
            translator, entries, target_lang,
            max_workers=max(1, int(translation_workers)),
            max_chars=max(4000, int(translation_batch_chars)),
            progress_callback=(lambda p, m: progress_callback(0.40 + p * 0.40, m)) if progress_callback else None,
            return_stats=True, adaptive_batch=adaptive_batch,
            use_cache=use_cache, use_checkpoint=use_checkpoint,
            protect_nontranslatable=protect_nontranslatable,
        )

        # Make a per-page map. Failed blocks intentionally keep the original text.
        page_outputs: dict[int, list[str]] = {}
        for page_num, block_idx, source in entries:
            page_outputs.setdefault(page_num, []).append(translated.get((page_num, block_idx), source))

        # 3) Redact and replace only successfully translated blocks.
        written = 0
        failed_keys = {(p, i) for p, i, _ in failures}
        stats.style = str(style or "")
        stats.translated_blocks = sum(1 for p, i, source in entries if (p, i) in translated and translated[(p, i)].strip() and translated[(p, i)].strip() != source.strip() and (p, i) not in failed_keys)
        stats.failed_blocks = len(failures)
        stats.first_translation_error = first_translation_error
        stats.cache_hits = _tm_stats.get("cache_hits", 0)
        stats.checkpoint_hits = _tm_stats.get("checkpoint_hits", 0)
        stats.api_requests = _tm_stats.get("api_requests", 0)
        stats.input_tokens = _tm_stats.get("input_tokens", 0)
        stats.output_tokens = _tm_stats.get("output_tokens", 0)
        stats.protected_spans = _tm_stats.get("protected_spans", 0)
        # Persian typography is mechanical work the model keeps getting wrong:
        # «می توان», spaces before «،», mixed Arabic letters. Fix it here, once,
        # instead of asking every block again.
        if target_lang == "fa" and translated:
            from trarium.fa_style import normalize as _fa_normalize

            for _key, _value in list(translated.items()):
                cleaned = _fa_normalize(_value)
                if cleaned:
                    translated[_key] = cleaned

        # Compare every translation with its source before anything is written.
        # This only reports; repairing costs tokens and stays opt-in.
        if translation_qc and translated:
            from trarium.translation_qc import check as _qc_check, repair_list as _qc_repair

            _pairs = [(p, i, src, translated.get((p, i), "")) for p, i, src in entries]
            try:
                _glossary = getattr(translator, "glossary", None) or {}
                _report = _qc_check(_pairs, _glossary)
                stats.translation_issues = _report["count"]
                stats.translation_issue_kinds = _report["by_kind"]
                if qc_repair:
                    _todo = _qc_repair(_report, _pairs)
                    if _todo:
                        from trarium.translation_qc import REPAIR_PROMPT

                        _src = {(p, i): s for p, i, s in entries}
                        for _page, _idx in _todo:
                            _source = _src.get((_page, _idx), "")
                            if not _source:
                                continue
                            try:
                                _fixed = translator.translate(REPAIR_PROMPT.format(source=_source, current=translated[(_page, _idx)]), target_lang)
                            except Exception:
                                continue
                            _fixed = _fa_normalize(_fixed or "").strip()
                            if _fixed and re.search(r"[\u0600-\u06ff]", _fixed):
                                translated[(_page, _idx)] = _fixed
                                stats.repaired_blocks += 1
            except Exception as exc:  # never let the checker break a run
                stats.translation_issue_kinds = {"checker_error": str(exc)[:200]}

        if self_review:
            stats.reviewed_blocks = _review_translations(translator, entries, translated, target_lang)
        # One body size for the whole document keeps a light novel from printing
        # one paragraph at 18 pt and the next at 7 pt.
        body_size = None
        if uniform_font:
            sizes = [float(b.get("font_size") or 0) for blocks in all_blocks for b in blocks if (b.get("font_size") or 0) > 0]
            if sizes:
                sizes.sort()
                body_size = sizes[len(sizes) // 2]
        pages_written = set()
        for page_num, blocks in enumerate(all_blocks):
            if page_num in skip_pages or not blocks:
                continue
            page = doc[page_num]
            to_replace = []
            for idx, block in enumerate(blocks):
                key = (page_num, idx)
                out = translated.get(key)
                if not out or key in failed_keys or out.strip() == str(block.get("text", "")).strip():
                    continue
                if page_num in garbled_pages:
                    continue
                if target_lang == "fa" and not re.search(r"[\u0600-\u06ff]", out):
                    # Nothing Persian came back (a title that stays English, a
                    # failed block): covering it would leave an empty patch.
                    continue
                if not _needs_translation(block.get("text", "")) or not _needs_translation(out):
                    # Numbers/percentages stay untouched: drawing them again only
                    # put white rectangles on top of the pie chart.
                    continue
                to_replace.append((idx, block, out))

            if not to_replace:
                continue

            # Decide once per page: inserting text changes get_text() of the page.
            # A scan can still carry a hidden OCR layer; its letters live in the
            # page image, so it has to be covered like a page with no text layer.
            scanned_page = not page.get_text().strip() or _has_page_image(page)
            artwork = _artwork_rects(page) if skip_text_on_image else []

            keep = []
            for idx, block, out in to_replace:
                if artwork and _block_on_artwork(page, block["bbox"], artwork):
                    # Text printed on top of an illustration: translating it would
                    # cover the drawing while the original letters stay in the image.
                    stats.skipped_artwork += 1
                    continue
                if scanned_page and skip_text_on_image and not _is_plain_paper(page, block["bbox"]):
                    # The scan's own picture is under this box: a paper-colour
                    # cover would paint stripes across the drawing.
                    stats.skipped_artwork += 1
                    continue
                keep.append((idx, block, out))
            if not keep:
                continue

            # Plan the whole page before writing a single block. Every box is
            # measured with the real font on the typographically cleaned text,
            # the page's columns are detected, and the boxes are made disjoint:
            # that is what stops a heading, a paragraph or a list entry from
            # landing on the next one.
            #
            # Only a page with a real text layer is planned this way. A page whose
            # text comes from OCR of a comic has speech bubbles scattered over the
            # artwork, and its boxes come from OCR guesses rather than from the
            # document; there the old path (cover the box, let the text fit the
            # blank bubble) is the one that keeps every line in its bubble.
            has_text_layer = bool(page.get_text().strip())
            if has_text_layer:
                targets = {(page_num, idx) for idx, _, _ in keep}
                fixed = [b["bbox"] for i, b in enumerate(blocks) if (page_num, i) not in targets]
                fixed += artwork
                items = []
                for idx, block, out in keep:
                    base = _base_size(block.get("font_size", 11), body_size if uniform_font else None)
                    cover_box = pymupdf.Rect(block["bbox"]) & page.rect
                    if scanned_page:
                        # Only the blank area of the bubble may be used.
                        limit = _grow_light_box(page, cover_box)
                    else:
                        limit = _expand_rect(block["bbox"], page.rect, out, base)
                    item = LayoutItem(text=out, cover=block["bbox"], base_size=base,
                                      lang=target_lang, limit=tuple(limit))
                    items.append((idx, block, out, item))
                layout = _plan_page([entry[3] for entry in items], page.rect,
                                    _layout_font(str(_FONT_PATH)), fixed=fixed)
                stats.layout_moved += layout.moved
                stats.layout_shrunk += layout.shrunk
                stats.layout_overflow += layout.overflow

                # Dry-run every planned box before a glyph is erased: the whole
                # page is erased in one pass below, so a block that would not fit
                # must be known now — otherwise its source text would be gone.
                ready = []
                for idx, block, out, item in items:
                    size = float(item.size or 6.5)
                    fits, _ = _preflight_htmlbox(pymupdf.Rect(item.plan), out, size, target_lang, page.rect)
                    while not fits and size > 6.0:
                        size = round(max(6.0, size - 0.5), 2)
                        fits, _ = _preflight_htmlbox(pymupdf.Rect(item.plan), out, size, target_lang, page.rect)
                    if not fits:
                        stats.write_failures += 1
                        if not stats.first_write_error:
                            stats.first_write_error = f"صفحه {page_num + 1}, بلاک {idx + 1}: متن در کادر برنامه‌ریزی‌شده جا نشد"
                        continue
                    item.size = size
                    ready.append((idx, block, out, item))
                if not ready:
                    continue
                # One erase for the whole page: a later block's redaction used to
                # wipe the translation an earlier block had just written.
                for _, _, _, item in ready:
                    page.add_redact_annot(pymupdf.Rect(item.cover) & page.rect, fill=None)
                page.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_NONE,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                )
                if scanned_page:
                    for _, _, _, item in ready:
                        pad = pymupdf.Rect(item.cover) & page.rect
                        pad = pymupdf.Rect(pad.x0 - 1, pad.y0 - 1.5, pad.x1 + 1, pad.y1 + 2) & page.rect
                        pad = _close_cut_ink(page, pad)
                        _fill_paper(page, pad)
                items = ready
            else:
                items = [(idx, block, out, None) for idx, block, out in keep]

            for idx, block, out, item in items:
                color = _sample_background(page, block["bbox"]) if (match_background and scanned_page) else None
                ok, err = _write_text(page, block["bbox"], out, block.get("font_size", 11), target_lang, scanned_page,
                                      lines=block.get("lines"), size_cap=body_size if uniform_font else None,
                                      background=color, cover=item.cover if item else block["bbox"],
                                      plan_rect=item.plan if item else None,
                                      planned_size=item.size if item else None,
                                      erase=item is None)
                if ok:
                    written += 1
                    pages_written.add(page_num)
                else:
                    stats.write_failures += 1
                    if not stats.first_write_error:
                        stats.first_write_error = f"صفحه {page_num + 1}, بلاک {idx + 1}: {err}"

            if progress_callback:
                progress_callback(0.80 + 0.20 * (page_num + 1) / max(total_pages, 1), f"ساخت صفحه {page_num + 1}/{total_pages}...")

        stats.pages_written = len(pages_written)
        stats.skipped_noise_pages = stats_pages_skipped
        stats.source_chars = sum(len(x[2]) for x in entries)
        stats.translated_chars = sum(len(translated.get((x[0],x[1]), "")) for x in entries)
        if written == 0 and stats.skipped_artwork == 0:
            reason = stats.first_translation_error or stats.first_write_error or "بررسی تنظیمات ترجمه و فونت را انجام دهید."
            raise RuntimeError(f"هیچ ترجمه‌ای در PDF نوشته نشد. {stats.failed_blocks} بلاک ناموفق بود. علت: {reason}")
        if written == 0 and stats.skipped_artwork:
            # Every block sits on artwork and is protected on purpose: the file is
            # returned unchanged instead of failing the whole job.
            console.print(f"  🖼️ همهٔ {stats.skipped_artwork} بلاک روی تصویر بودند و دست‌نخورده ماندند.")
        # Scanned pages are usually stored as JPEG 2000, which some readers
        # (pdf.js, several phone apps) cannot decode at all: there the plates,
        # the cover and the paper texture of the page vanish and only our text
        # is left. Re-encode those images once, in place, so the pictures show
        # up everywhere.
        if viewer_safe_images:
            from trarium.pdf_images import normalize_jpx_images

            converted = normalize_jpx_images(doc)
            if converted:
                console.print(f"  🖼️ {converted} تصویر JPEG 2000 به فرمت استاندارد تبدیل شد تا در همهٔ نمایشگرها دیده شود.")

        _append_pdf_attribution_page(doc, target_lang)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path, garbage=4, deflate=True, clean=True)
        if final_check:
            from trarium.pdf_qc import check_pdf

            report = check_pdf(input_path, output_path, target_lang)
            stats.qc_ok = report.ok
            stats.qc_issues = list(report.issues)
            for issue in report.issues:
                console.print(f"  ⚠️ QC: {issue}")
        console.print(f"  ✍️ {written}/{len(entries)} بلاک نوشته شد؛ {len(failures)} بلاک ترجمه‌نشده؛ {stats.write_failures} خطای درج.")
        return (output_path, stats) if return_stats else output_path
    finally:
        doc.close()

# ---------------------------------------------------------------------------
# Trarium 2.17 safe optimization layer (defined last to preserve compatibility
# with older integrations that import the legacy helper names).
# ---------------------------------------------------------------------------
from trarium.cache import TranslationCache
from trarium.checkpoint import CheckpointStore, stable_job_id
from trarium.optimizer import safe_normalize_text
from trarium.placeholders import protect as _protect_placeholders, restore as _restore_placeholders
from trarium.utils import estimate_tokens, probably_already_in_persian, is_nontranslatable_token


def _translation_chunks(entries: list[tuple[int, int, str]], max_chars: int = 14000, max_tokens: int | None = None):
    """Token-aware packing at natural block boundaries; source text is never rewritten."""
    limit_chars = max(2000, int(max_chars))
    token_limit = max_tokens or max(256, int(limit_chars / 3))
    batches, current = [], []
    chars, tokens = 0, 0
    for item in entries:
        extra_chars = len(item[2]) + 24
        extra_tokens = estimate_tokens(item[2]) + 8
        if current and (chars + extra_chars > limit_chars or tokens + extra_tokens > token_limit):
            batches.append(current)
            current, chars, tokens = [], 0, 0
        current.append(item)
        chars += extra_chars
        tokens += extra_tokens
    if current:
        batches.append(current)
    return batches


def _parse_marked_translation(result: str, expected_count: int) -> dict[int, str]:
    result = result or ""
    marker_re = re.compile(r"\[TRARIUM_BLOCK_(\d+)\]", re.I)
    matches = list(marker_re.finditer(result))
    if not matches:
        return {}
    parsed = {}
    for i, match in enumerate(matches):
        idx = int(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(result)
        value = result[start:end].strip()
        if 0 <= idx < expected_count and value:
            parsed[idx] = _clean_translation(value)
    return parsed


def _translator_identity(translator):
    provider = str(getattr(translator, "provider", "") or getattr(translator, "last_provider", "") or translator.__class__.__name__)
    model = str(getattr(translator, "model", "") or getattr(translator, "model_name", ""))
    return provider, model


class _ShortPromptTranslator:
    """Adds the user's short prompt («ادبی ننویس»…) as one line to every request.

    The model name carries the prompt too, so the cache and the checkpoint never
    hand back a translation made in another register.
    """

    def __init__(self, inner, short_prompt: str):
        self._inner = inner
        self._line = f"Style: {short_prompt.strip()}."
        self.model_name = f"{getattr(inner, 'model_name', '') or ''}|{short_prompt.strip()}"

    def translate(self, text, target_lang="fa", *args, **kwargs):
        return self._inner.translate(f"{self._line}\n{text}", target_lang, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _translate_marker_batch(translator, entries, target_lang, registry_prompt: str = "", protect_nontranslatable: bool = True):
    prepared = []
    mappings = []
    for i, (_, _, text) in enumerate(entries):
        p = _protect_placeholders(safe_normalize_text(text)) if protect_nontranslatable else type("P", (), {"text": safe_normalize_text(text), "mapping": {}})()
        prepared.append(f"[TRARIUM_BLOCK_{i}]\n{p.text}")
        mappings.append(p.mapping)
    combined = "\n\n".join(prepared)
    prompt = (
        "Translate every marked block completely and faithfully. Output only translated blocks. "
        "Keep [TRARIUM_BLOCK_n] markers exactly. Preserve every __TRARIUM_PH_####__ token exactly. "
        "Do not summarize or omit text.\n"
        + (registry_prompt + "\n" if registry_prompt else "")
        + f"Target language: {target_lang}\n\n{combined}"
    )
    result = translator.translate(prompt, target_lang)
    parsed = _parse_marked_translation(result, len(entries))
    mapped = {}
    for i, entry in enumerate(entries):
        if i in parsed:
            mapped[(entry[0], entry[1])] = _restore_placeholders(parsed[i], mappings[i])
    return mapped


def _valid_translation_output(value: str, source: str) -> bool:
    raw = str(value or "").strip()
    if not raw or raw == str(source or "").strip():
        return False
    return not bool(re.search(r"TRARIUM_BLOCK_|Target language:|Translate this literary text|Output only the translation", raw, re.I))


def _translate_blocks_resilient(translator, entries, target_lang, max_workers=4, max_chars=14000,
                                progress_callback=None, return_stats=False, adaptive_batch=True,
                                use_cache=True, use_checkpoint=True, cache=None, checkpoint=None,
                                registry=None, protect_nontranslatable=True):
    """Batch + cache + checkpoint + placeholder translation with lossless recovery."""
    provider, model = _translator_identity(translator)
    cache = cache or TranslationCache()
    checkpoint = checkpoint or CheckpointStore()
    source_fingerprint = __import__("hashlib").sha256(
        "\n".join(f"{p}:{i}:{safe_normalize_text(t)}" for p, i, t in entries).encode("utf-8")
    ).hexdigest()
    job_id = stable_job_id(source_fingerprint, target_lang, provider, model)
    stats = {"cache_hits": 0, "checkpoint_hits": 0, "api_requests": 0, "input_tokens": 0, "output_tokens": 0, "protected_spans": 0}
    done, pending = {}, []
    checkpoint_values = checkpoint.load(job_id) if use_checkpoint else {}
    duplicate_entries: dict[str, list[tuple[int,int,str]]] = {}

    token_limit = max(256, int(max_chars / 3)) if adaptive_batch else max(256, int(max_chars / 2))
    batches = _translation_chunks(entries, max_chars=max_chars, max_tokens=token_limit)

    def resolve_reuse(entry):
        p, i, text = entry
        source = safe_normalize_text(text)
        if ((target_lang == "fa" and probably_already_in_persian(source)) or is_nontranslatable_token(source)):
            return source
        if use_cache:
            hit = cache.get(source, target_lang, provider, model)
            if hit is not None and _valid_translation_output(hit, source):
                stats["cache_hits"] += 1
                return hit
        unit_key = __import__("hashlib").sha256((f"{job_id}|{p}|{i}|{source}").encode("utf-8")).hexdigest()
        if use_checkpoint:
            hit = checkpoint_values.get(unit_key)
            if hit is not None and _valid_translation_output(hit, source):
                stats["checkpoint_hits"] += 1
                return hit
        stats["protected_spans"] += len(_protect_placeholders(source).mapping)
        return None

    remaining = []
    seen_sources: dict[str, tuple[int,int,str]] = {}
    for entry in entries:
        reused = resolve_reuse(entry)
        key = (entry[0], entry[1])
        if reused is not None:
            done[key] = reused
            continue
        source_key = safe_normalize_text(entry[2])
        if source_key in seen_sources:
            duplicate_entries.setdefault(source_key, []).append(entry)
            continue
        seen_sources[source_key] = entry
        remaining.append(entry)

    def save(entry, value):
        p, i, text = entry
        source = safe_normalize_text(text)
        key = (p, i)
        done[key] = value
        if use_cache:
            cache.put(source, value, target_lang, provider, model)
        if use_checkpoint:
            unit_key = __import__("hashlib").sha256((f"{job_id}|{p}|{i}|{source}").encode("utf-8")).hexdigest()
            checkpoint.put(job_id, unit_key, value)
        for dup in duplicate_entries.get(source, []):
            dkey = (dup[0], dup[1])
            done[dkey] = value
            if use_checkpoint:
                du = __import__("hashlib").sha256((f"{job_id}|{dup[0]}|{dup[1]}|{source}").encode("utf-8")).hexdigest()
                checkpoint.put(job_id, du, value)

    def batch_call(batch):
        try:
            glossary = registry.prompt("\n".join(t for _, _, t in batch), target_lang) if registry else ""
            out = _translate_marker_batch(translator, batch, target_lang, glossary, protect_nontranslatable=protect_nontranslatable)
            usage = getattr(translator, "last_usage", {}) or {}
            stats["api_requests"] += 1
            stats["input_tokens"] += int(usage.get("input_tokens") or estimate_tokens("\n\n".join(t for _, _, t in batch)))
            stats["output_tokens"] += int(usage.get("output_tokens") or sum(estimate_tokens(v) for v in out.values()))
            return batch, out, ""
        except Exception as exc:
            return batch, {}, str(exc)

    first_error = ""
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = [pool.submit(batch_call, b) for b in _translation_chunks(remaining, max_chars=max_chars, max_tokens=token_limit)]
        for future in as_completed(futures):
            batch, out, err = future.result()
            for entry in batch:
                key = (entry[0], entry[1])
                value = out.get(key)
                if value and _valid_translation_output(value, entry[2]):
                    save(entry, value)
                else:
                    pending.append(entry)
            if err and not first_error:
                first_error = err
            if progress_callback:
                progress_callback(len(done) / max(1, len(entries)), f"ترجمه {len(done)}/{len(entries)} بلاک...")

    # Recovery: shrink aggressively. A provider/WAF/model can reject a large batch while accepting a single block.
    def retry_one(entry):
        p, i, text = entry
        source = safe_normalize_text(text)
        protected = _protect_placeholders(source) if protect_nontranslatable else type("P", (), {"text": source, "mapping": {}})()
        prompt = (
            "Translate this literary text completely and faithfully. Output only the translation. "
            "Preserve __TRARIUM_PH_####__ tokens exactly. Do not summarize or omit.\n"
            f"Target language: {target_lang}\n\n{protected.text}"
        )
        try:
            out = translator.translate(prompt, target_lang)
            usage = getattr(translator, "last_usage", {}) or {}
            stats["api_requests"] += 1
            stats["input_tokens"] += int(usage.get("input_tokens") or estimate_tokens(prompt))
            stats["output_tokens"] += int(usage.get("output_tokens") or estimate_tokens(str(out)))
            raw_out = str(out or "")
            out = _restore_placeholders(_clean_translation(raw_out), protected.mapping)
            looks_like_echo = bool(re.search(r"TRARIUM_BLOCK_|Target language:|Translate this literary text|Output only the translation", raw_out, re.I))
            if not out.strip() or out.strip() == source.strip() or looks_like_echo:
                return entry, None, "پاسخ خالی/تکراری یا echo پرامپت بود"
            return entry, out, ""
        except Exception as exc:
            return entry, None, str(exc)

    failures = []
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(pending)))) as pool:
            futures = [pool.submit(retry_one, e) for e in pending]
            for future in as_completed(futures):
                entry, value, err = future.result()
                if value:
                    save(entry, value)
                else:
                    failures.append(entry)
                    if not first_error:
                        first_error = err
                if progress_callback:
                    progress_callback(len(done) / max(1, len(entries)), f"بازیابی {len(done)}/{len(entries)} بلاک...")

    if return_stats:
        return done, failures, first_error, stats
    return done, failures, first_error
