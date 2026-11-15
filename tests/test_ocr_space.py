"""Tests for the OCR.space provider (free tier only).

Network is never touched: ``requests.post`` is replaced with a fake that returns
an OCR.space-shaped payload, so the URL, headers, form fields and the coordinate
conversion are all exercised offline.
"""
from __future__ import annotations

import io
import json

import pymupdf
import pytest

from trarium import ocr_space
from trarium.pdf_optimized import translate_pdf_optimized


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _overlay_payload(lines):
    words = []
    for text, left, top, width, height in lines:
        words.append({"WordText": text, "Left": left, "Top": top, "Width": width, "Height": height})
    return {
        "ParsedResults": [
            {
                "ParsedText": "".join(line[0] for line in lines),
                "TextOverlay": {
                    "Lines": [
                        {
                            "LineText": text,
                            "Words": [w],
                            "MaxHeight": height,
                            "MinTop": top,
                        }
                        for (text, left, top, width, height), w in zip(lines, words)
                    ],
                    "HasOverlay": True,
                },
            }
        ],
        "OCRExitCode": 1,
        "IsErroredOnProcessing": False,
        "ErrorMessage": None,
    }


@pytest.fixture()
def captured(monkeypatch):
    calls = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        calls.append({"url": url, "headers": headers, "data": data, "files": files})
        return _FakeResponse(_overlay_payload([("全球最佳动漫盘点", 10, 20, 200, 30)]))

    monkeypatch.setattr(ocr_space.requests, "post", fake_post)
    return calls


def test_language_codes_map_to_ocr_space(monkeypatch):
    assert ocr_space.language_codes("zh") == "chs"
    assert ocr_space.language_codes("chi_sim+eng") == "chs"
    assert ocr_space.language_codes("fas+eng") == "ara"
    assert ocr_space.language_codes(None) == "eng"


def test_free_engine_only():
    with pytest.raises(ocr_space.OcrSpaceError):
        ocr_space.ocr_image_bytes(b"x", engine=3)


def test_oversized_image_is_refused_before_upload():
    with pytest.raises(ocr_space.OcrSpaceError) as exc:
        ocr_space.ocr_image_bytes(b"0" * (ocr_space.MAX_BYTES + 1))
    assert "۱ مگابایت" in str(exc.value)


def test_request_uses_documented_fields(captured):
    ocr_space.ocr_image_bytes(b"jpeg", language="chs", engine=2)
    call = captured[0]
    assert call["url"] == ocr_space.ENDPOINT
    assert call["headers"]["apikey"]
    assert call["data"]["language"] == "chs"
    assert call["data"]["isOverlayRequired"] == "true"
    assert call["data"]["OCREngine"] == "2"


def test_key_resolution_prefers_argument_then_env(monkeypatch):
    monkeypatch.delenv(ocr_space.ENV_KEY, raising=False)
    monkeypatch.setenv("OCR_SPACE_API_KEY", "env-key")
    assert ocr_space.api_key() == "env-key"
    assert ocr_space.api_key("explicit") == "explicit"
    assert ocr_space.has_real_key() is True


def test_demo_key_is_reported_as_limited(monkeypatch):
    for name in (ocr_space.ENV_KEY, *ocr_space.ALT_ENV_KEYS):
        monkeypatch.delenv(name, raising=False)
    assert ocr_space.api_key() == ocr_space.DEMO_KEY
    assert ocr_space.has_real_key() is False
    assert "ocr.space/ocrapi/freekey" in ocr_space.availability_hint()


def test_api_error_becomes_provider_error(monkeypatch):
    monkeypatch.setattr(
        ocr_space.requests, "post",
        lambda *a, **k: _FakeResponse(
            {"ParsedResults": None, "IsErroredOnProcessing": True,
             "ErrorMessage": ["E101: key invalid"]}, 200
        ),
    )
    with pytest.raises(ocr_space.OcrSpaceError) as exc:
        ocr_space.ocr_image_bytes(b"jpeg")
    assert "E101" in str(exc.value)


def test_blocks_are_scaled_to_pdf_points():
    payload = _overlay_payload([("标题", 100, 50, 200, 40)])
    blocks = ocr_space.build_blocks(payload, page_width=550.0, image_width=1100)
    assert len(blocks) == 1
    x0, y0, x1, y1 = blocks[0]["bbox"]
    assert x0 == pytest.approx(50.0)
    assert y0 == pytest.approx(25.0)
    assert x1 == pytest.approx(150.0)
    assert y1 == pytest.approx(45.0)
    assert blocks[0]["text"] == "标题"


def test_page_ocr_returns_blocks(captured, tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "scan")
    blocks = ocr_space.ocr_page(page, language="chi_sim", dpi=150)
    doc.close()
    assert blocks and blocks[0]["text"] == "全球最佳动漫盘点"
    assert captured, "no request was sent"


def test_document_ocr_covers_every_page(captured, tmp_path):
    path = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page()
    doc.save(path)
    doc.close()
    blocks, meta = ocr_space.ocr_document_blocks(path, language="zh")
    assert set(blocks) == {0, 1}
    assert meta["requests"] == 2
    assert meta["language"] == "chs"


def test_empty_result_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ocr_space.requests, "post",
        lambda *a, **k: _FakeResponse({"ParsedResults": [{"TextOverlay": {"Lines": []}}],
                                       "IsErroredOnProcessing": False}),
    )
    path = tmp_path / "blank.pdf"
    doc = pymupdf.open(); doc.new_page(); doc.save(path); doc.close()
    with pytest.raises(ocr_space.OcrSpaceError):
        ocr_space.ocr_document_blocks(path)


class _OfflineTranslator:
    provider = "offline"
    model = "offline-1"
    last_usage: dict = {}

    def translate(self, text: str, target_lang: str = "fa") -> str:
        return "ترجمهٔ آزمایشی."  # no source text, so it is not flagged as an echo


def test_pipeline_uses_ocr_space_blocks(monkeypatch, tmp_path):
    path = tmp_path / "scan.pdf"
    # Pages with a drawing but no text layer, like a real scan.
    clean = pymupdf.open()
    for _ in range(2):
        clean.new_page().draw_rect(pymupdf.Rect(0, 0, 400, 500), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    clean.save(path)
    clean.close()

    calls = {}

    def fake_document(pdf, language=None, dpi=200, key=None, max_pages=None, progress=None):
        calls["language"] = language
        return (
            {0: [{"bbox": (60, 60, 400, 90), "text": "全球最佳动漫盘点", "font_size": 16}],
             1: [{"bbox": (60, 60, 400, 90), "text": "经典不灭", "font_size": 16}]},
            {"engine": "ocr.space", "pages": 2, "requests": 2, "chars": 12},
        )

    monkeypatch.setattr(ocr_space, "ocr_document_blocks", fake_document)

    output = tmp_path / "out.pdf"
    result, stats = translate_pdf_optimized(
        str(path), _OfflineTranslator(), "fa", str(output), 1,
        enable_ocr=True, ocr_method="ocrspace", ocr_lang="chi_sim+eng",
        skip_pages=set(), translation_workers=1, return_stats=True,
        use_cache=False, use_checkpoint=False,
    )
    assert calls["language"] == "chi_sim+eng"
    assert stats.translated_blocks >= 2
    assert output.exists()


def test_adjacent_lines_are_merged_and_cjk_has_no_space():
    payload = _overlay_payload([("第一行", 10, 20, 100, 20), ("第二行", 10, 42, 100, 20)])
    blocks = ocr_space.build_blocks(payload, page_width=100.0, image_width=100)
    assert len(blocks) == 1
    assert blocks[0]["text"] == "第一行第二行"


def test_separate_columns_are_not_merged():
    payload = _overlay_payload([("left", 0, 20, 40, 20), ("right", 200, 20, 40, 20)])
    blocks = ocr_space.build_blocks(payload, page_width=300.0, image_width=300)
    assert len(blocks) == 2


def test_merge_stops_after_four_lines():
    lines = [("خط", 10, 20 + i * 22, 100, 20) for i in range(8)]
    blocks = ocr_space.build_blocks(_overlay_payload(lines), page_width=100.0, image_width=100)
    assert len(blocks) == 2, "a paragraph must not swallow more than four lines"
    assert all("_lines" not in b for b in blocks)
