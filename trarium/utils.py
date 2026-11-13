"""ابزارهای مشترک تراریوم."""
from __future__ import annotations

import re


def clean_translation(text: str) -> str:
    """Conservative cleanup that removes obvious meta chatter without deleting story text."""
    if not text:
        return ""
    lines = []
    meta = re.compile(
        r"^\s*(?:here(?:'s| is)\s+the\s+translation|translation|output(?:\s+only)?|response|answer|note|warning)\s*:\s*$",
        re.I,
    )
    for line in str(text).splitlines():
        stripped = line.strip()
        if meta.match(stripped):
            continue
        if re.fullmatch(r"```(?:text|plaintext|translation)?", stripped, re.I):
            continue
        if re.fullmatch(r"```", stripped):
            continue
        lines.append(line.rstrip())
    result = "\n".join(lines).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = "\n".join(re.sub(r"[ \t]{2,}", " ", line) for line in result.split("\n"))
    return result.strip() or str(text).strip()


def probably_already_in_persian(text: str) -> bool:
    """Conservative detection used only to avoid translating already-Persian input."""
    if not text or len(text) < 40:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    arabic_script = sum("\u0600" <= c <= "\u06ff" for c in letters)
    persian_specific = sum(c in "پچژگک" for c in text)
    return arabic_script / len(letters) >= 0.88 and persian_specific >= 1


def is_nontranslatable_token(text: str) -> bool:
    """Detect tiny tokens that do not deserve an API request."""
    value = (text or "").strip()
    if not value:
        return True
    if len(value) <= 2:
        return True
    if re.fullmatch(r"(?:https?://|www\.)\S+", value, re.I):
        return True
    if re.fullmatch(r"[\d\s.,:/#()\-–—]+", value):
        return True
    if re.fullmatch(r"(?:page|pp?\.?)[\s\d\-–—]+", value, re.I):
        return True
    return False


def estimate_tokens(text: str) -> int:
    """Best-effort tokenizer; uses tiktoken when installed and a conservative heuristic otherwise."""
    value = str(text or "")
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return max(1, len(enc.encode(value)))
    except Exception:
        return max(1, (len(value) + 2) // 3)
