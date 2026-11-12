"""Small local OCR benchmark helper (designed for ~10 book pages)."""
from __future__ import annotations

import time
from pathlib import Path


def benchmark_ocr(pdf_path: str, methods: list[str], page_numbers: list[int] | None = None, lang: str = "eng") -> list[dict]:
    import pymupdf
    from trarium.pdf_optimized import extract_ocr_blocks

    doc = pymupdf.open(pdf_path)
    try:
        pages = page_numbers if page_numbers is not None else list(range(min(10, len(doc))))
        rows = []
        for method in methods:
            started = time.perf_counter()
            total_blocks = 0
            ok = 0
            error = ""
            for page_num in pages:
                try:
                    blocks = extract_ocr_blocks(doc[page_num], lang=lang, ocr_method=method)
                    total_blocks += len(blocks)
                    ok += bool(blocks)
                except Exception as exc:
                    error = str(exc)
                    break
            elapsed = time.perf_counter() - started
            rows.append({
                "method": method,
                "pages": len(pages),
                "successful_pages": ok,
                "seconds": round(elapsed, 2),
                "sec_per_page": round(elapsed / max(1, len(pages)), 2),
                "blocks": total_blocks,
                "error": error,
            })
        return rows
    finally:
        doc.close()
