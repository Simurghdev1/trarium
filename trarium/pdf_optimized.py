"""PDF translation with optional OCR, resilient batching and layout preservation."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable
from dataclasses import dataclass

from rich.console import Console

from trarium import ocr_layout
from trarium.utils import clean_translation as _clean_translation

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
    covered_blocks: int = 0          # raster-backed boxes painted before writing
    expanded_blocks: int = 0         # boxes that had to grow into free space
    refined_pages: int = 0           # pages re-OCRed at higher resolution
    uncertain_pages: int = 0         # pages whose OCR boxes failed validation
    patched_pages: int = 0           # scanned pages whose glyph pixels were erased
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
# Second pass for pages whose first OCR result failed validation.  Only flagged
# pages are re-sent, at a resolution where small glyphs stop disappearing.
OCR_REFINE_DPI = 300
OCR_REFINE_MAX_SIDE = 2400
OCR_REFINE_MAX_PAGES = 8

# OCR.space is the hosted engine users can enable with a key: it returns word
# level boxes, which is exactly what the layout writer needs.  The free plan
# rejects uploads over 1 MB, so the page JPEG is shrunk until it fits.
OCRSPACE_ENDPOINT = "https://api.ocr.space/parse/image"
OCRSPACE_MAX_BYTES = 1_000_000

_PADDLE_ENGINES: dict[str, object] = {}
_SURYA_ENGINE = None

GEMINI_OCR_MODEL = os.environ.get("TRARIUM_GEMINI_OCR_MODEL", "gemini-3.5-flash-lite")

OPENROUTER_VISION_MODELS = {
    "Auto (رایگان)": "openrouter/free",
    "Nemotron Nano 12B 2 VL (OCR)": "nvidia/nemotron-nano-12b-v2-vl:free",
    "Nemotron 3 Nano Omni (جدیدتر)": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "Mistral Small 3.1 24B": "mistralai/mistral-small-3.1-24b-instruct:free",
}


def _has_paddleocr() -> bool:
    try:
        return importlib.util.find_spec("paddleocr") is not None
    except Exception:
        return False


def _has_surya() -> bool:
    try:
        return importlib.util.find_spec("surya") is not None
    except Exception:
        return False


def _has_easyocr() -> bool:
    try:
        return importlib.util.find_spec("easyocr") is not None
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


_VISION_RULES = (
    "Rules: (1) one item per visual text block - never merge two separate speech "
    "bubbles, two columns, or a title with the paragraph under it; (2) copy the "
    "complete text of each block, including short labels, page numbers, captions "
    "and handwriting that is readable; (3) never invent or translate text; "
    "(4) bbox is normalized left,top,right,bottom from 0 to 1 measured on the image "
    "you were given; (5) list items in natural reading order; (6) language must be "
    "kept exactly as printed."
)


def _vision_prompt(multi: bool = False, page_nums: list[int] | None = None) -> str:
    if not multi:
        return (
            "Extract every readable piece of text from this scanned page. Return ONLY a JSON array. "
            'Each item must be {"text":"...","bbox":[x0,y0,x1,y1]}. ' + _VISION_RULES
        )
    return (
        f"Extract every readable piece of text from these {len(page_nums or [])} scanned pages. "
        "Return ONLY a JSON object. "
        f"Use these exact page keys: {page_nums}. Each value is a JSON array of "
        '{"text":"...","bbox":[x0,y0,x1,y1]} items. ' + _VISION_RULES
    )


def _render_page_jpeg(page, dpi: int = OCR_DPI, max_side: int = OCR_MAX_SIDE,
                      max_bytes: int | None = None) -> tuple[bytes, int, int]:
    """Render a page as JPEG for the hosted OCR APIs; returns (bytes, width, height)."""
    import pymupdf

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    longest = max(pix.width, pix.height)
    if longest > max_side:
        zoom = (max_side / longest) * (dpi / 72.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    quality = OCR_JPEG_QUALITY
    blob = pix.tobytes("jpeg", jpg_quality=quality)
    while max_bytes and len(blob) > max_bytes and quality > 40:
        quality -= 12
        blob = pix.tobytes("jpeg", jpg_quality=quality)
    return blob, pix.width, pix.height


def _vision_image_data_url(page, dpi: int = OCR_DPI, max_side: int = OCR_MAX_SIDE) -> str:
    """Render one page for vision OCR as a compact JPEG data URL."""
    blob, _width, _height = _render_page_jpeg(page, dpi, max_side)
    return f"data:image/jpeg;base64,{base64.b64encode(blob).decode()}"


def _vision_image_size(page, dpi: int = OCR_DPI, max_side: int = OCR_MAX_SIDE) -> tuple[int, int]:
    """Pixel size of the image actually sent for OCR (mirrors _vision_image_data_url)."""
    width = page.rect.width / 72.0 * dpi
    height = page.rect.height / 72.0 * dpi
    longest = max(width, height)
    if longest > max_side:
        scale = max_side / longest
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


def _gemini_vision_request(api_key: str, model_name: str, image_data_urls: list[str],
                           prompt: str, num_pages: int = 1) -> str:
    """Vision OCR straight through the Gemini API (no OpenRouter hop)."""
    payload = {
        "contents": [{"parts": [{"text": prompt}] + [
            {"inline_data": {"mime_type": url.split(";", 1)[0].split(":", 1)[1],
                             "data": url.split(",", 1)[1]}} for url in image_data_urls
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": min(32000, 8192 * max(1, num_pages))},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    last = "پاسخ خالی"
    for attempt in range(2):
        response = requests.post(url, headers={"Content-Type": "application/json",
                                                "x-goog-api-key": api_key},
                                 json=payload, timeout=240)
        if response.status_code >= 400:
            raise RuntimeError(f"Gemini Vision HTTP {response.status_code}: {response.text[:400]}")
        data = response.json()
        candidates = data.get("candidates") or []
        if candidates:
            last = str(candidates[0].get("finishReason") or "")
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(str(part.get("text") or "") for part in parts).strip()
            if text:
                return text
        if attempt == 0:
            payload["generationConfig"]["maxOutputTokens"] = min(48000, 16384 * max(1, num_pages))
    raise RuntimeError(f"مدل Vision متن برنگرداند ({last}).")


def extract_ocr_blocks_gemini(page, api_key: str | None = None, model_name: str | None = None,
                             dpi: int = OCR_DPI, max_side: int = OCR_MAX_SIDE) -> list[dict]:
    api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("کلید Gemini برای OCR تصویری تنظیم نشده است.")
    model_name = model_name or GEMINI_OCR_MODEL
    response = _gemini_vision_request(api_key, model_name,
                                      [_vision_image_data_url(page, dpi, max_side)], _vision_prompt())
    img_w, img_h = _vision_image_size(page, dpi, max_side)
    return _bbox_to_blocks(_parse_vision_json(response), page.rect.width, page.rect.height, img_w, img_h)


def extract_ocr_blocks_batch_gemini(pages: list[tuple[int, object]], api_key: str | None = None,
                                    model_name: str | None = None, batch_size: int = 3) -> dict[int, list[dict]]:
    api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("کلید Gemini برای OCR تصویری تنظیم نشده است.")
    model_name = model_name or GEMINI_OCR_MODEL
    batch_size = max(1, min(int(batch_size), 4))
    result: dict[int, list[dict]] = {}
    for start in range(0, len(pages), batch_size):
        batch = pages[start:start + batch_size]
        nums = [n for n, _ in batch]
        images = [_vision_image_data_url(page) for _, page in batch]
        try:
            response = _gemini_vision_request(api_key, model_name, images,
                                               _vision_prompt(len(batch) > 1, nums), len(batch))
            parsed = _parse_vision_json(response)
            if len(batch) == 1:
                img_w, img_h = _vision_image_size(batch[0][1])
                result[nums[0]] = _bbox_to_blocks(parsed, batch[0][1].rect.width,
                                                  batch[0][1].rect.height, img_w, img_h)
            else:
                for i, num in enumerate(nums):
                    items = parsed.get(str(num), []) if isinstance(parsed, dict) else []
                    if not items and isinstance(parsed, dict):
                        items = parsed.get(str(i + 1), [])
                    img_w, img_h = _vision_image_size(batch[i][1])
                    result[num] = _bbox_to_blocks(items, batch[i][1].rect.width,
                                                  batch[i][1].rect.height, img_w, img_h)
        except Exception as exc:
            console.print(f"  ⚠️ Gemini Vision batch {nums}: {exc}")
            for num in nums:
                result[num] = []
    # A page can come back empty when it shares a request with pages that
    # dominate the answer (or the model refuses the batch). Retry those alone.
    for num, page in [(num, page) for num, page in pages if not result.get(num)]:
        try:
            blocks = extract_ocr_blocks_gemini(page, api_key, model_name)
            if blocks:
                result[num] = blocks
        except Exception as exc:
            console.print(f"  ⚠️ Gemini Vision صفحه {num + 1}: {exc}")
    return result


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
        bbox = (x0 * pw, y0 * ph, x1 * pw, y1 * ph)
        blocks.append({
            "bbox": bbox,
            "text": text,
            # A paragraph box is not one line tall; estimating the line count
            # first stops 30pt type being written into a six-line paragraph.
            "font_size": ocr_layout.estimate_font_size(bbox, text),
            "line_count": ocr_layout.guess_line_count(bbox, text),
            "source": "vision",
        })
    blocks = ocr_layout.dedupe_blocks(blocks)
    return ocr_layout.reading_order(blocks, pw)


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


def _ocrspace_language(lang: str) -> tuple[str, str]:
    """Map our language names to OCR.space codes; returns (code, engine).

    Persian and Arabic are only supported by Engine 3 (which also accepts
    ``auto``), while Latin text is more reliable on Engine 2.
    """
    first = re.split(r"[+,]", (lang or "eng"))[0].strip().lower()
    codes = {
        "fas": "fas", "fa": "fas", "persian": "fas",
        "ara": "ara", "ar": "ara", "arabic": "ara",
        "eng": "eng", "en": "eng", "english": "eng",
        "jpn": "jpn", "japanese": "jpn", "ja": "jpn",
        "chi_sim": "chs", "ch": "chs", "chs": "chs",
        "rus": "rus", "ru": "rus", "kor": "kor", "ko": "kor",
        "fra": "fre", "deu": "ger", "spa": "spa", "tur": "tur",
    }
    code = codes.get(first, first if len(first) == 3 else "auto")
    return code, "3" if code in {"fas", "ara", "auto"} else "2"


def extract_ocr_blocks_ocrspace(page, api_key: str | None = None, lang: str = "eng",
                                dpi: int = OCR_DPI, max_side: int = OCR_MAX_SIDE,
                                engine: str | None = None) -> list[dict]:
    """OCR one page through the hosted OCR.space API (word boxes included).

    ``isOverlayRequired=true`` is what makes this usable for layout-preserving
    translation: the response carries the pixel box of every word, which is
    converted back to PDF points with the exact rendered image size.
    """
    api_key = api_key or os.environ.get("OCR_SPACE_API_KEY") or os.environ.get("OCRSPACE_API_KEY")
    if not api_key:
        raise RuntimeError("کلید OCR.space تنظیم نشده است (OCR_SPACE_API_KEY).")

    blob, image_width, image_height = _render_page_jpeg(page, dpi, max_side, OCRSPACE_MAX_BYTES)
    language, default_engine = _ocrspace_language(lang)
    payload = {
        "apikey": api_key,
        "language": language,
        "isOverlayRequired": "true",
        "OCREngine": str(engine or default_engine),
        "scale": "true",
        "detectOrientation": "true",
        "filetype": "JPG",
    }
    response = requests.post(OCRSPACE_ENDPOINT, data=payload,
                             files={"file": ("page.jpg", blob, "image/jpeg")}, timeout=180)
    if response.status_code >= 400:
        raise RuntimeError(f"OCR.space HTTP {response.status_code}: {response.text[:300]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"OCR.space پاسخ JSON نداد: {response.text[:200]}") from exc
    if data.get("IsErroredOnProcessing"):
        message = data.get("ErrorMessage") or data.get("ErrorDetails") or "خطای نامشخص"
        raise RuntimeError(f"OCR.space: {message}")

    scale_x = page.rect.width / max(1, image_width)
    scale_y = page.rect.height / max(1, image_height)
    blocks: list[dict] = []
    for parsed in data.get("ParsedResults") or []:
        overlay = parsed.get("TextOverlay") or {}
        for line in overlay.get("Lines") or []:
            words = line.get("Words") or []
            text = " ".join(str(word.get("WordText") or "") for word in words).strip()
            boxes = []
            for word in words:
                try:
                    left = float(word.get("Left") or 0)
                    top = float(word.get("Top") or 0)
                    boxes.append((left, top, left + float(word.get("Width") or 0),
                                  top + float(word.get("Height") or 0)))
                except (TypeError, ValueError):
                    continue
            if len(text) < ocr_layout.MIN_TEXT_CHARS or not boxes:
                continue
            bbox = (min(b[0] for b in boxes) * scale_x, min(b[1] for b in boxes) * scale_y,
                    max(b[2] for b in boxes) * scale_x, max(b[3] for b in boxes) * scale_y)
            blocks.append({
                "bbox": bbox,
                "text": text,
                "font_size": max(6.5, min(16.0, (bbox[3] - bbox[1]) * 0.75)),
                "line_count": 1,
                "source": "ocrspace",
            })
    if not blocks:
        raise RuntimeError("OCR.space هیچ کادر متنی برنگرداند (پاسخ overlay خالی بود).")
    return ocr_layout.reading_order(ocr_layout.dedupe_blocks(blocks), page.rect.width)


def _auto_ocr_methods() -> list[str]:
    """Local engines first, then whichever hosted engine has a key."""
    chain = ["paddleocr", "surya", "easyocr", "tesseract"]
    if os.environ.get("OCR_SPACE_API_KEY") or os.environ.get("OCRSPACE_API_KEY"):
        chain.append("ocrspace")
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        chain.append("gemini")
    chain.append("openrouter")
    return chain


def extract_ocr_blocks(page, lang: str = "eng", api_key: str | None = None,
                       vision_model: str | None = None, ocr_method: str = "auto",
                       ocrspace_key: str | None = None) -> list[dict]:
    """OCR one page with an explicit engine or sensible auto fallback."""
    methods = [ocr_method] if ocr_method != "auto" else _auto_ocr_methods()
    for method in methods:
        try:
            if method == "paddleocr" and _has_paddleocr():
                blocks = extract_ocr_blocks_paddleocr(page, lang=lang)
            elif method == "surya" and _has_surya():
                blocks = extract_ocr_blocks_surya(page, lang=lang)
            elif method == "easyocr" and _has_easyocr():
                easy_langs = ["en"] if "fas" not in lang and "fa" not in lang else ["fa", "en"]
                blocks = extract_ocr_blocks_easyocr(page, easy_langs)
            elif method == "ocrspace":
                blocks = extract_ocr_blocks_ocrspace(page, ocrspace_key, lang=lang)
            elif method == "gemini":
                blocks = extract_ocr_blocks_gemini(page)
            elif method == "openrouter" and api_key:
                blocks = extract_ocr_blocks_openrouter(page, api_key, vision_model)
            elif method == "tesseract" and _is_tesseract_installed():
                blocks = extract_ocr_blocks_tesseract(page, lang=lang)
            else:
                continue
            if blocks:
                return blocks
        except Exception as exc:
            console.print(f"  ⚠️ {method}: {exc}")
    raise RuntimeError("OCR با روش انتخاب‌شده موفق نشد.")


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


def _box_tuple(value) -> tuple[float, float, float, float]:
    try:
        x0, y0, x1, y1 = [float(v) for v in value]
        return x0, y0, x1, y1
    except Exception:
        return 0.0, 0.0, 0.0, 0.0


def _normalise_layout_blocks(blocks: list[dict], page_rect=None) -> list[dict]:
    """Validate OCR/native boxes, merge lines into paragraphs, fix reading order.

    The geometry rules live in :mod:`trarium.ocr_layout` so they are shared by
    every engine (native text, PaddleOCR, Tesseract, EasyOCR, Surya and the
    vision models) and can be unit-tested without a PDF.
    """
    cleaned: list[dict] = []
    for b in blocks or []:
        text = str(b.get("text", "") or "").strip()
        if len(text) < ocr_layout.MIN_TEXT_CHARS:
            continue
        bbox = ocr_layout.box(b)
        if ocr_layout.width(bbox) < 1.0 or ocr_layout.height(bbox) < 1.0:
            continue
        if page_rect is not None:
            page_area = max(1.0, page_rect.width * page_rect.height)
            if ocr_layout.area(bbox) / page_area > ocr_layout.MAX_BLOCK_AREA_RATIO:
                # A box that swallows the page cannot be trusted for redaction.
                continue
        item = dict(b)
        item["bbox"] = bbox
        item["text"] = text
        cleaned.append(item)
    if not cleaned:
        return []
    page_width = getattr(page_rect, "width", None)
    return ocr_layout.merge_lines(ocr_layout.dedupe_blocks(cleaned), page_width)


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
        # Native blocks are generally paragraph-level, so they are not merged;
        # they are ordered column by column instead of page-wide by y.
        return ocr_layout.reading_order(blocks, page.rect.width)
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


def _page_background_image(page, dpi: int = 110):
    """Render the page once so cover fills can be sampled from real pixels."""
    import numpy as np

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    if array.shape[2] > 3:
        array = array[:, :, :3]
    scale = (pix.w / max(1e-6, page.rect.width), pix.h / max(1e-6, page.rect.height))
    return array, scale


def _sample_fill(array, scale, page_rect, rect, include_vertical: bool = True) -> tuple[float, float, float]:
    """Background colour around a text box (never assumed to be white).

    A cream book page and a white manga bubble need different fills; painting
    pure white on either is exactly the bright rectangle users see today.
    """
    import numpy as np

    sx, sy = scale
    height, width = array.shape[:2]
    x0 = int(max(0, min(width - 1, (rect[0] - page_rect.x0) * sx)))
    x1 = int(max(x0 + 1, min(width, (rect[2] - page_rect.x0) * sx)))
    y0 = int(max(0, min(height - 1, (rect[1] - page_rect.y0) * sy)))
    y1 = int(max(y0 + 1, min(height, (rect[3] - page_rect.y0) * sy)))
    pad = max(2, int(round(3 * sx)))
    rings = [
        array[y0:y1, max(0, x0 - pad):x0],
        array[y0:y1, x1:min(width, x1 + pad)],
    ]
    if include_vertical:
        rings.extend([
            array[max(0, y0 - pad):y0, x0:x1],
            array[y1:min(height, y1 + pad), x0:x1],
        ])
    pixels = [ring.reshape(-1, 3) for ring in rings if ring.size]
    if not pixels:
        return (1.0, 1.0, 1.0)
    values = np.concatenate(pixels).astype(np.float32)
    luminance = values.mean(axis=1)
    bright = values[luminance >= np.percentile(luminance, 55)]
    if not len(bright):
        bright = values
    median = np.median(bright, axis=0) / 255.0
    return tuple(round(float(v), 4) for v in median)


def _cover_bands(array, scale, page_rect, rect, font_size: float = 11.0):
    """Split a cover into small cells that follow the page's own shading.

    A cream scanned page is brighter in the middle than at the margins, so one
    flat rectangle per box still reads as a pale patch.  Each cell takes its
    colour from the *paper pixels inside that cell* (the bright end of the
    histogram; glyphs are the dark minority), which reproduces the real
    gradient and makes the cover invisible on the page.
    """
    import numpy as np

    sx, sy = scale
    height, width = array.shape[:2]
    step = max(6.0, min(18.0, float(font_size or 11.0) * 1.25))
    rows = max(1, min(8, int(round((rect[3] - rect[1]) / step)) or 1))
    cols = max(1, min(3, int(round((rect[2] - rect[0]) / 110.0)) or 1))
    fallback = _sample_fill(array, scale, page_rect, rect)
    cells = []
    for row in range(rows):
        y0 = rect[1] + (rect[3] - rect[1]) * row / rows
        y1 = rect[1] + (rect[3] - rect[1]) * (row + 1) / rows
        py0 = int(max(0, min(height - 1, (y0 - page_rect.y0) * sy)))
        py1 = int(max(py0 + 1, min(height, (y1 - page_rect.y0) * sy)))
        for col in range(cols):
            x0 = rect[0] + (rect[2] - rect[0]) * col / cols
            x1 = rect[0] + (rect[2] - rect[0]) * (col + 1) / cols
            px0 = int(max(0, min(width - 1, (x0 - page_rect.x0) * sx)))
            px1 = int(max(px0 + 1, min(width, (x1 - page_rect.x0) * sx)))
            patch = array[py0:py1, px0:px1].reshape(-1, 3).astype(np.float32)
            colour = fallback
            if patch.size:
                luminance = patch.mean(axis=1)
                bright = patch[luminance >= np.percentile(luminance, 65)]
                if len(bright) and float(np.median(bright.mean(axis=1))) >= 150.0:
                    median = np.median(bright, axis=0) / 255.0
                    colour = tuple(round(float(v), 4) for v in median)
            cells.append(((x0, y0, x1, y1), colour))
    return cells


def _raster_has_glyphs(array, scale, page_rect, rect, min_ink: float = 0.004) -> bool:
    """Do the page pixels under this box contain printed-looking glyphs?

    Only then must the box be painted before the translation is drawn.  A photo
    behind a caption has ink but no bright paper background, so it is preserved.
    """
    import numpy as np

    sx, sy = scale
    height, width = array.shape[:2]
    x0 = int(max(0, min(width - 1, (rect[0] - page_rect.x0) * sx)))
    x1 = int(max(x0 + 1, min(width, (rect[2] - page_rect.x0) * sx)))
    y0 = int(max(0, min(height - 1, (rect[1] - page_rect.y0) * sy)))
    y1 = int(max(y0 + 1, min(height, (rect[3] - page_rect.y0) * sy)))
    crop = array[y0:y1, x0:x1]
    if crop.size == 0:
        return False
    gray = crop.astype(np.float32).mean(axis=2)
    # A fixed 128 threshold fails on mid-tone artwork: a blue cover is itself
    # below it and the whole box looked like ink.  Compare against the brightest
    # part of the box instead, the same way the pixel repair classifies glyphs.
    bright = float(np.percentile(gray, 85))
    cutoff = min(bright * 0.60, bright - 40.0)
    ink = float((gray < cutoff).mean())
    # Erase only when some pixels really are much darker than the rest of the
    # box; a uniformly dark photograph has no such contrast and is left alone.
    # A caption printed on a photo *is* erased: replacing it with the
    # translation is better than leaving English under the Persian.
    return bright >= 55.0 and min_ink < ink < 0.6


def _clipped_cover(band, fill, page_rect):
    import pymupdf
    return (pymupdf.Rect(band) & page_rect), fill


def _patch_raster_glyphs(page, boxes, dpi: int = 150, max_side: int = 2200) -> bool:
    """Erase the printed glyphs from the scan itself, keeping the artwork.

    Covering a box with a flat colour works on plain paper but destroys an
    illustration that the text was printed over (manga covers, posters).  Here
    only the dark glyph pixels are replaced by their surroundings, so the
    artwork, gradient and texture survive and no rectangle is drawn.
    """
    import io

    import numpy as np
    from PIL import Image
    import pymupdf

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    if max(pix.width, pix.height) > max_side:
        zoom = max_side / max(pix.width, pix.height) * dpi / 72.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
    scale_x = pix.width / max(1e-6, page.rect.width)
    scale_y = pix.height / max(1e-6, page.rect.height)

    mask = np.zeros(array.shape[:2], dtype=bool)
    scanned_box = False
    margin = max(2, int(round(2.0 * scale_x)))
    for box in boxes:
        x0 = int(max(0, min(pix.w - 1, box[0] * scale_x) - margin))
        x1 = int(max(x0 + 1, min(pix.w, box[2] * scale_x) + margin))
        y0 = int(max(0, min(pix.h - 1, box[1] * scale_y) - margin))
        y1 = int(max(y0 + 1, min(pix.h, box[3] * scale_y) + margin))
        region = array[y0:y1, x0:x1]
        if region.size == 0:
            continue
        luminance = region.astype(np.float32).mean(axis=2)
        paper = float(np.percentile(luminance, 85))
        if paper < 60.0:
            continue  # no bright paper here: probably an illustration, not text
        scanned_box = True
        # A fixed ratio fails on artwork: a mid-tone background can sit just
        # under the threshold and the whole box would be treated as glyph.
        cutoff = min(paper * 0.60, paper - 40.0)
        glyphs = luminance < cutoff
        if glyphs.mean() > 0.70:
            continue  # almost everything is dark: not a text line
        mask[y0:y1, x0:x1] |= glyphs
    if not mask.any():
        # Nothing left to erase: the redaction already removed the source text
        # (a caption printed as vector text on a photo).  Calling this a failure
        # would paint a rectangle where there is nothing to clean.
        return scanned_box

    # Grow the mask by one pixel so antialiased edges disappear with the glyph.
    grown = mask.copy()
    grown[1:, :] |= mask[:-1, :]
    grown[:-1, :] |= mask[1:, :]
    grown[:, 1:] |= mask[:, :-1]
    grown[:, :-1] |= mask[:, 1:]
    mask = grown

    # Diffusion fill: every pass paints the masked pixels that already have a
    # known neighbour and marks them known, so the paper colour spreads into the
    # glyph area from all sides.  Without the second step the inside of a thick
    # stroke stayed black (0 / 1e-6), which kept thick printed letters visible.
    values = array.astype(np.float32)
    known = ~mask
    remaining = mask.copy()
    for _ in range(10):
        if not remaining.any():
            break
        padded = np.pad(np.where(known[:, :, None], values, 0.0), ((1, 1), (1, 1), (0, 0)), mode="edge")
        padded_known = np.pad(known.astype(np.float32), ((1, 1), (1, 1)), mode="edge")
        total = np.zeros_like(values)
        weight = np.zeros(known.shape, dtype=np.float32)
        for dy in range(3):
            for dx in range(3):
                total += padded[dy:dy + values.shape[0], dx:dx + values.shape[1]]
                weight += padded_known[dy:dy + values.shape[0], dx:dx + values.shape[1]]
        fillable = remaining & (weight > 0)
        if not fillable.any():
            break
        values[fillable] = (total / np.maximum(weight, 1e-6)[:, :, None])[fillable]
        known |= fillable
        remaining &= ~fillable
    if remaining.any():
        # Anything still enclosed by a very thick stroke takes the page's own
        # paper colour rather than a hole.
        bright = array[array.mean(axis=2) > 150]
        paper_colour = np.median(bright, axis=0) if bright.size else np.array([255, 255, 255], np.float32)
        values[remaining] = paper_colour
    patched = np.clip(values, 0, 255).astype(np.uint8)

    buffer = io.BytesIO()
    Image.fromarray(patched).save(buffer, "JPEG", quality=90, optimize=True)
    page.insert_image(page.rect, stream=buffer.getvalue(), overlay=True)
    return True


def _font_ladder(size: float) -> list[float]:
    size = max(6.5, min(18.0, float(size or 11.0)))
    ladder: list[float] = []
    for factor in (1.0, 0.9, 0.8, 0.7, 0.62):
        value = round(max(6.5, size * factor), 2)
        if value not in ladder:
            ladder.append(value)
    if 6.5 not in ladder:
        ladder.append(6.5)
    return ladder


def _render_page(page, blocks: list[dict], replacements: dict[int, str], target_lang: str,
                 stats: "PDFTranslationStats", force_raster: bool | None = None) -> int:
    """Replace the source text of one page with its translations.

    All source glyphs on the page are removed in one redaction pass *before* any
    translation is drawn.  The previous per-block loop redacted and wrote in the
    same pass, so a later block's expanded box could erase a translation that
    had just been written - one of the causes of missing sentences.
    """
    import html as html_mod
    import pymupdf

    if not replacements:
        return 0
    if not _FONT_PATH.exists():
        raise RuntimeError(f"فونت Sahel پیدا نشد: {_FONT_PATH}")
    page_rect = page.rect
    all_boxes = [pymupdf.Rect(b["bbox"]) for b in blocks if b.get("bbox")]
    try:
        image_boxes = [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()]
    except Exception:
        image_boxes = []
    background, scale = _page_background_image(page)

    plans: list[tuple[int, object, tuple | None, object, float, str]] = []
    for idx, out in replacements.items():
        if not 0 <= idx < len(blocks):
            continue
        block = blocks[idx]
        source = str(block.get("text") or "")
        raw = pymupdf.Rect(block["bbox"]) & page_rect
        if raw.is_empty or raw.width < 2 or raw.height < 2:
            stats.write_failures += 1
            continue
        raw_tuple = (raw.x0, raw.y0, raw.x1, raw.y1)
        original_size = float(block.get("font_size") or 0) or ocr_layout.estimate_font_size(raw_tuple, source)
        raster = (force_raster if force_raster is not None else False) or (
            ocr_layout.raster_share(raw_tuple, [tuple(b) for b in image_boxes]) >= ocr_layout.RASTER_COVER_THRESHOLD
            and _raster_has_glyphs(background, scale, page_rect, raw_tuple)
        )
        obstacles = [tuple(b) for b in all_boxes
                     if not (abs(b.x0 - raw.x0) < 0.8 and abs(b.y0 - raw.y0) < 0.8
                             and abs(b.x1 - raw.x1) < 0.8 and abs(b.y1 - raw.y1) < 0.8)]

        chosen = None
        for rect_tuple in ocr_layout.candidate_rects(raw_tuple, tuple(page_rect), obstacles, source):
            rect = pymupdf.Rect(rect_tuple)
            for size in _font_ladder(original_size):
                fits, _scale = _preflight_htmlbox(rect, out, size, target_lang, page_rect)
                if fits:
                    chosen = (rect, size)
                    break
            if chosen:
                break
        if not chosen:
            stats.write_failures += 1
            if not stats.first_write_error:
                stats.first_write_error = f"صفحه {page.number + 1}, بلاک {idx + 1}: متن ترجمه در فضای آزاد جا نشد"
            continue
        text_rect, size = chosen
        padded = ocr_layout.cover_rect(raw_tuple, 1.2 if raster else 0.3)
        if raster:
            covers = [_clipped_cover(band, fill, page_rect)
                      for band, fill in _cover_bands(background, scale, page_rect, padded, size)]
            stats.covered_blocks += 1
        else:
            covers = [(pymupdf.Rect(padded) & page_rect, None)]
        if abs(text_rect.width - raw.width) > 1.0 or abs(text_rect.height - raw.height) > 1.0:
            stats.expanded_blocks += 1
        # The pixel repair looks only just outside the glyph box: a 1.2 padding
        # would also eat the speech-bubble outline or a panel border around the text.
        plans.append({"idx": idx, "padded": padded, "mask": ocr_layout.cover_rect(raw_tuple, 0.2),
                      "covers": covers, "raster": raster,
                      "text_rect": text_rect, "size": size, "out": out})

    if not plans:
        return 0

    # 1) Remove the source text layer first, without any fill.  This must happen
    #    before the page is re-rendered for pixel patching, otherwise the vector
    #    glyphs would be baked into the patched image and never disappear.
    for plan in plans:
        page.add_redact_annot(pymupdf.Rect(plan["padded"]) & page_rect, fill=None)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)

    # 2) For pages whose glyphs live in the scan, erase the glyph pixels from the
    #    image instead of painting rectangles over the artwork.
    patched = False
    if any(plan["raster"] for plan in plans):
        try:
            patched = _patch_raster_glyphs(page, [tuple(plan["mask"]) for plan in plans
                                                  if plan["raster"]])
        except Exception as exc:
            console.print(f"  ⚠️ پاک‌سازی پیکسلی صفحه {page.number + 1}: {exc}")
        if patched:
            stats.patched_pages += 1
    if not patched:
        # Fallback: paint the sampled page colour only when inpainting was not
        # possible, so a scanned page never keeps its source text under ours.
        for plan in plans:
            if not plan["raster"]:
                continue
            for cover, fill in plan["covers"]:
                if fill is not None:
                    page.draw_rect(cover, color=None, fill=fill, overlay=True)

    written = 0
    for plan in plans:
        idx, text_rect, size, out = plan["idx"], plan["text_rect"], plan["size"], plan["out"]
        try:
            archive = pymupdf.Archive(str(_FONT_DIR))
            html = f"<body>{html_mod.escape(out).replace(chr(10), '<br>')}</body>"
            # The dry run and the real insertion must allow the same minimum
            # scale, otherwise a block that preflight accepted at 0.30 still
            # fails here and its sentence disappears from the page.
            spare, _scale = page.insert_htmlbox(text_rect, html, css=_htmlbox_css(size, target_lang),
                                                archive=archive, scale_low=0.2)
            if spare >= 0:
                written += 1
            else:
                stats.write_failures += 1
                if not stats.first_write_error:
                    stats.first_write_error = f"صفحه {page.number + 1}, بلاک {idx + 1}: درج متن پس از پاک‌سازی شکست خورد"
        except Exception as exc:
            stats.write_failures += 1
            if not stats.first_write_error:
                stats.first_write_error = f"صفحه {page.number + 1}, بلاک {idx + 1}: {exc}"
    return written


def _write_text(page, rect, text, original_size: float, target_lang: str,
                scanned: bool | None = None) -> tuple[bool, str]:
    """Write one translated block (kept for callers that need a single block)."""
    stats = PDFTranslationStats()
    block = {"bbox": tuple(rect), "text": "", "font_size": original_size}
    written = _render_page(page, [block], {0: text}, target_lang, stats, force_raster=scanned)
    if written:
        return True, ""
    return False, stats.first_write_error or "متن ترجمه داخل محدوده صفحه جا نشد"


def _htmlbox_css(font_size: float, target_lang: str) -> str:
    rtl=target_lang in {"fa","ar","he","ur"}
    align="right" if rtl else "left"
    direction="rtl" if rtl else "ltr"
    return (f"@font-face{{font-family:Sahel;src:url('Sahel.ttf');}} *{{font-family:Sahel;"
            f"font-size:{font_size:.2f}pt;line-height:1.18;}} body{{margin:0;padding:0;"
            f"direction:{direction};text-align:{align};white-space:normal;}}")


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


def _refine_ocr_pages(scanned_pages: list[tuple[int, object]], ocr_map: dict[int, list[dict]],
                      ocr_method: str, api_key: str | None, vision_model: str | None,
                      max_pages: int = OCR_REFINE_MAX_PAGES) -> tuple[dict[int, list[dict]], int]:
    """Re-OCR only the pages whose boxes failed validation, then merge both passes.

    Vision models miss small text and sometimes answer with page-sized boxes.
    A second look at 300 dpi recovers that text; the merge keeps everything the
    first pass found correctly and never duplicates a line.
    """
    if ocr_method not in {"gemini", "openrouter"}:
        return ocr_map, 0
    flagged = [(num, page) for num, page in scanned_pages
               if ocr_layout.needs_second_pass(ocr_map.get(num) or [], tuple(page.rect))]
    refined = 0
    for num, page in flagged[:max_pages]:
        try:
            if ocr_method == "gemini":
                extra = extract_ocr_blocks_gemini(page, api_key, vision_model,
                                                 dpi=OCR_REFINE_DPI, max_side=OCR_REFINE_MAX_SIDE)
            else:
                extra = extract_ocr_blocks_openrouter(page, api_key, vision_model)
            merged = ocr_layout.merge_passes(ocr_map.get(num) or [], extra, tuple(page.rect))
            if merged:
                ocr_map[num] = merged
                refined += 1
        except Exception as exc:
            console.print(f"  ⚠️ بازبینی OCR صفحه {num + 1}: {exc}")
    return ocr_map, refined


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
    ocrspace_key: str | None = None,
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

    doc = pymupdf.open(input_path)
    try:
        total_pages = len(doc)
        stats = PDFTranslationStats()
        all_blocks: list[list[dict]] = [[] for _ in range(total_pages)]
        scanned_pages: list[tuple[int, object]] = []

        # 1) Native text first. OCR only for pages that actually need it.
        for page_num in range(total_pages):
            if page_num in skip_pages:
                continue
            blocks = extract_text_blocks(doc[page_num])
            if blocks:
                all_blocks[page_num] = blocks
            elif enable_ocr:
                scanned_pages.append((page_num, doc[page_num]))
            if progress_callback and (page_num + 1) % 10 == 0:
                progress_callback((page_num + 1) / max(total_pages, 1) * 0.15, f"تحلیل صفحه {page_num + 1}/{total_pages}...")

        if scanned_pages:
            if precomputed_ocr:
                for page_num, _ in scanned_pages:
                    all_blocks[page_num] = _normalise_layout_blocks(list(precomputed_ocr.get(page_num, [])), doc[page_num].rect)
            elif ocr_method == "gemini":
                ocr_map = extract_ocr_blocks_batch_gemini(scanned_pages,
                                                          os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
                                                          vision_model, batch_size)
                for page_num, _ in scanned_pages:
                    all_blocks[page_num] = _normalise_layout_blocks(ocr_map.get(page_num, []), doc[page_num].rect)
                try:
                    from trarium.usage import record_ocr
                    record_ocr(pages=len(ocr_map), requests=max(1, (len(scanned_pages) + batch_size - 1) // batch_size),
                               provider="gemini", model=str(vision_model or GEMINI_OCR_MODEL))
                except Exception:
                    pass
            elif ocr_method == "openrouter":
                ocr_map = extract_ocr_blocks_batch_openrouter(scanned_pages, api_key, vision_model, batch_size)
                for page_num, _ in scanned_pages:
                    all_blocks[page_num] = _normalise_layout_blocks(ocr_map.get(page_num, []), doc[page_num].rect)
                try:
                    from trarium.usage import record_ocr
                    record_ocr(pages=len(ocr_map), requests=max(1, (len(scanned_pages) + batch_size - 1) // batch_size),
                               provider="openrouter", model=str(vision_model or "vision"))
                except Exception:
                    pass
            else:
                for i, (page_num, page) in enumerate(scanned_pages, 1):
                    try:
                        all_blocks[page_num] = _normalise_layout_blocks(extract_ocr_blocks(page, lang=ocr_lang, api_key=api_key, vision_model=vision_model, ocr_method=ocr_method, ocrspace_key=ocrspace_key), page.rect)
                    except Exception as exc:
                        console.print(f"  ⚠️ OCR صفحه {page_num + 1}: {exc}")
                    if progress_callback:
                        progress_callback(0.15 + 0.25 * i / max(len(scanned_pages), 1), f"OCR صفحه {page_num + 1}/{total_pages}...")
                try:
                    from trarium.usage import record_ocr
                    record_ocr(pages=len(scanned_pages), requests=len(scanned_pages),
                               provider="local", model=str(ocr_method))
                except Exception:
                    pass

            # Pages whose boxes are empty, duplicated, page-sized or overlapping
            # get one more look before anything is redacted or written.
            stats.uncertain_pages = sum(
                1 for num, _page in scanned_pages
                if ocr_layout.needs_second_pass(all_blocks[num], doc[num].rect)
            )
            if ocr_method in {"gemini", "openrouter"}:
                primary = {num: all_blocks[num] for num, _page in scanned_pages}
                refined_map, refined = _refine_ocr_pages(scanned_pages, primary, ocr_method,
                                                        api_key, vision_model)
                if refined:
                    for num, _page in scanned_pages:
                        if refined_map.get(num):
                            all_blocks[num] = _normalise_layout_blocks(refined_map[num], doc[num].rect)
                    stats.refined_pages = refined

        entries = []
        for page_num, blocks in enumerate(all_blocks):
            if page_num in skip_pages:
                continue
            for idx, block in enumerate(blocks):
                text = str(block.get("text", "") or "").strip()
                if safe_optimization:
                    text = safe_normalize_text(text)
                if text:
                    entries.append((page_num, idx, text))

        if not entries:
            raise ValueError("هیچ متن قابل ترجمه‌ای پیدا نشد. OCR را فعال کنید یا PDF را با یک OCR خارجی پردازش کنید.")

        stats.total_blocks = len(entries)
        stats.pages_with_text = sum(bool(blocks) for blocks in all_blocks)

        # 2) Translate in multi-page batches. Never abort the whole document for empty blocks.
        translated, failures, first_translation_error, _tm_stats = _translate_blocks_resilient(
            translator, entries, target_lang,
            max_workers=max(1, int(translation_workers)),
            max_chars=max(4000, int(translation_batch_chars)),
            progress_callback=(lambda p, m: progress_callback(0.40 + p * 0.40, m)) if progress_callback else None,
            return_stats=True, adaptive_batch=adaptive_batch,
            use_cache=use_cache, use_checkpoint=use_checkpoint,
            protect_nontranslatable=protect_nontranslatable,
            short_prompt=short_prompt,
        )

        # Make a per-page map. Failed blocks intentionally keep the original text.
        page_outputs: dict[int, list[str]] = {}
        for page_num, block_idx, source in entries:
            page_outputs.setdefault(page_num, []).append(translated.get((page_num, block_idx), source))

        # 3) Redact and replace only successfully translated blocks.
        written = 0
        failed_keys = {(p, i) for p, i, _ in failures}
        stats.translated_blocks = sum(1 for p, i, source in entries if (p, i) in translated and translated[(p, i)].strip() and translated[(p, i)].strip() != source.strip() and (p, i) not in failed_keys)
        stats.failed_blocks = len(failures)
        stats.first_translation_error = first_translation_error
        stats.cache_hits = _tm_stats.get("cache_hits", 0)
        stats.checkpoint_hits = _tm_stats.get("checkpoint_hits", 0)
        stats.api_requests = _tm_stats.get("api_requests", 0)
        stats.input_tokens = _tm_stats.get("input_tokens", 0)
        stats.output_tokens = _tm_stats.get("output_tokens", 0)
        stats.protected_spans = _tm_stats.get("protected_spans", 0)
        pages_written = set()
        for page_num, blocks in enumerate(all_blocks):
            if page_num in skip_pages or not blocks:
                continue
            page = doc[page_num]
            replacements: dict[int, str] = {}
            for idx, block in enumerate(blocks):
                key = (page_num, idx)
                out = translated.get(key)
                if not out or key in failed_keys:
                    continue
                if out.strip() == str(block.get("text", "")).strip():
                    continue
                replacements[idx] = out

            if not replacements:
                continue

            page_written = _render_page(page, blocks, replacements, target_lang, stats)
            if page_written:
                written += page_written
                pages_written.add(page_num)

            if progress_callback:
                progress_callback(0.80 + 0.20 * (page_num + 1) / max(total_pages, 1), f"ساخت صفحه {page_num + 1}/{total_pages}...")

        stats.pages_written = len(pages_written)
        stats.source_chars = sum(len(x[2]) for x in entries)
        stats.translated_chars = sum(len(translated.get((x[0],x[1]), "")) for x in entries)
        if written == 0:
            reason = stats.first_translation_error or stats.first_write_error or "بررسی تنظیمات ترجمه و فونت را انجام دهید."
            raise RuntimeError(f"هیچ ترجمه‌ای در PDF نوشته نشد. {stats.failed_blocks} بلاک ناموفق بود. علت: {reason}")
        _append_pdf_attribution_page(doc, target_lang)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path, garbage=4, deflate=True, clean=True)
        console.print(f"  ✍️ {written}/{len(entries)} بلاک نوشته شد؛ {len(failures)} بلاک ترجمه‌نشده؛ "
                      f"{stats.write_failures} خطای درج؛ {stats.covered_blocks} پوشش تصویری؛ "
                      f"{stats.expanded_blocks} بلاک گسترده‌شده؛ {stats.refined_pages} صفحه بازبینی OCR.")
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


def _translate_marker_batch(translator, entries, target_lang, registry_prompt: str = "", protect_nontranslatable: bool = True, short_prompt: str = ""):
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
        + (f"Style: {short_prompt}.\n" if short_prompt else "")
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
                                registry=None, protect_nontranslatable=True, short_prompt: str = ""):
    """Batch + cache + checkpoint + placeholder translation with lossless recovery."""
    provider, model = _translator_identity(translator)
    cache = cache or TranslationCache()
    checkpoint = checkpoint or CheckpointStore()
    source_fingerprint = __import__("hashlib").sha256(
        "\n".join(f"{p}:{i}:{safe_normalize_text(t)}" for p, i, t in entries).encode("utf-8")
    ).hexdigest()
    job_id = stable_job_id(source_fingerprint, target_lang, provider, model + (f"|{short_prompt}" if short_prompt else ""))
    stats = {"cache_hits": 0, "checkpoint_hits": 0, "api_requests": 0, "input_tokens": 0, "output_tokens": 0, "protected_spans": 0}
    done, pending = {}, []
    checkpoint_values = checkpoint.load(job_id) if use_checkpoint else {}
    duplicate_entries: dict[str, list[tuple[int,int,str]]] = {}

    token_limit = max(256, int(max_chars / 3)) if adaptive_batch else max(256, int(max_chars / 2))
    def resolve_reuse(entry):
        p, i, text = entry
        source = safe_normalize_text(text)
        if ((target_lang == "fa" and probably_already_in_persian(source)) or is_nontranslatable_token(source)):
            return source
        if use_cache:
            hit = cache.get(source, target_lang, provider, model, short_prompt)
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
            cache.put(source, value, target_lang, provider, model, short_prompt)
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
            out = _translate_marker_batch(translator, batch, target_lang, glossary, protect_nontranslatable=protect_nontranslatable, short_prompt=short_prompt)
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
            + (f"Style: {short_prompt}.\n" if short_prompt else "")
            + f"Target language: {target_lang}\n\n{protected.text}"
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
