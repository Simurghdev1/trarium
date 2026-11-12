"""Conservative text optimization. It must not rewrite literary content."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class OptimizationStats:
    source_chars: int = 0
    output_chars: int = 0
    whitespace_changes: int = 0
    skipped_edge_lines: int = 0


def normalize_for_fingerprint(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def safe_normalize_text(text: str) -> str:
    # Deliberately conservative: only line endings / excess whitespace are normalized.
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(re.sub(r"[ \t]+", " ", line).rstrip() for line in value.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def looks_like_page_noise(text: str) -> bool:
    value = str(text or "").strip()
    if not value or len(value) > 80:
        return False
    return bool(
        re.fullmatch(r"(?:page|p\.?|pp\.?)\s*\d+(?:\s*[-–]\s*\d+)?", value, re.I)
        or re.fullmatch(r"\d{1,5}", value)
        or re.fullmatch(r"(?:chapter|فصل)\s*\d+", value, re.I)
    )


def detect_repeated_edge_lines(pages: list[list[dict]], min_repeats: int = 3) -> set[tuple[int, int]]:
    """Mark page-edge boilerplate without deleting it from output."""
    signatures: dict[str, list[tuple[int, int]]] = {}
    for pno, blocks in enumerate(pages):
        ordered = sorted(enumerate(blocks), key=lambda x: (x[1].get("bbox", (0, 0))[1], x[1].get("bbox", (0, 0))[0]))
        for idx, block in ordered[:2] + ordered[-2:]:
            text = str(block.get("text", "") or "").strip()
            if len(text) < 3:
                continue
            sig = normalize_for_fingerprint(text).casefold()
            signatures.setdefault(sig, []).append((pno, idx))
    marked: set[tuple[int, int]] = set()
    for locations in signatures.values():
        pages_seen = {p for p, _ in locations}
        if len(pages_seen) >= min_repeats:
            marked.update(locations)
    return marked
