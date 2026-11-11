"""ابزارهای مشترک تراریوم."""
import re

# زبان‌هایی که از راست به چپ نوشته می‌شوند
RTL_LANGS = {"fa", "ar", "he", "ur", "ps", "ckb"}


def is_rtl_lang(lang: str) -> bool:
    """آیا زبان مقصد راست‌به‌چپ است؟"""
    return (lang or "").lower() in RTL_LANGS


def reshape_rtl(text: str) -> str:
    """تبدیل متن فارسی/عربی به ترتیب بصری.

    PyMuPDF هنگام نوشتن با insert_text/insert_textbox شکل‌دهی حروف و
    راست‌به‌چپ‌سازی را انجام نمی‌دهد؛ پس متن باید پیش از نوشتن reshaped شود.
    اگر کتابخانهٔ arabic-reshaper نصب نباشد، bidi تنها اجرا می‌شود و در
    نهایت متن اصلی برگردانده می‌شود (ترجمه هرگز از بین نمی‌رود).
    """
    if not text:
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        try:
            from bidi.algorithm import get_display
            return get_display(text)
        except ImportError:
            return text
    except Exception:
        return text


def clean_translation(text: str) -> str:
    """پاک‌سازی محافظه‌کارانه خروجی ترجمه بدون حذف کلمات واقعی متن."""
    if not text:
        return ""

    lines = []
    metadata_line = re.compile(
        r"^\s*(?:user\s*safety|let\s+me\s+think|here(?:'s| is)\s+the\s+translation|"
        r"translation|output(?:\s+only)?|response|answer|note|important|warning)\s*:\s*$",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        stripped = line.strip()
        # Remove only explicit meta lines, not words occurring inside the translation.
        if metadata_line.match(stripped):
            continue
        if re.fullmatch(r"```(?:text|plaintext|translation)?", stripped, re.IGNORECASE):
            continue
        if re.fullmatch(r"```", stripped):
            continue
        if re.fullmatch(r"\[?(?:user\s+safety|keep\s+unchanged|do\s+not\s+change|preserve)\]?", stripped, re.IGNORECASE):
            continue
        lines.append(line)

    result = "\n".join(lines).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    # Normalize spaces on each line while preserving newlines and punctuation.
    result = "\n".join(re.sub(r"[ \t]{2,}", " ", line).rstrip() for line in result.split("\n"))
    return result.strip() if result.strip() else text.strip()


def probably_already_in_persian(text: str) -> bool:
    """تشخیص محافظه‌کارانه متن فارسی برای جلوگیری از ترجمه مجدد."""
    if not text or len(text) < 40:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    arabic_letters = sum(1 for c in letters if "\u0600" <= c <= "\u06ff")
    persian_specific = sum(1 for c in text if c in "پچژگک")
    return arabic_letters / len(letters) >= 0.88 and persian_specific >= 1
