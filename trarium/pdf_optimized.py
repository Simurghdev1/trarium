"""ترجمه بهینه — کمترین توکن ممکن."""

import base64
import io
import json
import os
import re
import time
import requests
from pathlib import Path
from rich.console import Console
from functools import lru_cache

from trarium.utils import clean_translation as _clean_translation

console = Console()

_FONT_DIR = Path(__file__).parent / "fonts"
_FONT_PATH = _FONT_DIR / "Sahel.ttf"

_PADDLE_ENGINES = {}
_SURYA_ENGINE = None

# OCR Vision: تصویر صفحه با ۳۰۰dpi و PNG متنی معادل ده‌ها هزار توکن است.
# تصویر کوچک‌تر و JPEG همان متن را خوانا نگه می‌دارد با کسری از توکن.
OCR_DPI = 180
OCR_JPEG_QUALITY = 82
OCR_MAX_SIDE = 1700

_FONT_NAME = "Sahel"
_RTL_TARGETS = {"fa", "ar", "he", "ur"}


@lru_cache(maxsize=8)
def _cached_tesseract_languages(tess_path: str):
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = tess_path
    return frozenset(pytesseract.get_languages(config=""))

def _get_paddle_engine(lang: str):
    """Create/reuse PaddleOCR across both 2.x and 3.x APIs."""
    import logging
    import re as _re
    import paddleocr as _paddleocr
    from paddleocr import PaddleOCR

    key = lang
    engine = _PADDLE_ENGINES.get(key)
    if engine is not None:
        return engine

    version_text = getattr(_paddleocr, "__version__", "3.0")
    m = _re.match(r"(\d+)\.(\d+)", str(version_text))
    version = (int(m.group(1)), int(m.group(2))) if m else (3, 0)

    if version < (3, 0):
        # PaddleOCR 2.x.
        kwargs = {"lang": key, "use_angle_cls": True, "show_log": False}
    else:
        # PaddleOCR 3.x removed show_log/use_angle_cls. Disable expensive
        # document preprocessing because Trarium already renders clean pages.
        kwargs = {
            "lang": key,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        }

    logging.getLogger("paddleocr").setLevel(logging.ERROR)
    engine = PaddleOCR(**kwargs)
    _PADDLE_ENGINES[key] = engine
    return engine

def _get_surya_engine():
    global _SURYA_ENGINE
    if _SURYA_ENGINE is None:
        from surya.inference import SuryaInferenceManager
        from surya.recognition import RecognitionPredictor
        manager = SuryaInferenceManager()
        _SURYA_ENGINE = RecognitionPredictor(manager)
    return _SURYA_ENGINE




def _is_tesseract_installed():
    from trarium.install_tesseract import _find_tesseract
    return _find_tesseract() is not None


def _has_easyocr():
    """بررسی نصب بودن EasyOCR."""
    try:
        import easyocr
        return True
    except ImportError:
        return False
def _has_paddleocr():
    """بررسی نصب بودن PaddleOCR."""
    try:
        import paddleocr
        return True
    except ImportError:
        return False

def _has_surya():
    """بررسی نصب بودن Surya OCR."""
    try:
        import surya
        return True
    except ImportError:
        return False

def _preprocess_image(image):
    """بهبود کیفیت تصویر قبل از OCR — افزایش کنتراست + حذف نویز."""
    try:
        import cv2
        import numpy as np
        from PIL import Image

        if image.mode != "L":
            gray = image.convert("L")
        else:
            gray = image

        img_array = np.array(gray)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(img_array)
        denoised = cv2.fastNlMeansDenoising(enhanced, h=10)
        _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return Image.fromarray(binary)
    except ImportError:
        return image
    except Exception:
        return image



# ── OpenRouter Vision OCR ──────────────────────────────────

OPENROUTER_VISION_MODELS = {
    "Auto (پیشنهادی)": "google/gemini-2.5-flash",
    "Nemotron Nano 12B VL (بهترین OCR)": "nvidia/nemotron-nano-12b-v2-vl:free",
    "Gemma 3 27B (چندزبانه)": "google/gemma-3-27b-it:free",
    "Mistral Small 3.1 24B": "mistralai/mistral-small-3.1-24b-instruct:free",
}


def _vision_image_data_url(page, dpi: int = OCR_DPI) -> str:
    """تصویر صفحه برای OCR — JPEG کوچک با همان خوانایی، به‌جای PNG سنگین.

    کاهش حجم تصویر مستقیماً هزینهٔ توکن Vision را کم می‌کند؛ برای یک صفحهٔ
    A4 این تغییر معمولاً بیش از ۸۰٪ صرفه‌جویی می‌دهد.
    """
    import pymupdf

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    longest = max(pix.width, pix.height)
    if longest > OCR_MAX_SIDE:
        zoom = (OCR_MAX_SIDE / longest) * (dpi / 72.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    try:
        data = pix.tobytes("jpeg", jpg_quality=OCR_JPEG_QUALITY)
        mime = "image/jpeg"
    except Exception:
        data = pix.tobytes("png")
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _openrouter_vision_request(api_key: str, model_name: str, image_data_urls: list[str],
                              prompt: str, num_pages: int = 1) -> str | None:
    """درخواست OCR به OpenRouter Vision — برای تک‌صفحه یا چندصفحه‌ای."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/trarium",
        "X-Title": "Trarium OCR",
    }

    content = [{"type": "text", "text": prompt}]
    for data_url in image_data_urls:
        content.append({"type": "image_url", "image_url": {"url": data_url}})

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        # سقف خروجی متناسب با تعداد صفحه‌ها، ولی نه بی‌نهایت
        "max_tokens": max(1024, min(8000, 1800 * max(1, num_pages))),
    }

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers, json=payload, timeout=180,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def _parse_vision_json(result_text: str):
    """پاسخ JSON مدل Vision رو با تحمل markdown/prose اضافی parse می‌کنه."""
    if not result_text or not result_text.strip():
        return []
    cleaned = result_text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # بعضی مدل‌ها یک جمله کوتاه قبل/بعد JSON می‌آورند. فقط خود اولین
        # ساختار کامل JSON را استخراج می‌کنیم؛ متن داستان دست‌کاری نمی‌شود.
        starts = [i for i in (cleaned.find("["), cleaned.find("{")) if i >= 0]
        if not starts:
            raise
        start = min(starts)
        end = max(cleaned.rfind("]"), cleaned.rfind("}"))
        if end <= start:
            raise
        return json.loads(cleaned[start:end + 1])


def _bbox_to_blocks(items: list[dict], pw: float, ph: float) -> list[dict]:
    """آیتم‌های JSON Vision رو به بلاک‌های متنی با bbox تبدیل می‌کنه."""
    blocks = []
    for item in items:
        text = item.get("text", "").strip()
        if not text:
            continue
        bbox_raw = item.get("bbox", [])
        if len(bbox_raw) != 4:
            continue
        try:
            x0, y0, x1, y1 = map(float, bbox_raw)
        except (TypeError, ValueError):
            continue
        # The prompt requests normalized coordinates. For out-of-range model
        # responses, treat them as normalized and clamp rather than scaling by
        # an arbitrary maximum coordinate (which distorts aspect ratio).
        x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
        y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
        if x1 <= x0 or y1 <= y0:
            continue
        blocks.append({
            "bbox": (x0 * pw, y0 * ph, x1 * pw, y1 * ph),
            "text": text,
            "font_size": max((y1 - y0) * ph * 0.25, 10),
        })
    return blocks


def extract_ocr_blocks_openrouter(page, api_key: str = None,
                                  model_name: str = None) -> list[dict]:
    """OCR تک‌صفحه با OpenRouter Vision."""
    if not api_key:
        api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        console.print("  ⚠️ OpenRouter Vision: API Key نیست!")
        return []
    if not model_name:
        model_name = "openrouter/free"

    try:
        image_data_url = _vision_image_data_url(page)

        prompt = (
            "Extract ALL text from this scanned page image. Return a JSON array.\n"
            'Each item: {"text": "...", "bbox": [x0, y0, x1, y1]}'
            " where bbox uses normalized coords 0.0-1.0 (left, top, right, bottom).\n"
            "Group text by visual paragraphs/lines. Keep original language.\n"
            "Return ONLY the JSON array, no other text. No markdown fences."
        )

        result_text = _openrouter_vision_request(api_key, model_name, [image_data_url], prompt)
        items = _parse_vision_json(result_text)
        return _bbox_to_blocks(items, page.rect.width, page.rect.height)
    except json.JSONDecodeError:
        console.print("  ⚠️ OpenRouter Vision: JSON parsing failed")
        return []
    except Exception as e:
        console.print(f"  ⚠️ OpenRouter Vision OCR: {e}")
        return []


def extract_ocr_blocks_batch_openrouter(pages: list, api_key: str = None,
                                       model_name: str = None,
                                       batch_size: int = 3,
                                       dpi: int = OCR_DPI) -> dict[int, list[dict]]:
    """OCR دسته‌ای — چند صفحه رو توی یه درخواست بفرسته (صرفه‌جویی توکن).
    pages: لیست (page_num, page_object)
    خروجی: dict[int, list[dict]] — بلاک‌های هر صفحه.
    """
    import base64

    if not api_key:
        api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        console.print("  ⚠️ OpenRouter Vision: API Key نیست!")
        return {}
    if not model_name:
        model_name = "openrouter/free"

    result = {}

    for batch_start in range(0, len(pages), batch_size):
        batch = pages[batch_start:batch_start + batch_size]
        batch_nums = [p[0] for p in batch]

        try:
            image_data_urls = []
            for _, page_obj in batch:
                image_data_urls.append(_vision_image_data_url(page_obj, dpi))

            if len(batch) == 1:
                prompt = (
                    "Extract ALL text from this scanned page image. Return a JSON array.\n"
                    'Each item: {"text": "...", "bbox": [x0, y0, x1, y1]}'
                    " where bbox uses normalized coords 0.0-1.0 (left, top, right, bottom).\n"
                    "Group text by visual paragraphs/lines. Keep original language.\n"
                    "Return ONLY the JSON array, no other text. No markdown fences."
                )
            else:
                prompt = (
                    f"Extract ALL text from these {len(batch)} scanned page images. "
                    f"Return a JSON object using these exact page keys: {batch_nums}. "
                    "Each key maps to that page\'s text blocks.\n"
                    'Each block: {"text": "...", "bbox": [x0, y0, x1, y1]}'
                    " where bbox uses normalized coords 0.0-1.0 (left, top, right, bottom).\n"
                    "Group text by visual paragraphs/lines. Keep original language.\n"
                    "Return ONLY the JSON object, no other text. No markdown fences."
                )

            result_text = _openrouter_vision_request(api_key, model_name, image_data_urls, prompt)
            parsed = _parse_vision_json(result_text)

            if len(batch) == 1:
                page_obj = batch[0][1]
                result[batch_nums[0]] = _bbox_to_blocks(
                    parsed, page_obj.rect.width, page_obj.rect.height
                )
            else:
                for i, page_num in enumerate(batch_nums):
                    page_num = batch_nums[i]
                    page_items = parsed.get(str(page_num), parsed.get(page_num))
                    if page_items is None:
                        page_items = parsed.get(str(i + 1), parsed.get(i + 1, []))
                    page_obj = batch[i][1]
                    result[page_num] = _bbox_to_blocks(
                        page_items, page_obj.rect.width, page_obj.rect.height
                    )

        except Exception as e:
            console.print(f"  ⚠️ OpenRouter Vision batch OCR: {e}")
            for page_num in batch_nums:
                result[page_num] = []

    return result


def _reshape(text: str) -> str:
    """تبدیل فارسی/عربی به شکل صحیح."""
    if not text:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except ImportError:
        # اگه arabic_reshaper نصب نبود، حداقل bidi رو اجرا کن
        try:
            from bidi.algorithm import get_display
            return get_display(text)
        except ImportError:
            return text
    except Exception:
        return text


def extract_text_blocks(page):
    """همه بلاک‌های متنی را استخراج می‌کند و اگر بخش قابل‌توجهی خراب باشد OCR را فعال می‌کند."""
    import pymupdf
    blocks = []
    total_chars = 0
    garbled_chars = 0
    data = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)

    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue

        bbox = block["bbox"]
        text_parts = []
        font_sizes = []

        for line in block.get("lines", []):
            line_text = ""
            for span in line.get("spans", []):
                line_text += span.get("text", "")
                font_sizes.append(span.get("size", 12))
            text_parts.append(line_text.strip())

        full_text = " ".join(text_parts).strip()
        if not full_text:
            continue
        total_chars += len(full_text)
        if _is_garbled(full_text):
            garbled_chars += len(full_text)
            continue

        avg_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12
        blocks.append({
            "bbox": bbox,
            "text": full_text,
            "font_size": round(avg_size, 1),
        })

    # اگر حداقل 25٪ متن استخراج‌شده خراب است، کل صفحه را برای OCR علامت بزن.
    if total_chars and garbled_chars / total_chars >= 0.25:
        return []
    return blocks




def _is_garbled(text: str) -> bool:
    """تشخیص متن خراب/غیرقابل خواندن."""
    if not text or len(text.strip()) < 3:
        return True
    # درصد کاراکترهای غیرعادی
    total = len(text)
    if total == 0:
        return True
    bad = sum(1 for c in text if c in r"!@#$%^&*()[]{}|\<>~`")
    if bad / total > 0.3:
        return True
    # خطوط خیلی کوتاه متوالی
    lines = text.strip().split("\n")
    short_lines = sum(1 for l in lines if len(l.strip()) < 3)
    if len(lines) > 3 and short_lines / len(lines) > 0.5:
        return True
    return False

def extract_text_first(page):
    """استخراج متن PyMuPDF؛ متن خیلی کوتاه یا مشکوک به خرابی را معتبر نمی‌داند."""
    text = page.get_text("text").strip()
    if len(text) > 50 and not _is_garbled(text):
        return text
    return None

def _safe_float(value, default=-1.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def extract_ocr_blocks_tesseract(page, lang="fas+eng"):
    """OCR با Tesseract — بدون فایل موقت (BytesIO)."""
    from trarium.install_tesseract import _find_tesseract

    tess_path = _find_tesseract()
    if not tess_path:
        raise RuntimeError("Tesseract پیدا نشد!")

    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(f"پکیج pytesseract نصب نیست!\npip install pytesseract Pillow\n\n{e}")

    pytesseract.pytesseract.tesseract_cmd = tess_path

    requested_langs = [part.strip() for part in lang.split("+") if part.strip()]
    try:
        installed_langs = set(_cached_tesseract_languages(str(tess_path)))
    except Exception as e:
        raise RuntimeError(f"نتوانستم زبان‌های Tesseract را بررسی کنم: {e}") from e
    missing_langs = [name for name in requested_langs if name not in installed_langs]
    if missing_langs:
        raise RuntimeError(
            "زبان‌های Tesseract نصب نیستند: " + ", ".join(missing_langs) +
            ". زبان‌های نصب‌شده: " + ", ".join(sorted(installed_langs))
        )

    blocks = []
    pix = page.get_pixmap(dpi=300)
    scale = 300 / 72

    # تبدیل pixmap به numpy array → PIL Image
    img = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)

    data = pytesseract.image_to_data(
        img, lang=lang,
        output_type=pytesseract.Output.DICT,
        config="--psm 3"
    )
    valid_words = [
        (t.strip(), float(c)) for t, c in zip(data.get("text", []), data.get("conf", []))
        if t and t.strip()
        for c in [str(c)]
        if _safe_float(c, -1.0) >= 0
    ]
    mean_conf = (sum(c for _, c in valid_words) / len(valid_words)) if valid_words else 0
    if len(valid_words) < 4 or mean_conf < 52:
        try:
            enhanced = _preprocess_image(img)
            retry = pytesseract.image_to_data(
                enhanced, lang=lang, output_type=pytesseract.Output.DICT, config="--psm 6"
            )
            retry_words = [
                (t.strip(), _safe_float(c, -1.0)) for t, c in zip(retry.get("text", []), retry.get("conf", []))
                if t and t.strip() and _safe_float(c, -1.0) >= 0
            ]
            retry_conf = (sum(c for _, c in retry_words) / len(retry_words)) if retry_words else 0
            if len(retry_words) > len(valid_words) or (retry_words and retry_conf > mean_conf + 5):
                data = retry
        except Exception:
            pass
    img.close()

    current_line = {"words": [], "bbox": None, "block_num": None, "line_num": None}

    for j in range(len(data["text"])):
        word = data["text"][j].strip()
        try:
            conf = float(data["conf"][j])
        except (TypeError, ValueError):
            conf = -1.0

        if not word or conf < 50:  # Increased from 15 to filter garbled text
            continue

        x = data["left"][j] / scale
        y = data["top"][j] / scale
        w = data["width"][j] / scale
        h = data["height"][j] / scale
        block_num = data["block_num"][j]
        line_num = data["line_num"][j]

        if (current_line["words"] and
            (current_line.get("block_num") != block_num or
             current_line.get("line_num") != line_num)):
            text = " ".join(current_line["words"])
            if len(text) >= 2:
                blocks.append({
                    "bbox": current_line["bbox"],
                    "text": text,
                    "font_size": max(h * 0.75, 10),
                })
            current_line = {"words": [], "bbox": None, "block_num": block_num, "line_num": line_num}

        current_line["words"].append(word)
        current_line["block_num"] = block_num
        current_line["line_num"] = line_num

        if current_line["bbox"] is None:
            current_line["bbox"] = (x, y, x + w, y + h)
        else:
            bx0, by0, bx1, by1 = current_line["bbox"]
            current_line["bbox"] = (min(bx0, x), min(by0, y), max(bx1, x + w), max(by1, y + h))

    if current_line["words"]:
        text = " ".join(current_line["words"])
        if len(text) >= 2:
            blocks.append({
                "bbox": current_line["bbox"],
                "text": text,
                "font_size": max(h * 0.75, 10),
            })

    return blocks

def extract_ocr_blocks_easyocr(page, languages=["fa", "en"]):
    """EasyOCR — غیرفعال؛ برای فارسی از PaddleOCR/Tesseract استفاده می‌شود."""
    console.print("  ⚠️ EasyOCR غیرفعال شد — از PaddleOCR استفاده کنید")
    return []

def extract_ocr_blocks_paddleocr(page, lang="fas+eng"):
    """OCR با PaddleOCR؛ سازگار با PaddleOCR 2.x و 3.x."""
    try:
        from paddleocr import PaddleOCR
    except ImportError as e:
        raise RuntimeError(
            "پکیج PaddleOCR نصب نیست!\n"
            "pip install paddleocr paddlepaddle\n\n" + str(e)
        )

    pix = page.get_pixmap(dpi=300, alpha=False)
    scale = 300 / 72

    paddle_lang = lang.split("+")[0] if "+" in lang else lang
    lang_map = {
        "fas": "fa", "fa": "fa", "ara": "ar", "ar": "ar",
        "eng": "en", "en": "en", "jpn": "japan", "ja": "japan",
        "chi_sim": "ch", "ch": "ch",
    }
    paddle_lang = lang_map.get(paddle_lang, paddle_lang or "en")

    ocr = _get_paddle_engine(paddle_lang)

    import numpy as np
    try:
        import paddleocr as _paddleocr_pkg
        _paddle_version = tuple(int(x) for x in re.match(r"(\d+)\.(\d+)", getattr(_paddleocr_pkg, "__version__", "0.0")).groups())
    except Exception:
        _paddle_version = (3, 0)
    img_data = np.frombuffer(pix.samples, dtype=np.uint8)
    if pix.n == 1:
        img_data = img_data.reshape(pix.h, pix.w)
    else:
        img_data = img_data.reshape(pix.h, pix.w, pix.n)[:, :, :3]
        # PyMuPDF gives RGB. PaddleOCR 2.x follows the OpenCV/BGR convention,
        # while the modern pipeline accepts the RGB numpy image directly.
        if _paddle_version < (3, 0):
            img_data = img_data[:, :, ::-1]

    # PaddleOCR 2.x accepts ``cls=True``; 3.x removed that argument.
    try:
        import inspect
        ocr_sig = inspect.signature(ocr.ocr)
        ocr_params = ocr_sig.parameters
    except (TypeError, ValueError):
        ocr_params = {}
    if "cls" in ocr_params:
        result = ocr.ocr(img_data, cls=True)
    else:
        result = ocr.ocr(img_data)
    blocks = []

    def add_block(box, text, conf):
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            return
        text = str(text or "").strip()
        if conf < 0.30 or len(text) < 2 or not box:
            return
        try:
            x0 = min(float(pt[0]) for pt in box) / scale
            y0 = min(float(pt[1]) for pt in box) / scale
            x1 = max(float(pt[0]) for pt in box) / scale
            y1 = max(float(pt[1]) for pt in box) / scale
        except (TypeError, ValueError, IndexError):
            return
        if x1 <= x0 or y1 <= y0:
            return
        blocks.append({
            "bbox": (x0, y0, x1, y1),
            "text": text,
            "font_size": max((y1 - y0) * 0.75, 10),
        })

    # PaddleOCR 3.x returns OCRResult objects/dicts with rec_texts,
    # rec_scores and rec_boxes/rec_polys.
    for item in result or []:
        rec_texts = getattr(item, "rec_texts", None)
        if rec_texts is None and isinstance(item, dict):
            rec_texts = item.get("rec_texts")
        rec_scores = getattr(item, "rec_scores", None)
        if rec_scores is None and isinstance(item, dict):
            rec_scores = item.get("rec_scores")
        rec_boxes = getattr(item, "rec_boxes", None)
        if rec_boxes is None and isinstance(item, dict):
            rec_boxes = item.get("rec_boxes")
        rec_polys = getattr(item, "rec_polys", None)
        if rec_polys is None and isinstance(item, dict):
            rec_polys = item.get("rec_polys")

        if rec_texts is not None and rec_scores is not None and (rec_boxes is not None or rec_polys is not None):
            boxes = rec_boxes if rec_boxes is not None else rec_polys
            for box, text, conf in zip(boxes, rec_texts, rec_scores):
                if hasattr(box, "tolist"):
                    box = box.tolist()
                # rec_boxes are [x0,y0,x1,y1], normalize to 4-point format.
                if len(box) == 4 and not isinstance(box[0], (list, tuple)):
                    x0, y0, x1, y1 = box
                    box = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                add_block(box, text, conf)
            continue

        # PaddleOCR 2.x: [[[points], (text, score)], ...]
        lines = item
        if isinstance(lines, dict):
            continue
        for line in lines or []:
            try:
                box, pair = line
                text, conf = pair
            except (TypeError, ValueError):
                continue
            add_block(box, text, conf)

    # Reading order: top-to-bottom, then left-to-right, with a small y bucket
    # so words on the same line do not jump around due to tiny bbox differences.
    blocks.sort(key=lambda b: (round(b["bbox"][1] / 4) * 4, b["bbox"][0]))
    return blocks

def extract_ocr_blocks_surya(page, lang="fas+eng"):
    """OCR با Surya 0.22.x؛ اختیاری و فقط در صورت نصب صحیح."""
    try:
        from surya.inference import SuryaInferenceManager
        from surya.recognition import RecognitionPredictor
    except ImportError as e:
        raise RuntimeError(
            "Surya OCR در این محیط قابل استفاده نیست. Python 3.10+ و نصب کامل "
            "surya-ocr لازم است.\n\n" + str(e)
        )

    from PIL import Image

    pix = page.get_pixmap(dpi=300, alpha=False)
    img = Image.frombytes("RGB", (pix.w, pix.h), pix.samples)
    recognition = _get_surya_engine()
    langs = []
    for raw in re.split(r"[+,]", lang or "eng"):
        raw = raw.strip().lower()
        if raw in {"fas", "fa", "persian"}:
            langs.append("fa")
        elif raw in {"eng", "en", "english"}:
            langs.append("en")
        elif raw:
            langs.append(raw)
    if not langs:
        langs = ["en"]

    predictions = recognition([img], [langs])
    blocks = []
    scale = 300 / 72
    try:
        pred = predictions[0] if predictions else None
        for line in getattr(pred, "text_lines", []) or []:
            text = str(getattr(line, "text", "") or "").strip()
            bbox = getattr(line, "bbox", None)
            if len(text) < 2 or not bbox or len(bbox) != 4:
                continue
            x0, y0, x1, y1 = map(float, bbox)
            blocks.append({
                "bbox": (x0 / scale, y0 / scale, x1 / scale, y1 / scale),
                "text": text,
                "font_size": max((y1 - y0) / scale * 0.75, 10),
            })
    finally:
        img.close()
    blocks.sort(key=lambda b: (round(b["bbox"][1] / 4) * 4, b["bbox"][0]))
    return blocks

def extract_ocr_blocks(page, lang="fas+eng", api_key=None, vision_model=None, ocr_method="auto"):
    """OCR خودکار یا انتخابی — بر اساس ocr_method."""
    tesseract_to_easyocr = {
        "fas": ["fa", "en"], "fas+eng": ["fa", "en"],
        "ara+eng": ["ar", "en"], "jpn": ["ja"],
        "chi_sim": ["ch_sim"], "eng": ["en"],
    }

    def _try_tesseract():
        if _is_tesseract_installed():
            try:
                return extract_ocr_blocks_tesseract(page, lang=lang)
            except Exception as e:
                console.print(f"  ⚠️ Tesseract: {e}")
        return None

    def _try_paddleocr():
        if _has_paddleocr():
            try:
                return extract_ocr_blocks_paddleocr(page, lang=lang)
            except Exception as e:
                console.print(f"  ⚠️ PaddleOCR: {e}")
        return None

    def _try_surya():
        if _has_surya():
            try:
                return extract_ocr_blocks_surya(page, lang=lang)
            except Exception as e:
                console.print(f"  ⚠️ Surya OCR: {e}")
        return None

    def _try_easyocr():
        if _has_easyocr():
            try:
                easyocr_langs = tesseract_to_easyocr.get(lang, ["fa", "en"])
                return extract_ocr_blocks_easyocr(page, languages=easyocr_langs)
            except Exception as e:
                console.print(f"  ⚠️ EasyOCR: {e}")
        return None

    def _try_vision():
        if api_key:
            try:
                blocks = extract_ocr_blocks_openrouter(page, api_key=api_key, model_name=vision_model)
                if blocks:
                    return blocks
            except Exception as e:
                console.print(f"  ⚠️ OpenRouter Vision: {e}")
        return None

    # انتخاب روش OCR
    if ocr_method == "tesseract":
        result = _try_tesseract()
        if result: return result
    elif ocr_method == "paddleocr":
        result = _try_paddleocr()
        if result: return result
    elif ocr_method == "surya":
        result = _try_surya()
        if result: return result
    elif ocr_method == "easyocr":
        result = _try_easyocr()
        if result: return result
    elif ocr_method == "openrouter":
        result = _try_vision()
        if result: return result
    else:
        # حالت خودکار: اولویت با PaddleOCR (دقیق‌ترین)
        for try_fn in [_try_paddleocr, _try_surya, _try_tesseract, _try_vision]:
            result = try_fn()
            if result: return result

    raise RuntimeError("OCR پیدا نشد! pip install paddleocr یا surya-ocr یا pytesseract")


_MARKER_RE = re.compile(r"\[\s*BLOCK[\s_]*(\d+)\s*\]", re.IGNORECASE)


def _sanitize_block_text(text: str) -> str:
    """جلوگیری از این‌که تگ‌های BLOCK داخل خود متن، پارس پاسخ را خراب کنند."""
    return _MARKER_RE.sub(lambda m: m.group(1), text or "")


def _parse_marker_output(result: str, count: int) -> list[str]:
    """پاسخ مدل را بر اساس تگ‌های BLOCK به لیست هم‌تراز تبدیل می‌کند."""
    translated = [""] * count
    if not result:
        return translated

    parts = re.split(r"\[\s*BLOCK[\s_]*(\d+)\s*\]\s*", result, flags=re.IGNORECASE)
    found = False
    for j in range(1, len(parts), 2):
        try:
            idx = int(parts[j])
        except (ValueError, IndexError):
            continue
        content = parts[j + 1].strip() if j + 1 < len(parts) else ""
        if 0 <= idx < count and content:
            translated[idx] = content
            found = True

    # فقط اگر مدل هیچ تگی نداد، تقسیم پاراگرافی را امتحان کن
    if not found and not _MARKER_RE.search(result):
        paragraphs = [p.strip() for p in result.split("\n\n") if p.strip()]
        for i in range(min(len(paragraphs), count)):
            translated[i] = paragraphs[i]
    return translated


def _translate_with_markers(translator, texts, target_lang):
    """ترجمهٔ بلاک‌های یک صفحه با حفظ دقیق ترتیب.

    اگر بخشی از بلاک‌ها بدون ترجمه بمانند (مدل تگ‌ها را گم کند)، همان بلاک‌ها
    تک‌به‌تک دوباره ترجمه می‌شوند تا نه متنی گم شود و نه ترجمه‌ای جابه‌جا شود.
    """
    if not texts:
        return texts

    safe_texts = [_sanitize_block_text(t) for t in texts]
    entries = [f"[BLOCK_{i}]\n{text}" for i, text in enumerate(safe_texts)]
    combined = "\n\n".join(entries)

    try:
        result = translator.translate(combined, target_lang) or ""
    except Exception as e:
        console.print(f"  ⚠️ خطا: {e}")
        result = ""

    translated = _parse_marker_output(result, len(texts))
    missing = [i for i, value in enumerate(translated) if not value.strip()]
    if missing:
        console.print(f"  ⚠️ {len(missing)} بلاک بدون ترجمه — تلاش مجدد تک‌به‌تک")
        for i in missing:
            try:
                single = translator.translate(safe_texts[i], target_lang) or ""
                if single.strip():
                    translated[i] = single
            except Exception as e:
                console.print(f"  ⚠️ بلاک {i}: {e}")

    return [_clean_translation(value) for value in translated]


def _write_persian_text(page, rect, text, fontsize, font_path, target_lang="fa"):
    """نوشتن متن فارسی/عربی در مستطیل — متن باید دقیقاً داخل باشه."""
    import pymupdf

    if not text or not text.strip():
        return False

    rect_height = rect.y1 - rect.y0
    rect_width = rect.x1 - rect.x0
    if rect_height < 3 or rect_width < 5:
        return False

    text = text.strip()
    if target_lang in _RTL_TARGETS:
        # insert_textbox نه شکل‌دهی حروف را انجام می‌دهد و نه راست‌به‌چپ‌سازی؛
        # متن باید پیش از نوشتن به ترتیب بصری تبدیل شود.
        text = _reshape(text)
    font_path = str(font_path if font_path else _FONT_PATH)

    # محاسبه سایز مناسب — متن باید داخل مستطیل جا بشه
    # حداکثر فونت = ارتفاع / (تعداد خطوط * ضریب)
    lines = text.split("\n")
    num_lines = max(len(lines), 1)

    # سایز مناسب: طوری باشه که متن کامل داخل مستطیل جا بشه
    ideal_fontsize = rect_height / (num_lines * 1.4)
    # محدوده معقول
    ideal_fontsize = min(ideal_fontsize, 18)
    ideal_fontsize = max(ideal_fontsize, 7)
    # از fontsize اصلی هم بزرگتر نشه
    ideal_fontsize = min(ideal_fontsize, max(fontsize, 7))

    candidate_sizes = []
    for fs in [ideal_fontsize, ideal_fontsize * 0.8, ideal_fontsize * 0.6, 7, 6, 5, 4]:
        fs = max(4.0, float(fs))
        if not any(abs(fs - prev) < 0.01 for prev in candidate_sizes):
            candidate_sizes.append(fs)

    for fs in candidate_sizes:
        try:
            rc = page.insert_textbox(
                pymupdf.Rect(
                    rect.x0 + 2, rect.y0 + 1,
                    rect.x1 - 2, rect.y1 - 1
                ),
                text,
                fontname=_FONT_NAME,
                fontfile=str(font_path),
                fontsize=fs,
                color=(0, 0, 0),
                align=(pymupdf.TEXT_ALIGN_RIGHT if target_lang in {"fa", "ar"} else pymupdf.TEXT_ALIGN_LEFT),
            )
            if rc >= 0:
                return True
        except Exception:
            continue

    return False


def translate_pdf_optimized(input_path: str, translator, target_lang: str = "fa",
                             output_path: str = None, batch_size: int = 5,
                             ocr_lang: str = "fas+eng",
                             api_key: str = None,
                             vision_model: str = None,
                             skip_pages: set = None,
                             precomputed_ocr: dict = None,
                             progress_callback=None,
                             on_empty_page=None) -> str:
    """ترجمه PDF — هر صفحه ترجمه در همون صفحه."""
    import pymupdf

    if output_path is None:
        p = Path(input_path)
        output_path = str(p.parent / f"{p.stem}_translated{p.suffix}")
    if skip_pages is None:
        skip_pages = set()
    if precomputed_ocr is None:
        precomputed_ocr = {}

    try:
        if Path(input_path).resolve() == Path(output_path).resolve():
            raise ValueError("مسیر خروجی نباید با فایل ورودی یکسان باشد")
    except FileNotFoundError:
        # resolve(strict=False) is normally fine; retain a readable error path
        # if a custom filesystem behaves differently.
        if os.path.abspath(str(input_path)) == os.path.abspath(str(output_path)):
            raise ValueError("مسیر خروجی نباید با فایل ورودی یکسان باشد")

    if not _FONT_PATH.exists():
        console.print(f"  ⚠️ فونت: {_FONT_PATH}")

    doc = pymupdf.open(input_path)
    total_pages = len(doc)

    # مرحله ۱: استخراج
    all_blocks = []
    ocr_count = 0
    need_ocr = False
    scanned_pages = []  # صفحات اسکن‌شده برای OCR دسته‌ای

    for page_num in range(total_pages):
        page = doc[page_num]
        if page_num in skip_pages:
            all_blocks.append([])
            continue

        blocks = extract_text_blocks(page)

        if not blocks:
            if on_empty_page:
                try:
                    user_choice = on_empty_page(page_num)
                except Exception:
                    user_choice = True
            else:
                user_choice = True

            if not user_choice:
                skip_pages.add(page_num)
                all_blocks.append([])
                continue

            # صفحه اسکن‌شده — برای OCR دسته‌ای جمع کن
            scanned_pages.append((page_num, page))
            all_blocks.append(None)  # موقت
            continue

        all_blocks.append(blocks)

        if progress_callback and (page_num % 10 == 0 or page_num == total_pages - 1):
            pct = (page_num + 1) / total_pages
            progress_callback(pct, f"📄 تحلیل صفحه {page_num + 1}/{total_pages}...")

    # OCR برای صفحات اسکن‌شده
    if scanned_pages:
        if precomputed_ocr:
            # استفاده از نتایج OCR از پیش‌انجام‌شده
            console.print(f"  🔍 استفاده از نتایج OCR از پیش‌انجام‌شده...")
            for page_num, _ in scanned_pages:
                blocks = precomputed_ocr.get(page_num, [])
                all_blocks[page_num] = blocks
                if blocks:
                    ocr_count += 1
                else:
                    skip_pages.add(page_num)
        elif _has_easyocr() or _has_paddleocr() or _has_surya() or _is_tesseract_installed():
            # OCR محلی — تکی
            ocr_engine = "EasyOCR" if _has_easyocr() else "Tesseract"
            console.print(f"  🔍 OCR فعال شد ({ocr_engine})")

            for page_num, page_obj in scanned_pages:
                try:
                    blocks = extract_ocr_blocks(page_obj, lang=ocr_lang, api_key=api_key, vision_model=vision_model)
                    all_blocks[page_num] = blocks
                    if blocks:
                        ocr_count += 1
                    else:
                        skip_pages.add(page_num)
                except Exception as e:
                    console.print(f"  ⚠️ OCR صفحه {page_num + 1}: {e}")
                    skip_pages.add(page_num)
                    all_blocks[page_num] = []
        else:
            # OpenRouter Vision — دسته‌ای (صرفه‌جویی توکن)
            console.print(f"  🔍 OCR Vision دسته‌ای ({len(scanned_pages)} صفحه)...")
            ocr_results = extract_ocr_blocks_batch_openrouter(
                scanned_pages, api_key=api_key, model_name=vision_model, batch_size=batch_size
            )
            for page_num, _ in scanned_pages:
                blocks = ocr_results.get(page_num, [])
                all_blocks[page_num] = blocks
                if blocks:
                    ocr_count += 1
                else:
                    skip_pages.add(page_num)

    total_blocks = sum(len(b) for b in all_blocks)
    console.print(f"  📊 {total_blocks} بلاک | {ocr_count} OCR | {len(skip_pages)} رد شد")

    if total_blocks == 0 and not skip_pages:
        doc.close()
        raise ValueError("متنی پیدا نشد!")

    # مرحله ۲: ترجمه
    console.print("  📝 ترجمه...")
    all_translated = {}

    for page_num in range(total_pages):
        if page_num in skip_pages:
            continue
        blocks = all_blocks[page_num]
        if not blocks:
            continue

        texts = [b["text"] for b in blocks]
        try:
            translated = _translate_with_markers(translator, texts, target_lang)
        except Exception as e:
            raise RuntimeError(f"ترجمه صفحه {page_num + 1} شکست خورد: {e}") from e
        if len(translated) != len(texts):
            raise RuntimeError(
                f"تعداد ترجمه‌های صفحه {page_num + 1} با تعداد بلاک‌ها برابر نیست "
                f"({len(translated)} در برابر {len(texts)})."
            )
        empty_indexes = [i for i, value in enumerate(translated) if not value or not value.strip()]
        if empty_indexes:
            # یک بلاک خالی نباید کل کتاب را متوقف کند؛ متن اصلی همان بلاک
            # حفظ می‌شود تا محتوا گم نشود و ترتیب بلاک‌ها جابه‌جا نشود.
            for i in empty_indexes:
                translated[i] = texts[i]
            console.print(
                f"  ⚠️ صفحه {page_num + 1}: {len(empty_indexes)} بلاک ترجمه نشد؛ "
                "متن اصلی آن بلاک حفظ شد"
            )
        all_translated[page_num] = translated

        if progress_callback:
            pct = (page_num + 1) / total_pages
            progress_callback(pct, f"📝 ترجمه صفحه {page_num + 1}/{total_pages} ({len(texts)} بلاک)...")

        # محدودیت سرعت باید توسط خود Translator/Pipeline مدیریت شود؛ اینجا delay ثابت نداریم.

    # مرحله ۳: نوشتن ترجمه روی صفحات
    # روش: redact متن اصلی + نوشتن ترجمه در همان موقعیت
    console.print("  🎨 ساخت PDF...")
    written_count = 0

    for page_num in range(total_pages):
        if page_num in skip_pages:
            continue
        blocks = all_blocks[page_num]
        page_results = all_translated.get(page_num, [])
        if not blocks or not page_results:
            continue

        page = doc[page_num]

        # اول: علامت‌گذاری همه بلاک‌ها برای redact
        for block in blocks:
            bbox = block["bbox"]
            # اضافه کردن کمی حاشیه برای پوشش کامل
            rect = pymupdf.Rect(bbox)
            rect.y0 -= 1
            rect.y1 += 1
            # fill=None: هیچ مستطیل سفیدی روی صفحه یا عکس کشیده نمیشود
            page.add_redact_annot(rect, fill=None)

        # اعمال redact — متن اصلی حذف میشه، عکس‌ها باقی میمونه
        # فقط متن حذف میشود؛ تصاویر و خطوط گرافیکی صفحه دستنخورده میمانند
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
        )

        # حالا: نوشتن ترجمه در موقعیت اصلی
        for i, block in enumerate(blocks):
            text = page_results[i] if i < len(page_results) else ""
            if not text.strip():
                continue

            bbox = block["bbox"]
            fontsize = block["font_size"]
            rect = pymupdf.Rect(bbox)

            # تبدیل RTL داخل _write_persian_text انجام می‌شود (نه دو بار)
            display_text = text

            ok = _write_persian_text(page, rect, display_text, fontsize, _FONT_PATH, target_lang=target_lang)

            if ok:
                written_count += 1

    console.print(f"  ✍️  {written_count}/{total_blocks} بلاک")

    try:
        doc.save(output_path)
        console.print(f"  📄 ذخیره شد: [bold green]{output_path}[/]")
        return output_path
    except Exception as e:
        console.print(f"  ❌ خطا در ذخیره: {e}")
        return ""
    finally:
        doc.close()
