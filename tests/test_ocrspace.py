"""OCR.space engine tests (HTTP is stubbed, no network, no real key)."""
import unittest
from unittest import mock

import pymupdf

from trarium import pdf_optimized as po


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)[:400]

    def json(self):
        return self._payload


def _page(width=400, height=560):
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((50, 100), "Hello OCR space", fontsize=12)
    return doc, page


def _overlay(words):
    return {"ParsedResults": [{"ParsedText": " ".join(w["WordText"] for w in words),
                               "TextOverlay": {"Lines": [{"Words": words}]}}],
            "IsErroredOnProcessing": False}


class LanguageMappingTests(unittest.TestCase):
    def test_english_uses_engine_two(self):
        self.assertEqual(po._ocrspace_language("eng"), ("eng", "2"))
        self.assertEqual(po._ocrspace_language("eng+fas"), ("eng", "2"))

    def test_persian_uses_engine_three(self):
        self.assertEqual(po._ocrspace_language("fas"), ("fas", "3"))
        self.assertEqual(po._ocrspace_language("fa"), ("fas", "3"))

    def test_unknown_language_falls_back_to_auto(self):
        self.assertEqual(po._ocrspace_language("klingon"), ("auto", "3"))


class OcrspaceRequestTests(unittest.TestCase):
    def setUp(self):
        self.doc, self.page = _page()
        self.blob, self.image_w, self.image_h = po._render_page_jpeg(self.page)

    def tearDown(self):
        self.doc.close()

    def test_word_boxes_are_converted_to_pdf_points(self):
        # Two words on one line, expressed in pixel coordinates of the upload.
        words = [
            {"WordText": "Hello", "Left": 10, "Top": 20, "Width": 40, "Height": 10},
            {"WordText": "OCR", "Left": 55, "Top": 20, "Width": 30, "Height": 10},
        ]
        with mock.patch.object(po.requests, "post", return_value=FakeResponse(_overlay(words))) as post:
            blocks = po.extract_ocr_blocks_ocrspace(self.page, api_key="test-key", lang="eng")
        self.assertEqual(len(blocks), 1)
        x0, y0, x1, y1 = blocks[0]["bbox"]
        scale_x = self.page.rect.width / self.image_w
        scale_y = self.page.rect.height / self.image_h
        self.assertAlmostEqual(x0, 10 * scale_x, places=3)
        self.assertAlmostEqual(y0, 20 * scale_y, places=3)
        self.assertAlmostEqual(x1, 85 * scale_x, places=3)
        self.assertAlmostEqual(y1, 30 * scale_y, places=3)
        self.assertEqual(blocks[0]["text"], "Hello OCR")

        # The request must ask for the overlay and carry the key + language.
        _, kwargs = post.call_args
        self.assertEqual(kwargs["data"]["apikey"], "test-key")
        self.assertEqual(kwargs["data"]["isOverlayRequired"], "true")
        self.assertEqual(kwargs["data"]["language"], "eng")
        self.assertEqual(kwargs["data"]["OCREngine"], "2")
        self.assertIn("file", kwargs["files"])

    def test_two_lines_stay_two_blocks(self):
        words = [
            {"WordText": "first", "Left": 10, "Top": 20, "Width": 40, "Height": 10},
            {"WordText": "second", "Left": 10, "Top": 200, "Width": 40, "Height": 10},
        ]
        payload = {"ParsedResults": [{"TextOverlay": {"Lines": [{"Words": [words[0]]}, {"Words": [words[1]]}]}}],
                   "IsErroredOnProcessing": False}
        with mock.patch.object(po.requests, "post", return_value=FakeResponse(payload)):
            blocks = po.extract_ocr_blocks_ocrspace(self.page, api_key="k", lang="eng")
        self.assertEqual(len(blocks), 2)
        self.assertLess(blocks[0]["bbox"][1], blocks[1]["bbox"][1])

    def test_missing_key_is_reported(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                po.extract_ocr_blocks_ocrspace(self.page)
        self.assertIn("OCR_SPACE_API_KEY", str(caught.exception))

    def test_api_error_is_reported(self):
        payload = {"IsErroredOnProcessing": True, "ErrorMessage": ["E201: quota reached"]}
        with mock.patch.object(po.requests, "post", return_value=FakeResponse(payload)):
            with self.assertRaises(RuntimeError) as caught:
                po.extract_ocr_blocks_ocrspace(self.page, api_key="k")
        self.assertIn("quota", str(caught.exception))

    def test_empty_overlay_is_an_error_not_silent_success(self):
        payload = {"IsErroredOnProcessing": False, "ParsedResults": [{"ParsedText": "text only"}]}
        with mock.patch.object(po.requests, "post", return_value=FakeResponse(payload)):
            with self.assertRaises(RuntimeError):
                po.extract_ocr_blocks_ocrspace(self.page, api_key="k")


class AutoChainTests(unittest.TestCase):
    def test_ocrspace_is_used_when_a_key_exists(self):
        with mock.patch.dict("os.environ", {"OCR_SPACE_API_KEY": "k"}, clear=True):
            chain = po._auto_ocr_methods()
        self.assertIn("ocrspace", chain)
        self.assertLess(chain.index("ocrspace"), chain.index("openrouter"))

    def test_no_hosted_engine_without_keys(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            chain = po._auto_ocr_methods()
        self.assertNotIn("ocrspace", chain)
        self.assertNotIn("gemini", chain)


if __name__ == "__main__":
    unittest.main()
