import tempfile
import unittest
from pathlib import Path

from trarium.cache import TranslationCache
from trarium.checkpoint import CheckpointStore
from trarium.extractors.base import ExtractedBook
from trarium.placeholders import protect, restore
from trarium.preflight import estimate_book, probe
from trarium.processors.pipeline import TranslationPipeline
from trarium.scheduler import FallbackTranslator
from trarium.term_registry import TermRegistry
from trarium.translators.base import BaseTranslator


class Fake(BaseTranslator):
    def __init__(self, name="fake", fail=False):
        self.provider = name
        self.model_name = "test-model"
        self.calls = 0
        self.fail = fail

    def get_token_limit(self):
        return 10000

    def translate(self, text, target_lang="fa"):
        self.calls += 1
        if self.fail:
            raise RuntimeError("blocked")
        if "__TRARIUM_PH_0000__" in text:
            return text.replace("__TRARIUM_PH_0000__", "__TRARIUM_PH_0000__") + " [ترجمه]"
        return "[ترجمه] " + text


class V217Tests(unittest.TestCase):
    def test_placeholder_roundtrip(self):
        p = protect("See https://example.com and `code()`.")
        self.assertIn("__TRARIUM_PH_", p.text)
        self.assertEqual(restore(p.text, p.mapping), "See https://example.com and `code()`.")

    def test_term_registry(self):
        with tempfile.TemporaryDirectory() as td:
            reg = TermRegistry(str(Path(td) / "terms.json"))
            reg.set("Subaru", "سوبارو")
            self.assertIn("Subaru -> سوبارو", reg.prompt("Subaru looked at Emilia"))

    def test_preflight_estimation_and_probe(self):
        total, batches = estimate_book(["hello " * 100, "world " * 100], batch_chars=300)
        self.assertGreater(total, 0)
        self.assertGreater(batches, 1)
        report = probe(Fake(), "fa")
        self.assertTrue(report.ok)
        self.assertGreater(report.token_limit, 0)

    def test_pipeline_translation_memory_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            cache = TranslationCache(str(Path(td) / "cache.sqlite3"))
            cp = CheckpointStore(str(Path(td) / "cp.sqlite3"))
            tr = Fake()
            book = ExtractedBook("T", "", [{"title":"Chapter 1", "content":"Hello world.\n\nHello world."}], "", 26)
            pipe = TranslationPipeline(tr, "fa", chunk_size=4000, max_workers=1, cache=cache, checkpoint=cp)
            first = pipe.run(book)
            calls = tr.calls
            second = pipe.run(book)
            self.assertEqual(calls, tr.calls)
            self.assertTrue(second.cache_hits + second.checkpoint_hits >= 1)
            self.assertIn("[ترجمه]", first.translated_text)

    def test_dynamic_provider_failover(self):
        primary = Fake("primary", fail=True)
        backup = Fake("backup", fail=False)
        router = FallbackTranslator([("primary", primary), ("backup", backup)], cooldown_seconds=0)
        out = router.translate("hello", "fa")
        self.assertIn("[ترجمه]", out)
        self.assertEqual(router.last_provider, "backup")


if __name__ == "__main__":
    unittest.main()
