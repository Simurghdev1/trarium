"""Persian typography cleanup for translated text.

The translation models are good at meaning and careless at typography: they write
«می توان» instead of «می‌توان», put a space before «،», mix Arabic ي/ك with
Persian ی/ک, and quote with straight quotes. This module fixes only those
mechanical things — it never touches word order or meaning — and it is
idempotent, so running it twice changes nothing.

Everything here is a pure string function: no I/O, no network, safe to test.
"""
from __future__ import annotations

import re

ZWNJ = "\u200c"

# Letters that look the same but are the wrong code point in Persian text.
_ARABIC_TO_PERSIAN = {
    "\u064a": "\u06cc",  # ي -> ی
    "\u0643": "\u06a9",  # ك -> ک
    "\u0649": "\u06cc",  # ى -> ی
    "\u06c0": "\u0647\u0654",  # ۀ
}

# Words that keep their space even though they start with a «می»-like prefix.
_MI_KEEP = {
    "می", "میز", "میکا", "میل", "میم", "میهن", "میدان", "میزان", "میوه",
    "میان", "میزبان", "میدانی", "میرا", "میکی", "میلاد", "مینا", "میرزا",
}

_PUNCT_BEFORE = "،؛:؟!٪"
_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _join_prefixes(text: str) -> str:
    """«می توان» -> «می‌توان», «کتاب ها» -> «کتاب‌ها»."""
    # می / نمی + verb
    def mi(match: re.Match) -> str:
        word = match.group(1)
        if word in _MI_KEEP or len(word) < 2:
            return match.group(0)
        return f"{match.group(2)}{word}" if match.group(2) else word

    text = re.sub(r"(?<![\w" + ZWNJ + r"])(ن?می)\s+([آ-ی][\w" + ZWNJ + r"]*)",
                  lambda m: f"{m.group(1)}{ZWNJ}{m.group(2)}", text)

    # Plural / comparative suffixes that are separate words after a translation.
    text = re.sub(r"([آ-ی" + ZWNJ + r"])\s+(هایی|های|ها)\b", rf"\1{ZWNJ}\2", text)
    text = re.sub(r"([آ-ی" + ZWNJ + r"])\s+(ترین)\b", rf"\1{ZWNJ}\2", text)
    text = re.sub(r"([آ-ی" + ZWNJ + r"])\s+(تر)\b(?!\s*[آ-ی])", rf"\1{ZWNJ}\2", text)
    return text


def _spacing(text: str) -> str:
    # No space before punctuation, exactly one space after it.
    text = re.sub(r"\s+([" + _PUNCT_BEFORE + r"])", r"\1", text)
    text = re.sub(r"([" + _PUNCT_BEFORE + r"])(?=[^\s" + ZWNJ + r"])", r"\1 ", text)
    # A dot that ends a Persian sentence.
    text = re.sub(r"([آ-ی" + ZWNJ + r"])\s*\.\s*", r"\1. ", text)
    # No space just inside guillemets or parentheses.
    text = re.sub(r"«\s+", "«", text)
    text = re.sub(r"\s+»", "»", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    # Never a space on either side of a ZWNJ.
    text = re.sub(r"\s*" + ZWNJ + r"\s*", ZWNJ, text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def _quotes(text: str) -> str:
    """Straight quotes around Persian text become «…»."""
    def pair(match: re.Match) -> str:
        inner = match.group(1).strip()
        return f"«{inner}»" if inner else match.group(0)

    text = re.sub(r'"([^"\n]{1,400})"', pair, text)
    text = re.sub(r"“([^”\n]{1,400})”", pair, text)
    text = re.sub(r"''([^'\n]{1,400})''", pair, text)
    return text


def normalize(text: str, *, persian_digits: bool = False, quotes: bool = True) -> str:
    """Clean the mechanical side of Persian typography. Idempotent."""
    if not text:
        return text or ""
    out = str(text)
    for wrong, right in _ARABIC_TO_PERSIAN.items():
        out = out.replace(wrong, right)
    if quotes:
        out = _quotes(out)
    out = _join_prefixes(out)
    out = _spacing(out)
    if persian_digits:
        out = re.sub(
            r"(?<=[\u0600-\u06ff" + ZWNJ + r"\s])(\d+)(?=[\u0600-\u06ff" + ZWNJ + r"\s\.،؛!؟]|$)",
            lambda m: m.group(1).translate(_DIGITS),
            out,
        )
    return "\n".join(line.strip() for line in out.splitlines()).strip()


def styling_issues(text: str) -> list[str]:
    """Mechanical problems a reviewer can see without reading the meaning."""
    issues: list[str] = []
    if re.search(r"[يك]", text or ""):
        issues.append("حروف عربی (ي/ك) در متن")
    if re.search(r"(?<![\w" + ZWNJ + r"])ن?می\s+[آ-ی]", text or ""):
        issues.append("«می» بدون نیم‌فاصله")
    if re.search(r"[آ-ی]\s+ها(?:ی)?\b", text or ""):
        issues.append("«ها» جدا از کلمه")
    if re.search(r"\s+[،؛:؟!]", text or ""):
        issues.append("فاصله قبل از علامت")
    if re.search(r"[،؛:؟!][^\s" + ZWNJ + r"\n]", text or ""):
        issues.append("نبود فاصله بعد از علامت")
    if re.search(r'["“]', text or ""):
        issues.append("گیومهٔ انگلیسی")
    return issues
