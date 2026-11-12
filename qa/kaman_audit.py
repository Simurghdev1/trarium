"""Independent checks of kaman-ai-translator's Qt-free core.

Runs against the cloned upstream repository (never modified). Measures chunk
integrity, prompt overhead and JSON parsing so the token comparison with
Trarium is based on measurements rather than guesses.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

KAMAN = Path(os.environ.get("KAMAN_PATH", "/workspace/chats/b1c9b096014dd3cf9eeb2830/audit/kaman")).resolve()
sys.path.insert(0, str(KAMAN))

import tiktoken  # noqa: E402

import kaman_core as k  # noqa: E402

ENC = tiktoken.get_encoding("cl100k_base")
RESULTS: dict[str, dict] = {}


def tokens(text: str) -> int:
    return len(ENC.encode(text or ""))


def check(name: str, fn):
    try:
        value = fn()
        RESULTS[name] = {"ok": True, **(value or {})}
    except Exception as exc:  # record, never pretend success
        RESULTS[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


CHAPTER = (
    "The old lighthouse stood at the edge of the cliff, its light sweeping the water every "
    "twelve seconds. Mara climbed the stairs each evening, counting them out loud, because "
    "the rhythm kept her thoughts from drifting back to the harbour. "
) * 40


def chunk_integrity():
    chunks = k.split_into_chunks(CHAPTER, 6000)
    joined = " ".join(chunks).split()
    assert joined == CHAPTER.split(), "kaman splits words across chunks"
    assert all(len(c) <= 6000 for c in chunks)
    return {"chars": len(CHAPTER), "chunks": len(chunks), "max_chunk": max(len(c) for c in chunks)}


def empty_and_long_units():
    assert k.split_into_chunks("", 6000) == []
    long_unit = "word " * 4000
    chunks = k.split_into_chunks(long_unit, 6000)
    assert all(len(c) <= 6000 for c in chunks), "long paragraph exceeds the configured chunk size"
    return {"chunks_for_20k": len(chunks)}


def prompt_overhead():
    prompt = k.build_prompt("Hello world.", "en_fa") if "en_fa" in k.DIRECTIONS else k.build_prompt("Hello world.", next(iter(k.DIRECTIONS)))
    return {"prompt_tokens": tokens(prompt), "content_tokens": tokens("Hello world."),
            "overhead_tokens": tokens(prompt) - tokens("Hello world.")}


def prompt_overhead_per_chunk():
    chunks = k.split_into_chunks(CHAPTER, 6000)
    total = 0
    overhead = 0
    for chunk in chunks:
        prompt = k.build_prompt(chunk, next(iter(k.DIRECTIONS)))
        total += tokens(prompt)
        overhead += tokens(prompt) - tokens(chunk)
    return {"chunks": len(chunks), "total_prompt_tokens": total, "overhead_tokens": overhead,
            "overhead_per_chunk": overhead // max(1, len(chunks))}


def json_parsing():
    assert k.parse_json_response('{"translation": "سلام"}')["translation"] == "سلام"
    assert k.parse_json_response('```json\n{"translation": "سلام"}\n```')["translation"] == "سلام"
    assert k.parse_json_response("not json at all") is None
    assert k.parse_json_response("") is None
    return {"cases": 4}


def rolling_context_bounds():
    cfg = {"context_tail_chars": 500}
    text = "word " * 1000
    tail = k.rolling_context(text, cfg)
    assert len(tail) <= 500, "context tail exceeds the configured budget"
    return {"tail_chars": len(tail)}


def qc_guard():
    class Engine:
        def generate(self, prompt, system=None, image_png=None, json_mode=True):
            # A reviewer that "fixes" by deleting 90% of the text must be rejected.
            return json.dumps({"ok": False, "issues": ["x"], "corrected_translation": "کوتاه"})

    class Bad:
        def generate(self, prompt, system=None, image_png=None, json_mode=True):
            return "not json"

    source = "A long English paragraph. " * 30
    translation = "یک پاراگراف بلند فارسی. " * 30
    kept = k.review_translation(Engine(), source, translation, next(iter(k.DIRECTIONS)),
                                {"context_tail_chars": 300})
    kept2 = k.review_translation(Bad(), source, translation, next(iter(k.DIRECTIONS)),
                                 {"context_tail_chars": 300})
    assert kept == translation, "QC accepted a shortened translation"
    assert kept2 == translation, "a broken QC response destroyed the translation"
    return {"qc_shortening_rejected": True, "qc_bad_json_safe": True}


for name, fn in [
    ("chunk_integrity", chunk_integrity),
    ("empty_and_long_units", empty_and_long_units),
    ("prompt_overhead", prompt_overhead),
    ("prompt_overhead_per_chunk", prompt_overhead_per_chunk),
    ("json_parsing", json_parsing),
    ("rolling_context_bounds", rolling_context_bounds),
    ("qc_guard", qc_guard),
]:
    check(name, fn)

out = Path(__file__).parent / "kaman_results.json"
out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(RESULTS, ensure_ascii=False, indent=2))
print("wrote", out)
