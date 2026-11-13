"""Pre-flight estimation and connectivity probe helpers."""
from __future__ import annotations

from dataclasses import dataclass
from trarium.utils import estimate_tokens

@dataclass
class PreflightReport:
    ok: bool
    token_limit: int
    sample_input_tokens: int
    sample_output_tokens: int
    estimated_book_tokens: int = 0
    estimated_batches: int = 0
    error: str = ""

def estimate_book(texts: list[str], batch_chars: int = 14000) -> tuple[int,int]:
    total = sum(estimate_tokens(t) for t in texts)
    # conservative 3x char heuristic is used only to estimate batch count
    batches = max(1, (sum(len(t) for t in texts) + batch_chars - 1) // batch_chars) if texts else 0
    return total, batches

def probe(translator, target_lang: str = "fa", sample: str | None = None) -> PreflightReport:
    sample = sample or ("این یک تست کوتاه برای بررسی اتصال مترجم است." if target_lang == "fa" else "This is a short translation connectivity test.")
    try:
        limit = int(getattr(translator, "get_token_limit", lambda: 8000)() or 8000)
        inp = estimate_tokens(sample)
        out = str(translator.translate(sample, target_lang) or "")
        if not out.strip():
            raise RuntimeError("پاسخ Probe خالی بود")
        return PreflightReport(True, limit, inp, estimate_tokens(out))
    except Exception as exc:
        return PreflightReport(False, int(getattr(translator, "get_token_limit", lambda: 8000)() or 8000), estimate_tokens(sample), 0, error=str(exc))
