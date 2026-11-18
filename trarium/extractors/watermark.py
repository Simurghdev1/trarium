"""حذف خطوط واترمارک/تبلیغاتی قبل از ترجمه.

کتاب‌های دانلودی معمولاً چند خط «OceanofPDF.com» یا «Downloaded from ...» دارند.
این خطوط نه متن کتاب‌اند نه باید هزینهٔ ترجمه بدهند؛ اگر ترجمه شوند، در نسخهٔ
فارسی هم می‌مانند.
"""
from __future__ import annotations

import re

# نام سایت‌هایی که به‌عنوان واترمارک داخل فایل‌های EPUB/PDF می‌آیند.
WATERMARK_TOKENS = (
    "oceanofpdf",
    "ocean of pdf",
    "oceanofpdf.com",
    "epub.pub",
    "z-lib",
    "zlib.pub",
    "zlibrary",
    "libgen",
    "b-ok",
    "annas-archive",
    "bookfrom.net",
    "smashwords.com",
    "ebookelo.com",
    "manybooks.net",
    "standardebooks.org",
    "gutenberg.org",
    "novelupdates.com",
    "wuxiaworld.com",
)

_TOKEN_RE = re.compile("|".join(re.escape(token) for token in WATERMARK_TOKENS), re.IGNORECASE)
_AD_RE = re.compile(
    r"(?i)^\s*(?:downloaded|converted|scanned|ripped|shared|generated)\s+(?:from|by|with)\b"
    r"|^\s*(?:visit|join|subscribe\s+to|follow\s+us|buy\s+the\s+book)\b"
    r"|^\s*(?:all\s+rights\s+reserved)\b"
)


def is_watermark_line(line: str) -> bool:
    """خط فقط وقتی واترمارک است که کوتاه باشد؛ جملهٔ داستان با نام سایت حذف نمی‌شود."""
    text = (line or "").strip()
    if not text:
        return False
    if _AD_RE.search(text):
        return True
    # «OceanofPDF.com» تنها در خطی کوتاه واترمارک است؛ نه داخل یک جملهٔ کتاب.
    return len(text) <= 60 and bool(_TOKEN_RE.search(text))


def strip_watermarks(text: str) -> str:
    """خطوط واترمارک را حذف می‌کند و فاصله‌های خالی اضافه را جمع می‌کند."""
    kept = [line for line in (text or "").splitlines() if not is_watermark_line(line)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
