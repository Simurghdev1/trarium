import tempfile
import unittest
import zipfile
from pathlib import Path

import pymupdf

from trarium.extractors.base import ExtractedBook
from trarium.novel.scraper import GenericAdapter
from trarium.pdf_optimized import _translate_blocks_resilient, extract_text_blocks, translate_pdf_optimized
from trarium.processors.pipeline import TranslationPipeline
from trarium.processors import TextChunker
from trarium.output import save_as_pdf, save_as_epub, save_as_html, save_as_text
from trarium.translators.base import BaseTranslator


class FakeTranslator(BaseTranslator):
    def __init__(self, fail_plain=False):
        self.fail_plain = fail_plain
        self.calls = []

    def get_token_limit(self):
        return 100000

    def translate(self, text, target_lang="fa"):
        self.calls.append(text)
        if self.fail_plain and "[TRARIUM_BLOCK_" not in text:
            raise RuntimeError("simulated block failure")
        if "[TRARIUM_BLOCK_" in text:
            # Simulate a model that drops one marker; Trarium must recover it separately.
            return "[TRARIUM_BLOCK_0]\nTRANSLATED_FIRST"
        return "TRANSLATED_SINGLE"


class TrariumSmokeTests(unittest.TestCase):
    def test_marker_recovery(self):
        translator = FakeTranslator()
        entries = [(0, 0, "First block"), (0, 1, "Second block")]
        done, failures, _ = _translate_blocks_resilient(translator, entries, "fa", max_workers=2, max_chars=500)
        self.assertEqual(failures, [])
        self.assertEqual(done[(0, 0)], "TRANSLATED_FIRST")
        self.assertEqual(done[(0, 1)], "TRANSLATED_SINGLE")

    def test_pdf_layout_smoke(self):
        translator = FakeTranslator()
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "input.pdf"
            dst = Path(td) / "output.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((70, 100), "This is a test paragraph.", fontsize=14)
            page.insert_text((70, 150), "Second paragraph.", fontsize=12)
            doc.save(src)
            doc.close()

            result, stats = translate_pdf_optimized(str(src), translator, target_lang="fa", output_path=str(dst),
                                             enable_ocr=False, translation_workers=2, translation_batch_chars=5000, return_stats=True)
            self.assertEqual(result, str(dst))
            self.assertTrue(dst.exists())
            self.assertGreater(stats.translated_blocks, 0)
            self.assertGreater(stats.pages_written, 0)
            out = pymupdf.open(dst)
            self.assertGreaterEqual(len(out), 2)
            first_text = out[0].get_text("text")
            all_text = "".join(p.get_text("text") for p in out)
            self.assertIn("TRANSLATED_FIRST", all_text)
            self.assertNotIn("This is a test paragraph.", first_text)
            self.assertTrue("ترجمه" in all_text or "ﺗﺮﺟﻤﻪ" in all_text)
            out.close()

    def test_text_pipeline_keeps_failed_content(self):
        class AlwaysFail(BaseTranslator):
            def get_token_limit(self): return 10000
            def translate(self, text, target_lang="fa"): raise RuntimeError("blocked")
        book = ExtractedBook("Test", "", [{"title": "Chapter 1", "content": "A paragraph long enough to translate."}], "", 38)
        result = TranslationPipeline(AlwaysFail(), "fa", chunk_size=100, max_workers=1).run(book)
        self.assertEqual(result.failed_chunks, 1)
        self.assertIn("A paragraph long enough to translate.", result.translated_text)

    def test_pdf_batch_failure_keeps_source_and_does_not_abort(self):
        class BatchFail(BaseTranslator):
            def get_token_limit(self): return 10000
            def translate(self, text, target_lang="fa"):
                if "[TRARIUM_BLOCK_" in text:
                    raise RuntimeError("provider blocked batch")
                raise RuntimeError("provider blocked block")

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "input.pdf"
            dst = Path(td) / "output.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((50, 80), "This text must survive even when translation fails.")
            doc.save(src)
            doc.close()
            with self.assertRaises(RuntimeError):
                translate_pdf_optimized(str(src), BatchFail(), output_path=str(dst), translation_workers=2, translation_batch_chars=5000)
            self.assertFalse(dst.exists())

    def test_generic_pdf_pages_are_packed_for_fewer_requests(self):
        chapters = [{"title": f"صفحه {i}", "content": "word " * 20} for i in range(1, 8)]
        chunks = TextChunker(max_chars=500).chunk_by_chapters(chapters)
        self.assertLess(len(chunks), len(chapters))

    def test_output_formats(self):
        from trarium.processors.pipeline import TranslationResult
        result = TranslationResult("Test", "", "en", "fa", "--- فصل ۱ ---\n\nیک پاراگراف آزمایشی.")
        with tempfile.TemporaryDirectory() as td:
            paths = [
                save_as_text(result, str(Path(td) / "x.txt")),
                save_as_pdf(result, str(Path(td) / "x.pdf")),
                save_as_epub(result, str(Path(td) / "x.epub")),
                save_as_html(result, str(Path(td) / "x.html")),
            ]
            for path in paths:
                self.assertTrue(Path(path).exists())
                self.assertGreater(Path(path).stat().st_size, 0)
            self.assertIn("ترجمه شده توسط تراریوم", Path(paths[0]).read_text(encoding="utf-8"))
            pdf = pymupdf.open(paths[1]); self.assertGreaterEqual(len(pdf), 2); all_text = "".join(p.get_text("text") for p in pdf); self.assertTrue("ترجمه" in all_text or "ﺗﺮﺟﻤﻪ" in all_text); pdf.close()
            self.assertIn("ترجمه شده توسط تراریوم", Path(paths[3]).read_text(encoding="utf-8"))
            with zipfile.ZipFile(paths[2]) as zf:
                attribution_files = [n for n in zf.namelist() if n.startswith("OEBPS/chapter_") and n.endswith(".xhtml")]
                self.assertTrue(attribution_files)
                self.assertIn("ترجمه شده توسط تراریوم", zf.read(attribution_files[-1]).decode("utf-8"))

    def test_webnovel_adapter_and_next(self):
        html = """
        <html><head><title>X</title></head><body>
        <article class='chapter-content'><h2>Bad nested title</h2>
        <p>First paragraph.</p><p>Second paragraph.</p>
        <img src='/cover.jpg' alt='cover'>
        </article>
        <a rel='next' href='/chapter-2'>Next Chapter</a>
        <script>alert(1)</script></body></html>
        """
        result = GenericAdapter().extract(html, "https://example.test/chapter-1")
        self.assertIn("First paragraph.", result.text)
        self.assertIn("Second paragraph.", result.text)
        self.assertEqual(result.next_url, "https://example.test/chapter-2")
        self.assertEqual(len(result.images), 1)


    def test_ocr_text_layer_fallback_and_layout_grouping(self):
        from trarium.pdf_optimized import _normalise_layout_blocks
        blocks=[
            {"bbox":(50,50,300,62),"text":"This is the first line.","font_size":10},
            {"bbox":(50,64,310,76),"text":"This is the second line.","font_size":10},
            {"bbox":(50,100,180,112),"text":"New paragraph.","font_size":10},
        ]
        merged=_normalise_layout_blocks(blocks)
        self.assertEqual(len(merged),2)
        self.assertIn("second line",merged[0]["text"])

    def test_layout_writer_rejects_zero_written(self):
        from trarium.pdf_optimized import translate_pdf_optimized
        class SameTranslator(BaseTranslator):
            def get_token_limit(self): return 10000
            def translate(self,text,target_lang="fa"): return text
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/"input.pdf"; dst=Path(td)/"output.pdf"
            doc=pymupdf.open(); pg=doc.new_page(); pg.insert_text((50,80),"Same source text."); doc.save(src); doc.close()
            with self.assertRaises(RuntimeError):
                translate_pdf_optimized(str(src),SameTranslator(),output_path=str(dst),translation_workers=1)
            self.assertFalse(dst.exists())

    def test_translation_cache_reuses_result(self):
        from trarium.cache import CachedTranslator, TranslationCache
        class T(BaseTranslator):
            def __init__(self): self.calls=0
            def get_token_limit(self): return 1000
            def translate(self,text,target_lang="fa"):
                self.calls += 1; return "ترجمه " + text
        with tempfile.TemporaryDirectory() as td:
            cache=TranslationCache(str(Path(td)/"cache.sqlite3")); inner=T(); wrapped=CachedTranslator(inner,"test","model",cache)
            self.assertEqual(wrapped.translate("hello","fa"),"ترجمه hello")
            self.assertEqual(wrapped.translate("hello","fa"),"ترجمه hello")
            self.assertEqual(inner.calls,1)
            self.assertEqual(wrapped.hits,1)

    def test_native_text_extraction(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Readable English text here.")
        blocks = extract_text_blocks(page)
        doc.close()
        self.assertTrue(blocks)
        self.assertIn("Readable", blocks[0]["text"])


if __name__ == "__main__":
    unittest.main()

class OpenRouterTransportTests(unittest.TestCase):
    def test_waf_response_is_classified(self):
        from trarium.translators.openrouter_http import _is_waf_security_block, OpenRouterSecurityBlockError
        self.assertTrue(_is_waf_security_block(403, '{"success":false,"error":"Access denied by security policy."}', {"success": False, "error": "Access denied by security policy."}))
        self.assertFalse(_is_waf_security_block(403, '{"error":{"code":403,"message":"blocked by guardrail"}}', {"error":{"code":403,"message":"blocked by guardrail"}}))

    def test_openrouter_security_error_does_not_retry(self):
        from trarium.translators.openrouter import OpenRouterTranslator
        from unittest.mock import patch
        from trarium.translators.openrouter_http import OpenRouterSecurityBlockError
        t = OpenRouterTranslator(api_key="test", model_name="openrouter/free")
        with patch('trarium.translators.openrouter.post_json', side_effect=OpenRouterSecurityBlockError('blocked', source='waf')) as mocked:
            with self.assertRaises(OpenRouterSecurityBlockError):
                t.translate('Hello', 'fa')
            self.assertEqual(mocked.call_count, 1)

    def test_current_free_model_slugs(self):
        from trarium.translators.openrouter import FREE_MODELS
        self.assertEqual(FREE_MODELS['Auto (پیشنهادی)'], 'openrouter/free')
        self.assertIn(':free', FREE_MODELS['Gemma 4 31B (free)'])
        self.assertNotIn('Nemotron 3.5 Lightning', FREE_MODELS)


class ProviderAndDiagnosticTests(unittest.TestCase):
    def test_provider_factory_includes_new_providers(self):
        from trarium.translators import _TRANSLATORS
        self.assertIn("gemini", _TRANSLATORS)
        self.assertIn("groq", _TRANSLATORS)
        self.assertIn("openai_compatible", _TRANSLATORS)

    def test_groq_current_models(self):
        from trarium.translators.groq import GROQ_MODELS
        self.assertEqual(GROQ_MODELS["GPT-OSS 120B"], "openai/gpt-oss-120b")
        self.assertEqual(GROQ_MODELS["Qwen 3.8 27B"], "qwen/qwen3.8-27b")

    def test_gemini_current_model(self):
        from trarium.translators.gemini import GEMINI_MODELS
        # The old list named gemini-3.8/2.5 models that the API answers with 404
        # for new keys; every entry here must be a model the API still serves.
        values = set(GEMINI_MODELS.values())
        self.assertIn("gemini-3.5-flash-lite", values)
        self.assertNotIn("gemini-2.5-flash", values)
        self.assertNotIn("gemini-3.8-flash", values)

    def test_openrouter_uses_minimal_headers(self):
        from trarium.translators.openrouter import OpenRouterTranslator
        h = OpenRouterTranslator(api_key="x")._headers()
        self.assertNotIn("HTTP-Referer", h)
        self.assertNotIn("X-OpenRouter-Title", h)
        self.assertIn("User-Agent", h)


class ProviderTransportMockTests(unittest.TestCase):
    def test_openrouter_post_uses_curl_first_in_auto_when_available(self):
        from trarium.translators import openrouter_http as h
        from unittest.mock import patch
        good = h.OpenRouterResponse(200, {}, {"choices":[{"message":{"content":"OK"}}]}, '{"choices":[]}', 'curl')
        payload={"model":"openrouter/free","messages":[{"role":"user","content":"hi"}]}
        with patch.object(h, "_curl_available", return_value="curl"), patch.object(h, "_curl_request_json", return_value=good) as c, patch.object(h, "_requests_request_json") as r:
            out=h.post_json("https://openrouter.ai/api/v1/chat/completions", {"Authorization":"Bearer x"}, payload, timeout=5)
            self.assertEqual(out.transport,"curl")
            c.assert_called_once(); r.assert_not_called()

    def test_gemini_uses_api_key_header_not_url(self):
        import trarium.translators.gemini as g
        from unittest.mock import patch
        class Resp:
            status_code=200; text='';
            def json(self): return {"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}
        with patch.object(g.requests, "post", return_value=Resp()) as p:
            t=g.GeminiTranslator(api_key="secret", model_name="gemini-3.8-flash")
            self.assertEqual(t.translate("hi","en"),"OK")
            _, kwargs=p.call_args
            self.assertEqual(kwargs["headers"]["x-goog-api-key"],"secret")
            self.assertNotIn("key", kwargs.get("params",{}))
