"""Tests for the GitHub Actions translation driver (qa/translate_files.py).

The driver is what actually spends the API credits on the CI runners, so it is
covered here end to end against the local mock provider: the real transport,
the real PDF pipeline and the real book pipeline all run, only the model is a
mock. No API key and no network access are needed.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qa.mock_provider import MockServer  # noqa: E402
from qa.translate_files import build_translator, parse_pages, safe_name  # noqa: E402


class DriverHelperTests(unittest.TestCase):
    def test_safe_name_keeps_the_file_readable(self):
        self.assertEqual(safe_name("/books/Omniscient Viewpoint.epub"), "Omniscient_Viewpoint")
        self.assertEqual(safe_name("Josep Moore - Mangas ENG.pdf"), "Josep_Moore_-_Mangas_ENG")
        self.assertEqual(safe_name("///"), "input")

    def test_parse_pages_reads_ranges(self):
        self.assertEqual(parse_pages("3,5,10-12"), {2, 4, 9, 10, 11})
        self.assertEqual(parse_pages(None), set())
        self.assertEqual(parse_pages(""), set())

    def test_no_key_is_a_clear_error(self):
        old = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "TRARIUM_BASE_URL")}
        try:
            with self.assertRaises(SystemExit):
                build_translator(["gemini", "openrouter", "openai_compatible"])
        finally:
            for key, value in old.items():
                if value is not None:
                    os.environ[key] = value


class DriverEndToEndTests(unittest.TestCase):
    """The driver really runs: a PDF and a text file go through the mock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self.tmp.name)
        self.home = self.out / "home"
        self.env = dict(os.environ)
        self.env.update({
            "TRARIUM_BASE_URL": "",
            "TRARIUM_API_KEY": "mock-key",
            "TRARIUM_MODEL": "mock/model",
            "TRARIUM_HOME": str(self.home),
            "PYTHONUNBUFFERED": "1",
        })
        for key in ("GEMINI_API_KEY", "OPENROUTER_API_KEY"):
            self.env.pop(key, None)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, args: list[str]) -> tuple[int, str]:
        import subprocess

        proc = subprocess.run(
            [sys.executable, "qa/translate_files.py", *args],
            cwd=str(ROOT), env=self.env, capture_output=True, text=True, timeout=900,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def test_txt_file_is_translated(self):
        with MockServer() as server:
            self.env["TRARIUM_BASE_URL"] = server.base_url
            code, out = self._run([
                "--input", "qa/fixtures/fa_source.txt",
                "--outdir", str(self.out / "txt"),
                "--providers", "openai_compatible",
                "--out-format", "txt",
            ])
        self.assertEqual(code, 0, out[-3000:])
        metrics = json.loads((self.out / "txt" / "fa_source" / "metrics.json").read_text(encoding="utf-8"))
        self.assertTrue(metrics["ok"])
        self.assertEqual(metrics["failed_chunks"], 0)
        self.assertGreater(metrics["persian_chars"], 0)
        self.assertTrue((self.out / "txt" / "fa_source" / "02_translated_fa.txt").exists())

    def test_pdf_keeps_its_layout_and_writes_screenshots(self):
        with MockServer() as server:
            self.env["TRARIUM_BASE_URL"] = server.base_url
            code, out = self._run([
                "--input", "qa/fixtures/en_source.pdf",
                "--outdir", str(self.out / "pdf"),
                "--providers", "openai_compatible",
                "--workers", "2",
                "--batch-chars", "4000",
            ])
        self.assertEqual(code, 0, out[-3000:])
        folder = self.out / "pdf" / "en_source"
        metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
        self.assertTrue(metrics["ok"])
        self.assertGreater(metrics["translated_blocks"], 0)
        self.assertEqual(metrics["write_failures"], 0)
        self.assertGreater(metrics["bytes"], 0)
        # The before/after screenshots are how a human judges the result.
        shots = sorted(p.name for p in (folder / "screenshots").glob("*.png"))
        self.assertTrue(shots, "no screenshots were written")
        self.assertTrue(any(n.startswith("original-") for n in shots))
        self.assertTrue(any(n.startswith("translated-") for n in shots))

    def test_a_missing_input_fails_without_a_traceback(self):
        code, out = self._run([
            "--input", "qa/fixtures/does-not-exist.pdf",
            "--outdir", str(self.out / "missing"),
            "--providers", "openai_compatible",
        ])
        self.assertEqual(code, 2)
        self.assertIn("no input file was found", out)


if __name__ == "__main__":
    unittest.main()
