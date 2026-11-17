"""Short Persian rules that ride along with every prompt.

A translation can be word-perfect and still read like English: Persian builds
phrases the other way round ("blue car" → «ماشین آبی»), so copying the English
word order sentence by sentence gives a text no Persian speaker would say. The
rule below is added to every short prompt — page blocks, retries, subtitles and
the provider system prompts — and is kept to two sentences because it is paid
for on every single request.
"""
from __future__ import annotations

PERSIAN_NATURAL_ORDER = (
    "Write Persian the way Persian is spoken, never the English word order with Persian "
    "words: the noun comes before its adjective and description (\"blue car\" → «ماشین آبی»), "
    "and each phrase is built in its own Persian order instead of the English sequence. "
    "If no Persian speaker would say it that way, rewrite the sentence."
)


def persian_rules(base: str = "") -> str:
    """``base`` plus the word-order rule, for Persian output."""
    base = (base or "").strip()
    return f"{base} {PERSIAN_NATURAL_ORDER}".strip() if base else PERSIAN_NATURAL_ORDER
