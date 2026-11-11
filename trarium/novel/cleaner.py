"""
🌿 تراریوم — پاک‌سازی متن وب‌نول
"""
import re


def clean_novel_text(text: str) -> str:
    """پاک‌سازی جامع متن وب‌نول."""
    if not text:
        return ""

    lines = text.split("\n")
    cleaned = []

    # الگوهای حذف
    ad_patterns = [
        # تبلیغات
        r"(?i)click\s*here\s*to\s*(read|continue|support)",
        r"(?i)read\s*more\s*at",
        r"(?i)sponsored\s*content",
        r"(?i)advertisement",
        r"(?i)join\s*our\s*(discord|telegram|server|community)",
        r"(?i)please\s*rate\s*and\s*review",
        r"(?i)if\s*you\s*want\s*to\s*support",
        r"(?i)consider\s*sponsoring",
        r"(?i)patreon\.com",
        r"(?i)donation",
        r"(?i)vote\s*for\s*this\s*novel",
        r"(?i)add\s*this\s*novel",
        r"(?i)bookmark\s*this",
        r"(?i)follow\s*me\s*on",
        r"(?i)my\s*other\s*novels",
        r"(?i)check\s*out\s*my",
        r"(?i)latest\s*chapter\s*at",
        r"(?i)for\s*more\s*chapters",
        r"(?i)visit\s*our\s*site",
        # خطوط خالی زیاد
        r"^\s*[-=]{5,}\s*$",
        r"^\s[*]{5,}\s*$",
    ]

    for line in lines:
        stripped = line.strip()

        # خط خالی → حفظ
        if not stripped:
            cleaned.append("")
            continue

        # خط تبلیغاتی → حذف
        skip = False
        for pattern in ad_patterns:
            if re.search(pattern, stripped):
                skip = True
                break

        # فقط خطی که عملاً یک URL مستقل است حذف شود؛ URL داخل متن داستان حفظ شود.
        if re.fullmatch(r"https?://\S+", stripped, re.IGNORECASE):
            skip = True
        if skip:
            continue

        # خط خیلی کوتاه (< 2 حرف) → حذف
        if len(stripped) < 2 and not stripped.isdigit():
            continue

        cleaned.append(stripped)

    # حذف خطوط خالی اضافی
    result = "\n".join(cleaned)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip()


def detect_language(text: str) -> str:
    """تشخیص زبان متن."""
    if not text:
        return "unknown"

    # شمارش کاراکترها
    arabic = len(re.findall(r"[\u0600-\u06FF]", text))
    persian = len(re.findall(r"[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF]", text))
    latin = len(re.findall(r"[a-zA-Z]", text))
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]", text))

    total = len(text)
    if total == 0:
        return "unknown"

    # تشخیص — اول بررسی عربی خالص (بدون کاراکترهای فارسی خاص)
    persian_only = len(re.findall(r"[پچژکگی]", text))  # کاراکترهای فقط فارسی
    if arabic / max(total, 1) > 0.3 and persian_only < 3:
        return "ar"
    if persian / max(total, 1) > 0.3:
        return "fa"
    if cjk / max(total, 1) > 0.3:
        if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", text):
            return "ja"
        return "zh"
    if latin / max(total, 1) > 0.5:
        return "en"

    return "unknown"


def split_into_paragraphs(text: str, max_chars: int = 2000) -> list[str]:
    """تقسیم متن به پاراگراف‌های مناسب برای ترجمه."""
    if not text:
        return []

    paragraphs = text.split("\n\n")

    # اگه پاراگراف‌ها خیلی بزرگن، بشکون
    result = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(para) <= max_chars:
            result.append(para)
        else:
            # بشکون بر اساس جمله
            sentences = re.split(r"(?<=[.!?])\s+", para)
            current = ""
            for sentence in sentences:
                if len(current) + len(sentence) > max_chars:
                    if current:
                        result.append(current.strip())
                    current = sentence
                else:
                    current += " " + sentence if current else sentence
            if current:
                result.append(current.strip())

    return result if result else [text]
