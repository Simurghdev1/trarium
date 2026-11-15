# تست OCR.space — چینی → فارسی (۳ صفحه، فقط تصویر)

موتور OCR: **OCR.space** (کلید رایگان، `language=chs`، موتور ۲) — ۳ صفحه، ۳ درخواست، ۱۳۱۷ نویسه، ۷.۳ ثانیه
ترجمه: **Gemini رایگان** — `gemini-3-flash-preview` (۲۵ ثانیه) و `gemini-flash-lite-latest` (۱۳.۵ ثانیه)

| فایل | توضیح |
| --- | --- |
| [01_original.pdf](01_original.pdf) | PDF اصلی چینی، دست‌نخورده |
| [02_ocrspace_ocr.pdf](02_ocrspace_ocr.pdf) | همان صفحات + لایهٔ متنی OCR.space |
| [03_translated_gemini-gemini-flash-lite-latest.pdf](03_translated_gemini-gemini-flash-lite-latest.pdf) | ترجمه با Gemini Flash Lite (رایگان) |
| [04_translated_openrouter-nemotron-3-ultra-550b.pdf](04_translated_openrouter-nemotron-3-ultra-550b.pdf) | ترجمه با OpenRouter (مدل رایگان) |
| [metrics.json](metrics.json) | عددهای اندازه‌گیری‌شده |
| [ocr_blocks.json](ocr_blocks.json) | بلاک‌های خام OCR.space |
| [screenshots/](screenshots) | تصویر تک‌تک صفحات و مقایسه‌ها |
| [بخش ۱۷ گزارش QA](../../../QA_REPORT.md) | توضیح پرووایدر، تست‌ها و محدودیت‌ها |

## صفحهٔ ۱ — اصلی و ترجمه‌شده

![صفحه ۱ اصلی](pages/original-p1.jpg)
![صفحه ۱ ترجمه](pages/translated-p1.jpg)

## صفحهٔ ۲

![صفحه ۲ اصلی](pages/original-p2.jpg)
![صفحه ۲ ترجمه](pages/translated-p2.jpg)

## صفحهٔ ۳

![صفحه ۳ اصلی](pages/original-p3.jpg)
![صفحه ۳ ترجمه](pages/translated-p3.jpg)

**وضعیت این اجرا در یک نگاه:** ۸۲ بلاک، ۵۴ نوشته‌شده، ۰ ناموفق، ۰ نویسهٔ چینی باقی‌مانده، ۰ علامت سؤال.
هر دو پرووایدر تست شدند: Gemini Flash Lite در ۱۳.۶ ثانیه و OpenRouter (مدل رایگان) در ۳۹۹.۸ ثانیه.

**رندر جدید:** ترجمه روی همان خطوط اصلی نوشته می‌شود (هر خط کادر خودش را دارد)، پس جدول‌ها، نمودارها و تصاویر جای خودشان می‌مانند و فضای سفید بزرگ ساخته نمی‌شود.

## OpenRouter — سه صفحه

![صفحه ۱ OpenRouter](pages/translated-openrouter-p1.jpg)
![صفحه ۲ OpenRouter](pages/translated-openrouter-p2.jpg)
![صفحه ۳ OpenRouter](pages/translated-openrouter-p3.jpg)
