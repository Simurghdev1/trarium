"""Tests for the PDF24 OCR provider.

The Windows executable ``pdf24-Ocr.exe`` cannot run on Linux CI, so these tests
drive the *documented command-line contract*
(https://creator.pdf24.org/manual/11/#101-command-line) through a stand-in
executable: it parses the same switches, writes a PDF with a real text layer and
returns the documented exit codes. That verifies our side — command building,
copying the input instead of touching it, exit-code handling and block
extraction — without pretending the real binary ran.
"""
from __future__ import annotations

import hashlib
import sys
import textwrap
from pathlib import Path

import pymupdf
import pytest

from trarium import ocr_pdf24
from trarium.pdf_optimized import translate_pdf_optimized

FAKE_OCR = textwrap.dedent(
    '''
    import sys, pathlib
    args = sys.argv[1:]
    if not args:
        sys.exit(2)
    out = None; lang = "eng"; dpi = "300"; files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-outputFile": out = args[i + 1]; i += 2
        elif a == "-language": lang = args[i + 1]; i += 2
        elif a == "-dpi": dpi = args[i + 1]; i += 2
        elif a.startswith("-"): i += 1
        else: files.append(a); i += 1
    if not out or len(files) != 1:
        sys.exit(1)
    import pymupdf
    src = pymupdf.open(files[0])
    doc = pymupdf.open()
    for page in src:
        new = doc.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_text((72, 100), f"OCR text layer ({lang}, {dpi} dpi)", fontsize=12)
        new.insert_text((72, 130), "Chinese source line translated here.", fontsize=12)
    doc.save(out)
    sys.exit(0)
    '''
)

FAILING_OCR = "import sys\nsys.stderr.write('ocr failed: no language data\\n')\nsys.exit(1)\n"


@pytest.fixture()
def fake_pdf24(tmp_path: Path, monkeypatch):
    binary = tmp_path / "pdf24-Ocr.exe"
    binary.write_text(f"#!/usr/bin/env {sys.executable}\n{FAKE_OCR}", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv(ocr_pdf24.ENV_BIN, str(binary))
    return binary


@pytest.fixture()
def source_pdf(tmp_path: Path) -> Path:
    # Image-only pages, like a real scan: no text layer at all.
    path = tmp_path / "scan.pdf"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 560), 0)
    pix.set_rect(pix.irect, (235, 238, 242))
    doc = pymupdf.open()
    for index in range(2):
        page = doc.new_page()
        page.insert_image(page.rect, pixmap=pix)
    doc.save(path)
    doc.close()
    return path


def test_binary_is_found_from_env(fake_pdf24):
    assert ocr_pdf24.is_available() is True
    assert ocr_pdf24.find_binary() == str(fake_pdf24)


def test_missing_binary_reports_why(monkeypatch):
    monkeypatch.setenv(ocr_pdf24.ENV_BIN, "/nonexistent/pdf24-Ocr.exe")
    assert ocr_pdf24.is_available() is False
    with pytest.raises(ocr_pdf24.Pdf24Error) as exc:
        ocr_pdf24.ocr_pdf("whatever.pdf")
    assert "PDF24" in str(exc.value)


def test_language_codes_map_to_tesseract_names():
    assert ocr_pdf24.language_codes("zh") == "chi_sim"
    assert ocr_pdf24.language_codes(["fa", "en"]) == "fas+eng"
    assert ocr_pdf24.language_codes("chi_sim+eng") == "chi_sim+eng"
    assert ocr_pdf24.language_codes(None) == "eng"


def test_command_uses_documented_switches():
    command = ocr_pdf24.build_command("pdf24-Ocr.exe", Path("in.pdf"), Path("out.pdf"), "chi_sim", 300)
    assert command[0] == "pdf24-Ocr.exe"
    assert "-outputFile" in command and "-language" in command and "-dpi" in command
    assert command[-1] == "in.pdf"


def test_ocr_runs_and_keeps_original_untouched(fake_pdf24, source_pdf, tmp_path):
    before = hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    out = ocr_pdf24.ocr_pdf(source_pdf, tmp_path / "ocr.pdf", language="chi_sim+eng")
    after = hashlib.sha256(source_pdf.read_bytes()).hexdigest()
    assert before == after, "the user's PDF was modified"
    assert out.exists() and out.stat().st_size > 0
    assert ocr_pdf24.has_text_layer(out) is True


def test_extract_blocks_reads_the_text_layer(fake_pdf24, source_pdf, tmp_path):
    blocks, ocr_path = ocr_pdf24.ocr_document_blocks(source_pdf, language="eng", keep_dir=tmp_path)
    assert ocr_path.exists()
    assert set(blocks) == {0, 1}
    text = " ".join(b["text"] for b in blocks[0])
    assert "OCR text layer" in text


def test_failure_exit_code_is_reported(tmp_path, monkeypatch):
    binary = tmp_path / "pdf24-Ocr.exe"
    binary.write_text(f"#!/usr/bin/env {sys.executable}\n{FAILING_OCR}", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv(ocr_pdf24.ENV_BIN, str(binary))
    source = tmp_path / "scan.pdf"
    doc = pymupdf.open(); doc.new_page(); doc.save(source); doc.close()
    with pytest.raises(ocr_pdf24.Pdf24Error) as exc:
        ocr_pdf24.ocr_pdf(source, tmp_path / "out.pdf")
    assert "ناموفق" in str(exc.value)


class _OfflineTranslator:
    """Deterministic stand-in so the pipeline can run without any network call."""

    provider = "offline"
    model = "offline-1"

    def __init__(self):
        self.last_usage = {}

    def translate(self, text: str, target_lang: str = "fa") -> str:
        # No source text in the answer, otherwise the pipeline correctly flags
        # it as an echo of the prompt.
        return "این متن ترجمهٔ آزمایشی است."


def test_pipeline_accepts_pdf24_blocks(fake_pdf24, source_pdf, tmp_path):
    output = tmp_path / "translated.pdf"
    result, stats = translate_pdf_optimized(
        str(source_pdf), _OfflineTranslator(), "fa", str(output), 1,
        enable_ocr=True, ocr_method="pdf24", ocr_lang="eng",
        skip_pages=set(), translation_workers=1, return_stats=True,
        use_cache=False, use_checkpoint=False,
    )
    assert Path(result).exists()
    assert stats.ocr_pdf_path.endswith(".pdf")
    import unicodedata

    text = unicodedata.normalize("NFKC", "".join(page.get_text() for page in pymupdf.open(result)))
    assert "متن" in text, "OCR text never reached the translation step"
    assert stats.translated_blocks >= 1
