"""Translate one PDF with the real OpenRouter API (used by GitHub Actions).

Usage: python qa/real_translation.py <source.pdf> <output.pdf> [target_lang]

The API key is read from ``OPENROUTER_API_KEY`` and is never printed. The model
defaults to ``google/gemini-2.5-flash-lite`` and can be overridden with
``TRARIUM_MODEL``. The script keeps the original layout of every page, writes a
JSON summary to stdout and exits non-zero when nothing was translated.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: real_translation.py <source.pdf> <output.pdf> [target_lang]")
        return 2
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    lang = sys.argv[3] if len(sys.argv) > 3 else "fa"
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        print("OPENROUTER_API_KEY is not set; skipping the real translation.")
        return 2
    if not source.exists():
        print(f"source file not found: {source}")
        return 2

    model = os.environ.get("TRARIUM_MODEL") or "google/gemini-2.5-flash-lite"
    output.parent.mkdir(parents=True, exist_ok=True)

    from trarium.pdf_optimized import translate_pdf_optimized
    from trarium.translators.openrouter import OpenRouterTranslator

    translator = OpenRouterTranslator(api_key=key, model_name=model)
    stats = translate_pdf_optimized(
        str(source), translator, lang, str(output),
        translation_workers=2, translation_batch_chars=8000, return_stats=True,
    )
    st = stats[1] if isinstance(stats, tuple) else stats

    summary = {
        "source": str(source),
        "output": str(output),
        "target_lang": lang,
        "model": model,
        "translated_blocks": getattr(st, "translated_blocks", 0),
        "failed_blocks": getattr(st, "failed_blocks", 0),
        "pages_written": getattr(st, "pages_written", 0),
        "write_failures": getattr(st, "write_failures", 0),
        "api_requests": getattr(st, "api_requests", 0),
        "bytes": output.stat().st_size if output.exists() else 0,
        "first_write_error": getattr(st, "first_write_error", "") or "",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if summary["translated_blocks"] and summary["bytes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
