"""Resilient vision OCR for real scanned pages, used by the Chinese PDF run.

Free vision models are flaky in two ways that matter for a 3-page scan:

* reasoning models burn the whole token budget on hidden reasoning and return
  ``content: null`` (the bug fixed in ``_openrouter_vision_request``), and
* dense pages still overflow even with a larger budget.

So this helper tries each model with a realistic budget, and if a page still comes
back empty it splits the page into horizontal bands and OCRs those separately.
Everything is cached per page, so a re-run never pays twice.
"""
from __future__ import annotations

import json
import pathlib
import time

import pymupdf

from trarium.pdf_optimized import (
    _bbox_to_blocks,
    _parse_vision_json,
    _vision_prompt,
    _openrouter_vision_request,
    OCR_DPI,
    OCR_JPEG_QUALITY,
    OCR_MAX_SIDE,
)

MODELS = (
    ("nvidia/nemotron-3.5-super-vl:free", 12000),
    ("google/gemma-4-31b-it:free", 8000),
    ("particle-ai/particle-1:free", 8000),
    ("dots-studio/dots-3-note-preview:free", 8000),
)


def _data_url(page, clip=None) -> tuple[str, int, int]:
    import base64

    pix = page.get_pixmap(dpi=OCR_DPI, clip=clip, alpha=False)
    longest = max(pix.width, pix.height)
    if longest > OCR_MAX_SIDE:
        scale = OCR_MAX_SIDE / longest
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale * OCR_DPI / 72, scale * OCR_DPI / 72),
                              clip=clip, alpha=False)
    blob = pix.tobytes("jpeg", jpg_quality=OCR_JPEG_QUALITY)
    return f"data:image/jpeg;base64,{base64.b64encode(blob).decode()}", pix.width, pix.height


def _blocks_for(page, key: str, clip, api_key: str, model: str) -> list[dict]:
    url, img_w, img_h = _data_url(page, clip)
    raw = _openrouter_vision_request(api_key, model, [url], _vision_prompt(False, [1]), 1)
    rect = clip or page.rect
    blocks = _bbox_to_blocks(_parse_vision_json(raw), rect.width, rect.height, img_w, img_h)
    if clip:
        for block in blocks:
            x0, y0, x1, y1 = block["bbox"]
            block["bbox"] = (x0 + clip.x0, y0 + clip.y0, x1 + clip.x0, y1 + clip.y0)
    return blocks


def _cjk_count(blocks) -> int:
    return sum(1 for b in blocks for ch in b["text"] if "\u4e00" <= ch <= "\u9fff")


def ocr_page(page, api_key: str, cache: pathlib.Path) -> tuple[list[dict], str]:
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return [{"bbox": tuple(b["bbox"]), "text": b["text"], "font_size": b["font_size"]}
                for b in data["blocks"]], f"cache ({data['model']})"
    errors = []
    for model, _budget in MODELS:
        for attempt in range(1):
            try:
                blocks = _blocks_for(page, "full", None, api_key, model)
                if _cjk_count(blocks) >= 30:
                    cache.write_text(json.dumps({"model": model, "blocks": blocks}, ensure_ascii=False),
                                     encoding="utf-8")
                    return blocks, model
                errors.append(f"{model}: only {_cjk_count(blocks)} CJK chars")
            except Exception as exc:  # noqa: BLE001 - provider errors are expected
                errors.append(f"{model}: {str(exc)[:90]}")
            time.sleep(5)
        # last resort for this model: two horizontal bands
        try:
            rect = page.rect
            half = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height / 2)
            lower = pymupdf.Rect(rect.x0, rect.y0 + rect.height / 2, rect.x1, rect.y1)
            blocks = (_blocks_for(page, "top", half, api_key, model)
                      + _blocks_for(page, "bottom", lower, api_key, model))
            if _cjk_count(blocks) >= 30:
                cache.write_text(json.dumps({"model": model + " (tiled)", "blocks": blocks},
                                            ensure_ascii=False), encoding="utf-8")
                return blocks, model + " (tiled)"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{model} tiled: {str(exc)[:90]}")
    raise RuntimeError("OCR failed for this page: " + " | ".join(errors[-4:]))
