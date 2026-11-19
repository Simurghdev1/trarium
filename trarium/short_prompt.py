"""پرامپت کوتاه: کلیدواژه‌های «ارسال سریع».

هر کلیدواژه یک برچسب فارسی برای کاربر است و در پس‌زمینه به یک دستور کوتاه
انگلیسی تبدیل می‌شود؛ این‌طوری یک خط چند توکنی به پرامپت اضافه می‌شود، نه یک
درخواست تازه. کلیک روی کلیدواژه هیچ فراخوانی مدل و هیچ توکنی مصرف نمی‌کند.
"""
from __future__ import annotations

# کمینهٔ طول برچسب: کلیدواژه‌های خیلی کوتاه مبهم‌اند و مدل هم برای فهمیدنشان
# توکن بیشتری لازم دارد.
MIN_CHIP_CHARS = 10

SHORT_PROMPT_CHIPS: dict[str, str] = {
    "ادبی و شاعرانه ننویس": "plain, non-literary prose",
    "ترجمهٔ عادی و روان": "natural everyday prose",
    "خودمونی و محاوره‌ای": "colloquial spoken register",
    "لحن رسمی و کتابی": "formal literary register",
    "جمله‌ها کوتاه باشند": "keep sentences short",
    "نام‌ها را ترجمه نکن": "keep proper names as written",
    "بدون توضیح و ایموجی": "no notes, no emoji",
    "اصطلاح‌ها را فارسی کن": "localize idioms into Persian",
}


CONFLICT_GROUPS: list[set[str]] = [
    # Only one register can be asked for at a time.
    {"ادبی و شاعرانه ننویس", "ترجمهٔ عادی و روان", "خودمونی و محاوره‌ای", "لحن رسمی و کتابی"},
]


def conflicting_labels(label: str) -> set[str]:
    """Labels that cannot be active together with `label`."""
    for group in CONFLICT_GROUPS:
        if label in group:
            return group - {label}
    return set()


def filter_conflicts(labels) -> list[str]:
    """Keep the newest register chip and drop the contradictory older ones."""
    result: list[str] = []
    for label in labels or []:
        for group in CONFLICT_GROUPS:
            if str(label) in group:
                result = [item for item in result if item not in group]
        if label not in result:
            result.append(label)
    return result


def validate_chips() -> None:
    """قاعدهٔ توکن: دست‌کم دو کلیدواژه و هرکدام دست‌کم ۱۰ نویسه."""
    if len(SHORT_PROMPT_CHIPS) < 2:
        raise ValueError("پرامپت کوتاه باید دست‌کم دو کلیدواژه داشته باشد.")
    short = [label for label in SHORT_PROMPT_CHIPS if len(label.strip()) < MIN_CHIP_CHARS]
    if short:
        raise ValueError(f"این کلیدواژه‌ها کوتاه‌تر از {MIN_CHIP_CHARS} نویسه‌اند: {short}")


def build_short_prompt(selected_labels, extra: str = "") -> str:
    """از کلیدواژه‌های انتخاب‌شده و متن دلخواه، یک خط کوتاه می‌سازد."""
    parts: list[str] = []
    for label in selected_labels or []:
        directive = SHORT_PROMPT_CHIPS.get(str(label).strip())
        if directive and directive not in parts:
            parts.append(directive)
    typed = str(extra or "").strip()
    if typed and typed not in parts:
        parts.append(typed)
    return "; ".join(parts)


validate_chips()
