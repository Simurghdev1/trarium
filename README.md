<div align="center">

# 🪴 Trarium

**موتور ترجمهٔ فارسی که چیدمان را خراب نمی‌کند.**

کتاب اسکن‌شده، رمان، مانگا، مقاله — متن فارسی همان‌جا می‌نشیند که متن اصلی بود.

[![CI](https://github.com/alimahamdarijanipro-stack/trarium-/actions/workflows/qa.yml/badge.svg?branch=qa%2Fv2.17.0-audit)](https://github.com/alimahamdarijanipro-stack/trarium-/actions/workflows/qa.yml)
[![Tests](https://img.shields.io/badge/tests-77%20passing-2ea043?style=flat-square)](#-تستها)
[![Version](https://img.shields.io/badge/version-2.17.0-1f6feb?style=flat-square)](CHANGELOG.md)
[![Persian](https://img.shields.io/badge/🇮🇷-فارسی-f9c74f?style=flat-square)](#)
[![License](https://img.shields.io/badge/license-MIT-8957e5?style=flat-square)](LICENSE)

</div>

---

## ✨ در یک نگاه

| 🎯 | چه می‌کند |
| --- | --- |
| 🖼️ | متن روی نقاشی را ترجمه **نمی‌کند** تا تصویر خراب نشود |
| 🎨 | کادر متن را با **رنگ خود کاغذ** می‌پوشاند، نه سفید |
| 📖 | صفحهٔ اسکن‌شده را تشخیص می‌دهد و مثل اسکن رفتار می‌کند |
| 🔤 | نیم‌فاصله، گیومهٔ فارسی و فاصلهٔ علائم را خودش درست می‌کند |
| 🔍 | هر بلوک را با متن اصلی مقایسه می‌کند: جاافتادگی، تکرار، نام ناهمگون |
| ✒️ | **سبک ترجمه** انتخاب می‌کنی، نه پرامپت طولانی |
| 🧠 | حافظهٔ اصطلاحات و نام‌ها، برای یکدستی در کل کتاب |
| ♻️ | سهمیهٔ مدل تمام شود، خودکار مدل بعدی ادامه می‌دهد |
| 🧱 | متن فارسی از کادر خودش بیرون نمی‌زند و روی پاراگراف بعدی نمی‌افتد |

## ✒️ سبک‌های ترجمه

| سبک | مناسب |
| --- | --- |
| روان *(پیش‌فرض)* | همه‌چیز |
| ادبی | رمان کلاسیک، کتاب مصور |
| روان و ساده | نوجوان، متن آموزشی |
| خودمونی | گفتگو و خاطره |
| رسمی | سند و گزارش |
| وفادار به متن | متنی که نباید تغییر کند |
| تخصصی | مقالهٔ فنی |
| مانگا / کمیک | حباب‌های گفتگو |
| لایت‌ناول | رمان سبک |

سبک فقط **یک خط** به دستور مدل اضافه می‌کند؛ مصرف توکن را بالا نمی‌برد.

## 🔍 بررسی کیفیت ترجمه

بعد از ترجمه و قبل از نوشتن در PDF، هر بلوک با متن اصلی مقایسه می‌شود:

- **جاافتادگی** — ترجمه کوتاه‌تر از آن است که همهٔ جمله‌ها را داشته باشد
- **جملهٔ گم‌شده** — تعداد جمله‌ها کم شده
- **تکرار** — دو بلوک مختلف یک ترجمه گرفته‌اند
- **ترجمه‌نشده** — بلوک هنوز لاتین است
- **ناهماهنگی نام** — نامی که در حافظهٔ پروژه ثبت شده، جور دیگری نوشته شده

با `--qc-repair` (یا تیک مربوطه در رابط) همان بلوک‌ها یک بار دیگر ترجمه می‌شوند؛
پیش‌فرض فقط گزارش است، چون هر تعمیر خرج دارد.

## 🖼️ Automatic Cover Detection — تشخیص خودکار جلد و صفحه‌های تزئینی

پیش از OCR و ترجمه، **هر صفحهٔ PDF جداگانه** بررسی می‌شود (نه فقط صفحهٔ اول) و یکی از
این وضعیت‌ها را می‌گیرد:

| وضعیت | معنی |
|---|---|
| `NORMAL_PAGE` | صفحهٔ عادی؛ ترجمه می‌شود |
| `BOOK_COVER` | جلد کتاب / لایت‌ناول |
| `MANGA_COVER` | جلد مانگا / کمیک |
| `TITLE_PAGE` | صفحهٔ عنوان و نیم‌عنوان |
| `ILLUSTRATION_PAGE` | صفحهٔ تمام‌تصویری و تزئینی |
| `AD_PAGE` | تبلیغات، معرفی مجموعه، صفحهٔ اعتبارها |
| `UNCERTAIN` | تشخیص نامطمئن؛ **ترجمه می‌شود** و فقط گزارش می‌شود |

صفحه‌ای که وضعیت آن یکی از جلد/عنوان/تصویری/تبلیغاتی باشد و اطمینانش از آستانه بیشتر
باشد، از ترجمه خارج می‌شود و **دقیقاً مثل فایل اصلی** می‌ماند: نه OCR می‌شود، نه متنش
پاک می‌شود، نه تصویرش دوباره رندر می‌شود، و نه جایش عوض می‌شود.

سه لایه شواهد استفاده می‌شود:

1. **ساختار صفحه** — متن لایهٔ متنی: پاراگراف چندصدکلمه‌ای یعنی صفحهٔ داستان؛ چند سطر
   کوتاه و وسط‌چین با فونت نمایشی روی تصویر تمام‌صفحه یعنی جلد.
2. **تحلیل تصویر** — نوارهای مرکب، تیرگی و اشباع رنگ، برای جدا کردن جلد عکسی از
   صفحهٔ اسکن‌شدهٔ متن.
3. **مدل بینایی** (اختیاری، با کلید Gemini) — فقط برای صفحه‌های مبهم: صفحهٔ تصویری اول/آخر
   فایل، و صفحه‌ای که شواهدش نزدیک آستانه است. تعداد درخواست‌ها سقف دارد.

قاعدهٔ ایمنی: **صفحه‌ای که تصویر دارد جلد نیست.** صفحهٔ ترکیبی تصویر + پاراگراف داستان،
صفحهٔ مانگا با حباب دیالوگ، و صفحهٔ بدون لایهٔ متنی که OCR می‌شود، همه ترجمه می‌شوند.
اگر همهٔ صفحه‌های یک فایل «جلد» تشخیص داده شوند، صفحهٔ دارای بیشترین متن برای ترجمه
نگه داشته می‌شود.

تنظیمات (در رابط وب و در `translate_pdf_optimized`):

```python
translate_pdf_optimized(
    ...,
    cover_detection=True,      # فعال/غیرفعال
    cover_threshold=0.72,      # آستانهٔ اطمینان
    manual_page_status={0: "MANGA_COVER", 3: "NORMAL_PAGE"},  # تغییر دستی وضعیت
    vision_api_key=os.environ.get("GEMINI_API_KEY"),           # تأیید با مدل بینایی
)
```

در رابط وب، دکمهٔ «🔎 مشاهدهٔ صفحه‌های تشخیص‌داده‌شده» جدول هر صفحه را با وضعیت،
اطمینان و دلیل نشان می‌دهد و وضعیت هر صفحه داخل همان جدول قابل تغییر است. خروجی هر اجرا
`page_status.json` و شمارش‌های گزارش را در `metrics.json` دارد.

## 🧪 تست‌ها

```bash
python3 -m venv .venv-qa && .venv-qa/bin/pip install -r requirements.txt pytest ebooklib
.venv-qa/bin/python -m pytest tests -q
```

۱۳۲ تست: چیدمان اسکن، رنگ کاغذ، متن روی تصویر، حباب مانگا، نیم‌فاصله،
سبک‌ها، بررسی کیفیت ترجمه، fallback مدل‌ها، تشخیص جلد/عنوان/تبلیغات و ترجمهٔ زیرنویس.



> **فایل‌های سنگین تست (PDF/PNG خروجی و ورودی) از این ریپو به [trarium-files](https://github.com/alimahamdarijanipro-stack/trarium-files) منتقل شده‌اند تا کلون‌کردن کد سبک بماند؛ لینک‌های زیر به همان‌جا اشاره می‌کنند.**

[![Trarium QA](https://github.com/alimahamdarijanipro-stack/trarium-/actions/workflows/qa.yml/badge.svg?branch=qa%2Fv2.17.0-audit)](https://github.com/alimahamdarijanipro-stack/trarium-/actions/workflows/qa.yml)

🔤 **[تست OCR.space → فارسی (۳ صفحه، تصویر کامل)](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/README.md)** — PDF اصلی، PDF پس از OCR، دو PDF ترجمه‌شده، عکس هر سه صفحه و متریک‌ها.

✅ **[آخرین اجرای QA (Actions)](https://github.com/alimahamdarijanipro-stack/trarium-/actions/runs/43619771082)** — سبز روی کامیت `dd8f97f` (شاخهٔ QA)، شامل ۴۷ تست واحد.

📦 **[خروجی‌های تست و اکشنز (Release)](https://github.com/alimahamdarijanipro-stack/trarium-/releases/tag/qa-2.17.0-2026-11-13)** — PDF گزارش، بستهٔ آرتیفکت و لاگ اجرا.

🧪 **[خروجی تست چینی → فارسی](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/zh-to-fa/trarium_zh_to_fa_text.pdf)** — PDF فارسی ساخته‌شده توسط خود تراریوم، همراه با [گزارش کامل](QA_REPORT.md).

📋 **[گزارش تست و ممیزی (QA_REPORT.md)](QA_REPORT.md)** — نتایج واقعی تست‌های واحد، مجموعهٔ QA، و گردش‌کار PDF با فایل‌های ورودی واقعی، عددهای اندازه‌گیری‌شده و تصویر صفحات.

نسخهٔ جدید هستهٔ ترجمه با تمرکز روی حفظ متن، کاهش مصرف API و امکان ادامهٔ امن کار.

### قابلیت‌های 2.17
- استخراج متن PDF قبل از OCR و اجرای OCR فقط روی صفحات واقعاً بدون لایهٔ متن
- Safe Optimization بدون حذف محتوای داستان
- Translation Memory / Cache نرمال‌شده و حذف درخواست‌های تکراری
- Checkpoint دائمی SQLite برای resume پس از خاموشی یا خطا
- Batch و تقسیم‌بندی Token-aware با اندازهٔ تطبیقی
- Placeholder برای URL، ایمیل، تگ و کد
- Character/Term Registry برای ثبات اسامی و اصطلاحات
- Fallback Scheduler برای عبور از خطای یک Provider
- Pre-flight estimation و connectivity probe
- حفظ فونت Sahel و صفحهٔ «ترجمه شده توسط تراریوم» در خروجی‌های متنی/PDF/EPUB/HTML

این نسخه بر پایهٔ ساختار 2.16.4 ارتقا داده شده و قابلیت‌های قبلی پروژه حفظ شده‌اند.

# 🌿 Trarium 2.17.0

Trarium is a small Streamlit application for translating books and web novels with AI, with an optional OCR layer and a PDF layout-preserving mode.

## What changed in 2.16.2

- Fixed the `ترجمه صفحه ... شامل ... بلاک خالی است` failure mode. Missing markers now trigger per-block recovery instead of aborting the whole page.
- PDF translation now batches blocks across pages and uses parallel requests. One API call is no longer forced per page.
- Failed text-translation chunks keep their original source text instead of disappearing.
- OCR is **optional**. Native PDF text is always tried first. Pages without a text layer are only OCRed when you explicitly enable OCR.
- PaddleOCR integration supports the modern 3.x `predict()` path and legacy 2.x fallback without passing the obsolete `show_log` argument.
- The layout mode can consume PDFs already OCRed by another tool; no second OCR pass is required when a text layer exists.
- Web-novel crawling follows real `rel=next` / “Next Chapter” links and stops on loops instead of inventing chapter URLs.
- Web-novel image downloads are parallelized and deduplicated.
- Added TXT/PDF/EPUB/HTML outputs and bundled the Sahel font for Persian output.
- Reworked the Streamlit UI into three clear modes: text translation, layout-preserving PDF, and web novel.
- Added a small OCR benchmark helper for testing about 10 pages.
- OCR dependencies moved out of the core requirements; Tesseract remains available only as an explicit fallback, not the automatic OCR path.

## Windows quick start

Run:

```bat
install_core.bat
run_trarium.bat
```

The core installation does **not** install heavy OCR engines.

### Optional PaddleOCR

For Windows OCR, use a separate Python environment. The included helper creates `.venv-paddle` with Python 3.12:

```bat
install_paddle_windows.bat
```

After that, `run_trarium.bat` automatically uses the Paddle environment when it exists, so PaddleOCR is visible to the UI. Current PaddleOCR documentation exposes OCR through its local Python pipeline and returns recognized text plus bounding boxes; the Trarium integration uses those results to place text back on PDF pages. See the current official docs for the exact environment support and models.


## AI providers

- **OpenRouter**: current free-model catalog with `openrouter/free`; automatic transport prefers `curl` when available on Windows and falls back to Python requests.
- **Google Gemini (direct REST)**: current Gemini 3.8/3.7/3.6/3.5 Flash model IDs. Google’s official available-region list currently does not include Iran, while it does include Azerbaijan.
- **Groq (direct)**: current GPT-OSS 120B/20B and Qwen 3.8 27B through Groq’s OpenAI-compatible endpoint.
- **OpenAI-compatible**: custom Base URL + key + model for other compatible gateways.

The UI has a small authenticated request test for direct providers and a two-step OpenRouter diagnostic (public `/models` plus a tiny chat completion).

## OCR workflow

There are three useful cases:

1. **Normal text PDF** → leave OCR off.
2. **PDF already OCRed by a service such as PDF24/iLovePDF** → leave OCR off; Trarium reads the existing text layer.
3. **Image-only scan** → enable OCR and choose an installed engine.

## Layout mode

Trarium reads native text blocks and their positions, translates them in batches, then replaces only blocks that were successfully translated. When a block cannot be translated, the original block is kept so content is not silently lost.

This mode is intended for clean, text-heavy book pages. Pages with complex artwork or text baked into a non-white image may need a specialized page compositor rather than a simple white redaction.

## Web novel mode

Enter a starting chapter URL and Trarium follows the site's actual next-chapter link. The scraper has site adapters for several common novel platforms plus a generic fallback.

JavaScript-heavy pages can be opened with the optional Playwright browser mode:

```bash
pip install playwright
playwright install chromium
```

## Notes on free models

OpenRouter's `openrouter/free` route is used by default in the UI. Free endpoints are rate-limited, so increasing parallelism does not always increase throughput. For stable results on a slower connection, start with 3–4 workers.

## OCR benchmark

In **حفظ قالب PDF**, enable OCR and use **بنچمارک ۱۰ صفحه** to compare the installed OCR engines on the first 10 scanned pages. It reports total time, average seconds/page, successful pages, detected blocks and errors.

## Tests

The project includes a smoke suite:

```bash
python -m unittest discover -s tests -v
```

It covers marker recovery, PDF layout output, failed-chunk preservation, native PDF extraction and Next Chapter detection.

## Project structure

```text
streamlit_app.py
trarium/
  pdf_optimized.py
  benchmark.py
  processors/
  translators/
  extractors/
  novel/
  output/
  fonts/Sahel.ttf
tests/
```


### v2.16.2
- Red/black UI and visible Trarium title.
- Fixed PDF layout font path and surfaced no-write failures.
- Added Trarium attribution to translated outputs.


## v2.16.2 highlights
- Reliable layout PDF pipeline for native and OCR text layers.
- Paragraph grouping for OCR lines to reduce API calls and improve context.
- Safe preflight before source redaction; no destructive redaction on failed insertion.
- Persistent translation cache in `~/.trarium/cache.sqlite3` for resume and duplicate-request avoidance.
- Automatic retry in text translation.
- No translated PDF is saved when zero blocks were successfully written.
- Detailed PDF layout write diagnostics.
- OCR text-layer fallback and OCR-line paragraph grouping.
- Echo detection: a model returning the source text is treated as a failed translation and retried.
- Web-novel next links are constrained to the same host.
## OpenRouter 403 diagnostic

If OpenRouter returns `403` with `Access denied by security policy`, Trarium now classifies it separately from a translation failure. In automatic transport mode it retries the request once through `curl` when available, because some security layers can treat HTTP clients differently. If both transports are blocked, the app stops immediately instead of retrying every block and wasting time.

The Streamlit UI also includes an **OpenRouter connection test** under the provider settings. A structured OpenRouter 403 (for example an account/guardrail restriction) is reported separately from a WAF-style 403. OpenRouter documents that guardrails can return HTTP 403, while its standard API examples use the same `/api/v1/chat/completions` endpoint and optional attribution headers.


## 🧪 QA و استقرار

- ممیزی کامل نسخهٔ ۲٫۱۷٫۰ با اندازه‌گیری واقعی: [QA_REPORT.md](QA_REPORT.md)
- مجموعهٔ تست واقعی (بدون کلید API، با سرور mock محلی): `python -m pytest qa/qa_suite.py -q`
- استقرار Railway: `Dockerfile` + `railway.json` آماده است؛ پورت از `$PORT` خوانده می‌شود.
  برای ماندگاری کش SQLite یک Volume روی `/root/.trarium` وصل کنید.
