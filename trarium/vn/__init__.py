"""ترجمهٔ ویژوال ناول (آزمایشی).

ورودی: پوشه یا زیپ بازی. خروجی: پچ فارسی (فقط فایل‌های عوض‌شده) و کل بازیِ
فارسی‌شده. فایل اجرایی بازی (exe/app) دست نمی‌خورد؛ فقط متن‌ها و فونت عوض می‌شوند.

موتورها: Ren'Py (کامل، با فونت و راست‌به‌چپ)، RPG Maker MV/MZ (متن‌ها؛ نمایش
فارسی پیوسته هنوز پلاگین RTL لازم دارد).
"""
from trarium.vn.detect import detect_engine
from trarium.vn.build import translate_game

__all__ = ["detect_engine", "translate_game"]
