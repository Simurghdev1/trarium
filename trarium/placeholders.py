"""Protect non-translatable spans while leaving literary text untouched."""
from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS = [
    re.compile(r"https?://[^\s<>]+", re.I),
    re.compile(r"(?:mailto:)?[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I),
    re.compile(r"</?[A-Za-z][^>]{0,200}?>"),
    re.compile(r"`[^`\n]+`"),
]

@dataclass
class PlaceholderResult:
    text: str
    mapping: dict[str, str]


def protect(text: str) -> PlaceholderResult:
    source = str(text or "")
    mapping: dict[str, str] = {}
    counter = 0
    def repl(match: re.Match) -> str:
        nonlocal counter
        token = f"__TRARIUM_PH_{counter:04d}__"
        mapping[token] = match.group(0)
        counter += 1
        return token
    current = source
    for pattern in PATTERNS:
        current = pattern.sub(repl, current)
    return PlaceholderResult(current, mapping)


def restore(text: str, mapping: dict[str, str]) -> str:
    value = str(text or "")
    for token, original in mapping.items():
        value = value.replace(token, original)
    return value
