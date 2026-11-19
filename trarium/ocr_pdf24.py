"""PDF24 Creator OCR provider.

PDF24 ships a documented command-line OCR tool (``pdf24-Ocr.exe``) with the
PDF24 Creator for Windows — see the official manual,
https://creator.pdf24.org/manual/11/#101-command-line:

    pdf24-Ocr.exe -outputFile OUT.pdf [-language eng] [-dpi 300]
                  [-skipFilesWithText] [-skipPagesWithText] ... "IN.pdf"

With exactly one input file and ``-outputFile`` it runs without a window and
returns exit code 0 when the output was written (1 on failure, since 11.31.0).
That is the only supported automated entry point: PDF24 publishes no HTTP API,
and their web tool (tools.pdf24.org/en/ocr-pdf) is a browser app with no public
upload endpoint, so this module never touches it.

Design notes
------------
* The user's file is never modified: it is copied to a temporary directory and
  only the copy is handed to PDF24.
* The produced OCR PDF keeps the original page images and adds a text layer, so
  the rest of Trarium can read it exactly like any other text PDF.
* Nothing here is Windows-only at import time; on other systems the provider is
  simply reported as unavailable (``is_available() is False``) instead of
  silently falling back to another engine.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Environment override first, so a portable/secondary installation or a test
# double can be pointed at explicitly.
ENV_BIN = "TRARIUM_PDF24_BIN"

WINDOWS_CANDIDATES = (
    r"C:\Program Files\PDF24\pdf24-Ocr.exe",
    r"C:\Program Files (x86)\PDF24\pdf24-Ocr.exe",
    r"C:\Programme\PDF24\pdf24-Ocr.exe",
)

# Tesseract language codes used by PDF24 (it downloads a missing file on demand).
LANGUAGE_MAP = {
    "eng": "eng",
    "en": "eng",
    "fas": "fas",
    "fa": "fas",
    "per": "fas",
    "chi_sim": "chi_sim",
    "zh": "chi_sim",
    "zh-cn": "chi_sim",
}


class Pdf24Error(RuntimeError):
    """Raised when the PDF24 OCR tool is missing or reports a failure."""


def find_binary() -> str | None:
    """Return the pdf24-Ocr executable path, or None when it is not installed."""
    override = os.environ.get(ENV_BIN, "").strip()
    if override:
        return override if Path(override).exists() else None
    for candidate in WINDOWS_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in ("pdf24-Ocr.exe", "pdf24-Ocr", "pdf24-ocr"):
        found = shutil.which(name)
        if found:
            return found
    return None


def is_available() -> bool:
    return find_binary() is not None


def availability_hint() -> str:
    """Human-readable explanation used by the UI when the provider is missing."""
    return (
        "PDF24 OCR فقط با نسخهٔ ویندوزی PDF24 Creator در دسترس است. "
        "PDF24 Creator را نصب کنید (pdf24-Ocr.exe باید موجود باشد) یا مسیر آن را در "
        f"متغیر محیطی {ENV_BIN} قرار دهید. PDF24 هیچ API اینترنتی عمومی ندارد؛ ابزار وب آن "
        "قابل فراخوانی از برنامه نیست و در این پروژه از آن استفاده نمی‌شود."
    )


def language_codes(languages: str | list[str] | None) -> str:
    """Map Trarium/ISO codes to the Tesseract codes PDF24 expects."""
    if not languages:
        return "eng"
    items = [languages] if isinstance(languages, str) else list(languages)
    mapped: list[str] = []
    for item in items:
        for part in str(item).replace(",", "+").split("+"):
            part = part.strip().lower()
            if not part:
                continue
            code = LANGUAGE_MAP.get(part, part)
            if code not in mapped:
                mapped.append(code)
    return "+".join(mapped) or "eng"


def build_command(binary: str, source: Path, output: Path, language: str, dpi: int) -> list[str]:
    return [
        binary,
        "-outputFile", str(output),
        "-language", language,
        "-dpi", str(int(dpi)),
        str(source),
    ]


def ocr_pdf(
    input_pdf: str | os.PathLike,
    output_pdf: str | os.PathLike | None = None,
    language: str | list[str] | None = None,
    dpi: int = 300,
    timeout: int = 1800,
    binary: str | None = None,
) -> Path:
    """OCR a whole PDF with PDF24 and return the new file's path.

    The input file is copied first, so the caller's document is never touched.
    """
    binary = binary or find_binary()
    if not binary:
        raise Pdf24Error(availability_hint())

    source = Path(input_pdf).expanduser().resolve()
    if not source.exists():
        raise Pdf24Error(f"فایل ورودی پیدا نشد: {source}")

    workdir = Path(tempfile.mkdtemp(prefix="trarium-pdf24-"))
    copy = workdir / f"input{source.suffix or '.pdf'}"
    shutil.copy2(source, copy)

    target = Path(output_pdf).expanduser().resolve() if output_pdf else workdir / "ocr.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)

    command = build_command(binary, copy, target, language_codes(language), dpi)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise Pdf24Error(f"pdf24-Ocr اجرا نشد: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise Pdf24Error(f"PDF24 OCR بیش از {timeout} ثانیه طول کشید و متوقف شد.") from exc

    if completed.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:800]
        raise Pdf24Error(
            f"PDF24 OCR ناموفق بود (کد خروج {completed.returncode}). {detail}"
        )
    if not has_text_layer(target):
        raise Pdf24Error(
            "PDF24 فایل را ساخت ولی لایهٔ متنی خالی است؛ احتمالاً زبان انتخاب‌شده نصب نیست."
        )
    return target


def has_text_layer(pdf_path: str | os.PathLike, min_chars: int = 20) -> bool:
    """True when the PDF carries selectable text (i.e. OCR actually produced a layer)."""
    import pymupdf

    try:
        with pymupdf.open(pdf_path) as doc:
            chars = 0
            for page in doc:
                chars += len(page.get_text().strip())
                if chars >= min_chars:
                    return True
    except Exception:
        return False
    return False


def extract_blocks(pdf_path: str | os.PathLike) -> dict[int, list[dict]]:
    """Read the OCR text layer as Trarium layout blocks (0-based page index)."""
    import pymupdf

    from trarium.pdf_optimized import extract_text_blocks

    blocks: dict[int, list[dict]] = {}
    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            page = doc[index]
            page_blocks = extract_text_blocks(page)
            if not page_blocks:
                # Fall back to raw words grouped by line for unusual layouts.
                page_blocks = [
                    {"bbox": tuple(word[:4]), "text": word[4], "font_size": 10.0}
                    for word in page.get_text("words")
                    if str(word[4]).strip()
                ]
            blocks[index] = page_blocks
    return blocks


def ocr_document_blocks(
    input_pdf: str | os.PathLike,
    language: str | list[str] | None = None,
    dpi: int = 300,
    timeout: int = 1800,
    binary: str | None = None,
    keep_dir: str | os.PathLike | None = None,
) -> tuple[dict[int, list[dict]], Path]:
    """Run PDF24 once for the document and return (blocks per page, OCR pdf path)."""
    target = None
    if keep_dir:
        target = Path(keep_dir) / "pdf24_ocr.pdf"
    ocr_path = ocr_pdf(input_pdf, target, language=language, dpi=dpi, timeout=timeout, binary=binary)
    return extract_blocks(ocr_path), ocr_path
