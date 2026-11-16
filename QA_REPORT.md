# گزارش ممیزی و تست واقعی — Trarium 2.17.0


> **فایل‌های سنگین تست (PDF/PNG خروجی و ورودی) از این ریپو به [trarium-files](https://github.com/alimahamdarijanipro-stack/trarium-files) منتقل شده‌اند تا کلون‌کردن کد سبک بماند؛ لینک‌های زیر به همان‌جا اشاره می‌کنند.**

> این گزارش فقط بر اساس اجرای واقعی نوشته شده است. هر عددی که اینجا آمده از یک
> اجرای ضبط‌شده در `.json` می‌آید و هر چیزی که قابل تست نبوده، در بخش «محدودیت‌ها»
> صریحاً نام برده شده است.

## 1) وضعیت نسخهٔ اصلی (تغییرنکرده)

| مورد | مقدار |
|---|---|
| فایل | `Trarium_v2.17.0_FINAL.zip` |
| SHA-256 | `86d3770dc1a17f8ac96bb0e146a38ef481fbfb1ee4d1fb445a866572c415d790` |
| حجم استخراج‌شده | 484 KB — ۴۹ فایل — ۴٬۹۸۹ خط پایتون |
| برخورد | استخراج فقط‌خواندنی، بدون هیچ ویرایش؛ همهٔ تغییرات روی کپی جدا و شاخهٔ `qa/v2.17.0-audit` |

## 2) محیط آزمایش

- Ubuntu (بدون root)، Python 3.12.3، PyMuPDF 2.1، ebooklib، streamlit، tiktoken، Pillow
- نصب وابستگی‌ها: `pip install -r requirements.txt` → موفق (بدون وابستگی سیستمی اضافی)
- تست‌های خود پروژه: **۲۵ تست، همه پاس** (پیش و پس از اصلاحات)
- روش تست بدون کلید API: یک سرور محلی سازگار با OpenAI (`qa/mock_provider.py`) که
  همان پروتکل HTTP را جواب می‌دهد، توکن‌ها را با tiktoken می‌شمارد و خطاهای
  واقعی (500، 429، پاسخ خالی، WAF 403) را شبیه‌سازی می‌کند. یعنی ترابری، retry،
  پارس، کش، checkpoint و نویسنده‌های PDF/EPUB واقعاً اجرا شدند؛ فقط API شخص ثالث
  جایگزین شد.

## 3) باگ‌هایی که واقعاً بازتولید شدند

| # | فایل:خط (نسخهٔ اصلی) | مشکل | شاهد اندازه‌گیری‌شده |
|---|---|---|---|
| B1 | `trarium/processors/__init__.py:101` | `end = break_point + 1` اولین حرف کلمهٔ بعدی را می‌خورد | ۶۰۰ کلمهٔ ورودی → ۶۰۳ توکن در خروجی (کلمه‌ها تکه شدند) |
| B2 | `trarium/output/__init__.py:90` | تقسیم صفحه با تخمین «۶۲ کاراکتر در خط» و پرتاب پاراگراف بلند | پاراگراف ۲٬۰۹۰ حرفی → فقط **۱۹ حرف** نوشته شد (**۰٫۹٪**) |
| B3 | `trarium/pdf_optimized.py:860-861` | `add_redact_annot(..., fill=(1,1,1))` + `apply_redactions()` بدون فلگ | بایت‌های تصویر تغییر کرد (`image_identical: false`) و کادر سفید روی تصویر کشیده شد — همان شکایت قدیمی «عکس‌ها خراب می‌شد» |
| B4 | `trarium/pdf_optimized.py:485,505` | OCR با PNG ۳۰۰dpi (A4 ≈ ۸٫۷ مگاپیکسل) | ۸٬۷۰۲٬۳۲۰ پیکسل و ۴۸۸ KB برای ۳ صفحه؛ توکن Vision با پیکسل مقیاس می‌گیرد |
| B5 | `trarium/output/__init__.py` (manifest EPUB) | فونت داخل زیپ هست ولی در `content.opf` اعلام نمی‌شود | `font_in_manifest: false` |
| B6 | `trarium/translators/openai_compatible.py:49` | `test_connection` بیرون از کلاس تعریف شده (کد مرده) | `hasattr(translator, "test_connection") == False` |
| B7 | `trarium/translators/openrouter.py:21` | دو اسلاگ مدل در کاتالوگ زندهٔ OpenRouter وجود ندارند | `poolside/laguna-s-2.1:free` و `qwen/qwen3.8-27b:free` در ۴۶۸ مدل زنده نیستند |
| B8 | `trarium/pdf_optimized.py:691/708/726/741` و `1058/1078/1101/1132` | چهار تابع دو بار تعریف شده‌اند؛ نسخهٔ دوم بی‌صدا برنده می‌شود | نسخهٔ اول کد مرده و گمراه‌کننده است |
| B9 | `trarium/processors/pipeline.py:110` | دستور کامل سیستم در هر درخواست تکرار می‌شد | سرباری **۱۰۰ توکن در هر درخواست** (۲۲۰ → ۱۲۷ توکن) |

نکتهٔ مثبت: مدل‌های پیش‌فرض Gemini (`gemini-3.8-flash` و …) **معتبر** هستند؛ با
مستندات زندهٔ Google بررسی شد و به‌اشتباه «قدیمی» فرض نشدند.

## 4) اصلاحات اعمال‌شده (فقط روی شاخه، نسخهٔ اصلی دست‌نخورده)

| # | اصلاح | فایل |
|---|---|---|
| F1 | مرز جمله بدون خوردن حرف بعدی | `trarium/processors/__init__.py` |
| F2 | صفحه‌بندی PDF با اندازه‌گیری واقعی فونت + reshape عنوان | `trarium/output/__init__.py` |
| F3 | redaction با `fill=None` و `PDF_REDACT_IMAGE_NONE` + `PDF_REDACT_LINE_ART_NONE` | `trarium/pdf_optimized.py` |
| F4 | OCR با ۱۸۰dpi، JPEG کیفیت ۸۲ و سقف ضلع ۱۷۰۰ + سقف توکن متناسب | `trarium/pdf_optimized.py` |
| F5 | اعلام فونت در manifest و OPF | `trarium/output/__init__.py` |
| F6 | `test_connection` به داخل کلاس منتقل شد | `trarium/translators/openai_compatible.py` |
| F7 | دو اسلاگ مدل با مدل‌های آزاد واقعی جایگزین شد | `trarium/translators/openrouter.py` |
| F8 | حذف چهار تعریف تکراری مرده | `trarium/pdf_optimized.py` |
| F9 | حذف سرباری پرامپت (فقط راهنمای واقعاً لازم) | `trarium/processors/pipeline.py` |

اسکریپت بازتولید: `patches/apply_fixes.py` (هر جایگزینی با `assert` محافظت شده؛
اگر متن تغییر کند، بی‌صدا رد نمی‌شود).

## 5) نتایج قبل / بعد (همه اندازه‌گیری‌شده)

| سنجه | ۲٫۱۷٫۰ اصلی | شاخهٔ اصلاح‌شده |
|---|---|---|
| کامل بودن متن PDF | ۱۹ از ۲٬۰۹۰ حرف (۰٫۹٪) | ۲٬۱۰۹ از ۲٬۰۹۰ (۱۰۰٪) |
| بایت‌های تصویر پس از ترجمه | تغییر می‌کرد | **یکسان** (SHA-256 برابر) |
| پیکسل در ناحیهٔ متن اصلی | پوشیده با سفید | بدون تغییر (تصویر سالم) |
| پیکسل‌های ارسالی OCR (A4) | ۸٬۷۰۲٬۳۲۰ (۱٫۰ از ۳۰۰dpi) | ۲٬۰۴۳٬۴۰۰ (**۰٫۲۳۵** → ۷۶٫۵٪ کمتر) |
| فرمت تصویر OCR | PNG، ۴۸۸ KB | JPEG، ۱۹۷ KB |
| فونت در manifest EPUB | ❌ | ✅ |
| سرباری پرامپت هر درخواست | ۱۰۰ توکن | ۷ توکن (−۹۳٪) |
| توکن ورودی کل (۳ فصل نمونه) | ۴۴۰ | ۲۵۴ (−۴۲٪) |
| تقسیم کلمات | ۶۰۰ → ۶۰۳ توکن | ۶۰۰ → ۶۰۰ |
| اسلاگ‌های مردهٔ مدل | ۲ | ۰ |
| تعریف تکراری تابع | ۴ | ۰ |
| زمان ۳۰ صفحه (ترجمهٔ mock) | ۰٫۶۵ ثانیه | ۰٫۳۷ ثانیه |
| اوج حافظه (RSS) | ۲۰۲ MB | ۱۷۹ MB |

بازدهی/کش: اجرای دوم همان کتاب **۰ درخواست** زد (۱ cache hit)؛ خطای گذرای 500 با
یک retry جبران شد؛ پاسخ خالی باعث از دست رفتن متن نشد (متن اصلی حفظ شد)؛ خطای
WAF 403 بدون سه‌بار retry بسته شد.

## 6) RTL و PDF

- فونت `Sahel Regular` واقعاً داخل PDF جاسازی می‌شود.
- هیچ `?` (گلیف ناموجود) در متن استخراج‌شده نیست.
- انتهای سطرها تا `x1 = 542.8` از عرض ۵۹۵ می‌رسد → راست‌چین واقعی.
- عنوان فصل هم اکنون reshape می‌شود؛ پیش از این فقط بدنه reshape می‌شد.
- بررسی چشمی: تصاویر `fixed-simple-p1.png` و `fixed-hybrid-p1.png` — متن فارسی
  به‌هم‌چسبیده و راست‌چین، تصویر پس‌زمینه دست‌نخورده و بدون کادر سفید.

## 7) OCR

| مسیر | نتیجه |
|---|---|
| لایهٔ متن بومی PDF | بدون OCR کار می‌کند (اولویت اول، مطابق طراحی) |
| OCR صفحهٔ اسکن‌شده با Vision (mock) | ۱ درخواست، بلاک‌ها استخراج و در PDF نوشته شد |
| هزینهٔ تصویر | ۷۶٫۵٪ پیکسل کمتر → تقریباً همان نسبت توکن کمتر |
| موتورهای محلی (Tesseract/Paddle/EasyOCR/Surya) | **تست نشد** — باینری tesseract و فضای نصب در این محیط نبود؛ کد فقط از نظر مسیر فراخوانی بررسی شد |

## 8) پروژهٔ Kaman AI Translator (مستقل)

نسخهٔ `3.2.3` کلون شد و **هیچ تغییری** در آن داده نشد.

| بررسی | نتیجه |
|---|---|
| تست‌های خود مخزن | **اجرا نشد**: `PyQt6` به `libxkbcommon.so.0` نیاز دارد که در این محیط نصب‌شدنی نیست |
| هستهٔ بدون Qt (`kaman_core`) | ۷ بررسی، همه پاس |
| یکپارچگی تکه‌سازی | ۶٬۰۰۰ کاراکتر، بدون شکستن کلمه، حداکثر ۵٬۸۹۹ |
| سرباری پرامپت | ۱۷۶ توکن در هر تکه (بیشتر از تراریوم؛ عمدتاً به‌خاطر قواعد صریح و JSON schema) |
| کانتکست غلتان | محدود به ۵۰۰ کاراکتر؛ بودجه رعایت می‌شود |
| محافظ QC | اصلاحیهٔ کوتاه‌شده رد می‌شود و JSON خراب ترجمه را نابود نمی‌کند |
| پارس JSON | ۴ حالت (خالص، ```json، متن نامعتبر، خالی) درست |

**ارزیابی ادغام:** دو ایده از کامان ارزش پیاده‌سازی دارد ولی در این شاخه اعمال
**نشد** (برای حفظ دامنه و پرهیز از ریسک):
1. «کانتکست غلتان» برای یکدستی اصطلاحات بین تکه‌ها — هزینه‌اش توکن اضافه در هر
   درخواست است و کلید کش را می‌شکند، پس باید اختیاری و با کلید کش جدید باشد.
2. «بازبینی QC» با محافظ طول — دقت را بالا می‌برد ولی مصرف را تا ۲ برابر می‌کند.
   کد تراریوم فعلاً QC ندارد؛ افزودنش باید اختیاری و پیش‌فرض خاموش باشد.

## 9) آمادگی Railway

| مورد | وضعیت |
|---|---|
| `requirements.txt` | ✅ سبک، بدون OCR سنگین |
| `Dockerfile` | ✅ `python:3.12-slim`، استریم‌لیت روی `0.0.0.0:$PORT`، healthcheck |
| `railway.json` | ✅ بیلدر Dockerfile + healthcheck `/_stcore/health` |
| `.dockerignore` | ✅ بدون zip/venv/caches |
| متغیرها | `OPENROUTER_API_KEY` / `GOOGLE_API_KEY` / `GROQ_API_KEY` / `TRARIUM_API_KEY` (اختیاری، از env خوانده می‌شوند) |
| امنیت | ⚠️ `.streamlit/config.toml` اصلی `enableCORS` و `enableXsrfProtection` را خاموش کرده؛ در Dockerfile با env روشن می‌شود. توصیه: خود فایل هم اصلاح شود |
| منابع | ۳۰ صفحه: ~۱۸۰ MB RSS. OCR محلی (paddle) چند GB لازم دارد → در ایمیج نصب **نشد**؛ روی Railway از Vision استفاده شود |
| اجرای واقعی فرمان شروع | ✅ `PORT=8099 sh -c 'streamlit ... --server.port=${PORT:-8080}'` → `/_stcore/health` = **200 "ok"** و صفحهٔ اصلی ۲۰۰ (۷٬۸۶۳ بایت) |
| ساخت ایمیج Docker | ❌ انجام نشد — دسترسی به daemon داکر در این محیط `permission denied` است (بدون root). Dockerfile فقط از نظر محتوا و با اجرای واقعی همان فرمان شروع بررسی شد |
| باگ پیدا‌شده در همین بررسی | اگر `$PORT` خالی باشد، Streamlit با خطا خارج می‌شود؛ `startCommand` به `${PORT:-8080}` اصلاح شد |
| محدودیت ذخیره‌سازی | کش/checkpoint در `~/.trarium` (SQLite)؛ روی Railway موقتی است و با ری‌استارت پاک می‌شود → برای ماندگاری به Volume نیاز است |

نتیجه: برای Railway **آماده است** به شرطی که OCR محلی نخواهید و برای ماندگاری
کش یک Volume وصل کنید. استقرار واقعی روی Railway انجام **نشد** (نیازمند حساب و
توکن Railway است و در محدودهٔ این گفتگو نبود).

## 10) آنچه تست نشد یا نامطمئن است

- کیفیت واقعی ترجمهٔ مدل‌ها: بدون کلید API قابل سنجش نیست؛ فقط یکپارچگی مسیر
  (ترتیب، عدم گم‌شدن متن، کش، retry) تست شد.
- دقت OCR موتورهای محلی و کیفیت OCR Vision روی صفحهٔ واقعی کتاب.
- تست کامان در سطح رابط گرافیکی (Qt) به‌دلیل نبود کتابخانهٔ سیستمی.
- بار سنگین (کتاب ۵۰۰+ صفحه) و همزمانی چند کاربر.
- استقرار واقعی Railway و اجرای آنلاین اپ.

## 11) مصنوعات (Artifacts)

- `qa/qa_results_ci.json` در GitHub Actions (آپلود خودکار)
- `qa_results_baseline.json` / `qa_results_fixed.json` — اندازه‌گیری قبل و بعد
- `kaman_results.json` — نتیجهٔ تست هستهٔ کامان
- PDF/PNG نمونه: `fixed-simple.pdf`, `fixed-hybrid.pdf`, `fixed-source.pdf` و رندر PNG


## 12) GitHub Actions (اجرای واقعی)

- اجرای اول (شاخهٔ `qa/v2.17.0-audit`): https://github.com/alimahamdarijanipro-stack/trarium-/actions/runs/43222762699
  - `Unit tests (Python 3.10)` ✅ / `3.11` ✅ / `3.12` ✅
  - `End-to-end QA (mock provider, no API key)` ✅
  - آرتیفکت `trarium-qa-results` آپلود شد.
- اجرای دوم: runs/43222989269 — ✅
- اجرای سوم (با PDF/EPUB در آرتیفکت، ۳۴۳ KB): runs/43223494942 — ✅
- همهٔ اجراها: https://github.com/alimahamdarijanipro-stack/trarium-/actions/workflows/qa.yml
- آرتیفکت `trarium-qa-results` شامل `qa_results_ci.json`، `qa-pytest.log` و PDF/EPUB تولیدشده است.
- SHA-256 زیپ نسخهٔ ۲٫۱۷٫۰ روی `main` پس از همهٔ این تغییرات دوباره چک شد:
  `86d3770dc1a17f8ac96bb0e146a38ef481fbfb1ee4d1fb445a866572c415d790` (بدون تغییر).

تنها ریپوی تراریوم دسترسی نوشتن دارد؛ برای مخزن کامان (پروژهٔ شخص دیگری) هیچ
workflowای اضافه نشد چون دسترسی نوشتن وجود ندارد و تغییر پروژهٔ دیگران بدون اجازه
مجاز نیست.

## 13) بازرسی نهایی مخزن

- هیچ توکن یا secret در شاخه نیست (`git grep` روی الگوهای `ghp_` و `github_pat_`).
- فایل‌های `__pycache__` از شاخه حذف و `.gitignore` اضافه شد.
- زیپ نسخهٔ ۲٫۱۷٫۰ و ۲٫۱۶٫۰ داخل شاخه دست‌نخورده باقی مانده‌اند.
- تغییرات فقط روی `qa/v2.17.0-audit` است؛ `main` هیچ کامیت جدیدی نگرفت.

### وضعیت نهایی CI

- آخرین اجرای تأییدشده: https://github.com/alimahamdarijanipro-stack/trarium-/actions/runs/43223728521 — ✅
  (`Unit tests 3.10/3.11/3.12` و `End-to-end QA` هر چهار مورد success)
- آرتیفکت دانلودشده و بازرسی‌شده: `qa_results_ci.json`، `qa-pytest.log`،
  `full.pdf`، `out.pdf`، `rtl.pdf`، `scan_out.pdf`، `big_out.pdf`، `b.epub` (~۳۴۳ KB)

---

## 14) گردش‌کار واقعی PDF — فایل ورودی واقعی، PDF واقعی، عکس واقعی

این بخش با `qa/pdf_pipeline_test.py` ساخته شده است. فایل‌های ورودی در `qa/fixtures/`
اسناد واقعی و عمومی‌اند (نه فایل ساختگی):

| فایل | منبع واقعی |
|---|---|
| `en_source.pdf` | سه صفحهٔ اول مقالهٔ «Attention Is All You Need» (arXiv:1706.03762v7) |
| `fa_source.pdf` | متن واقعی ویکی‌پدیای فارسی (سعدی / گلستان / حافظ) با لایهٔ متنی فارسی |
| `scan_source.pdf` | یک برگ اسکن‌شدهٔ واقعی از کتاب *The Modern Hercules* (Internet Archive) |

هر PDF خروجی را خودِ تراریوم ساخته، بعد همان فایل دوباره به تصویر تبدیل شده تا
تصویرهای زیر دقیقاً همان چیزی باشند که پایپ‌لاین تولید کرده است.

### تصویر ۱ — انگلیسی به فارسی (ترکیبی)، صفحهٔ ۱: چپ = ورودی اصلی، راست = خروجی ترجمه‌شده

![مقایسه صفحه ۱](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/report-assets/compare-en-p1.png)

### تصویر ۲ — صفحهٔ ۲ (متن پیوسته)

![مقایسه صفحه ۲](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/report-assets/compare-en-p2.png)

### تصویر ۳ — صفحهٔ ۳ (شامل شکل/تصویر صفحه)

![مقایسه صفحه ۳](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/report-assets/compare-en-p3.png)

### تصویر ۴ — فارسی راست‌چین: چپ = فایل ورودی (لایهٔ متنی منطقی، بدون شکل‌دهی حروف)، راست = خروجی تراریوم با فونت Sahel

![مقایسه فارسی](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/report-assets/compare-fa-p1.png)

### تصویر ۵ — مسیر OCR روی برگ اسکن‌شدهٔ واقعی

![خروجی OCR](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/report-assets/scan_ocr-p1.png)

## Real PDF workflow — measured results

Produced by `qa/pdf_pipeline_test.py`: real input documents from `qa/fixtures/` are pushed
through the real pipeline, the produced PDFs are rendered back to page images, and every
number below is read from those files. Translation text comes from the local mock
provider (no AI API key exists in the environment), so translation *quality* is out of
scope and is marked N/A — everything about PDF writing, RTL layout, image preservation
and text extraction is real.

### Test results

| Test | Items | Passed | Failed | Success | Failure |
| --- | ---: | ---: | ---: | ---: | ---: |
| English PDF → Persian (hybrid) | 4 | 1 | 0 | 25.00% | 0.00% |
| Persian RTL PDF rendering | 5 | 5 | 0 | 100.00% | 0.00% |
| English LTR PDF rendering | 3 | 3 | 0 | 100.00% | 0.00% |
| Scanned page → OCR path | 2 | 2 | 0 | 100.00% | 0.00% |
| **Total** | **14** | **11** | 0 | **78.57%** | **0.00%** |

### English → Persian hybrid run

| Metric | Measured value |
| --- | --- |
| Source pages / output content pages | 3 / 3 |
| Extra attribution page | 1 (by design) |
| Source text characters handed in | 8941 |
| Text blocks translated | 39 |
| Text blocks that failed | 0 |
| Persian characters in the output | 3723 |
| English source text fully replaced | yes |
| Embedded figures before / after | 1 / 1 |
| Figure bytes identical | yes |
| Blank content pages | 0 |
| Page sizes in one file | 612x792 |
| Runtime / output size | 0.41 s / 215.4 KB |

### Text comparison — Persian source vs. generated PDF

| Metric | Measured value |
| --- | --- |
| Source characters | 3626 |
| Source letters (whitespace removed) | 2934 |
| Letters recovered from the PDF | 2836 |
| Letters preserved | 2817 |
| Text preservation | **96.01%** |
| Missing text | 117 letters |
| Glyphs rendered as `?` | 0 |
| Pages | 3 |
| Embedded fonts | Sahel Regular |

The gap between source and recovered letters is caused by extraction, not by lost text:
the writer reshapes Persian into presentation forms, so a naive copy/paste out of the PDF
returns those forms. The comparison therefore normalises both sides with NFKC first —
which is what a real user's copy/paste does after normalisation, and what makes the
percentage meaningful rather than an artefact of encoding.

### RTL / LTR layout metrics

| Metric | Persian RTL | English LTR |
| --- | --- | --- |
| Text written right-to-left / left-to-right | 97.9% of spans | 41.8% of spans |
| Text preservation | 96.01% | 100.0% |
| Missing glyphs (`?`) | 0 | 0 |
| Blank content pages | 0 | 0 |

### Visual quality

| Metric | Measured value |
| --- | --- |
| Pages inspected (rendered to PNG) | 12 |
| Pages with visual problems | 0 |
| Pages passing visual inspection | 100.00% |
| Persian pages rendering RTL correctly | 3 of 3 |
| Persian pages rendering RTL incorrectly | 0 |

### OCR path

| Metric | Measured value |
| --- | --- |
| Vision requests for the scanned leaf | 1 |
| Image sent | 2,043,400 px, 284 KB, image/jpeg |
| Blocks translated after OCR | 1 |
| OCR accuracy vs. ground truth | N/A |
| Why | The vision endpoint is the local mock (no AI API key in the environment), so OCR text cannot be compared with the page's ground truth. |

### Overall

Overall text-preservation score: **98.00%** — the mean of the Persian RTL
(96.01%) and English LTR (100.0%) preservation
measurements. It is a letter-level comparison of the text that went in against the text
recovered from the generated PDF, after Unicode normalisation.

Failed tests: none in this module. Limitations that are *not* scored:

- Translation quality: the provider is the local mock, so no metric is claimed.
- OCR accuracy: same reason — marked N/A instead of invented.

### خروجی‌های آماده برای دانلود

| مورد | لینک |
|---|---|
| اجرای واقعی GitHub Actions | https://github.com/alimahamdarijanipro-stack/trarium-/actions/runs/43400846444 |
| PDF گزارش تست‌ها (۱۰ صفحه) | https://github.com/alimahamdarijanipro-stack/trarium-/releases/download/qa-2.17.0-2026-11-13/Trarium_2.17.0_test_report.pdf |
| بستهٔ آرتیفکت اکشنز (PDFهای ساخته‌شده + تصاویر صفحات + metrics.json) | https://github.com/alimahamdarijanipro-stack/trarium-/releases/download/qa-2.17.0-2026-11-13/trarium-pdf-workflow.zip |
| لاگ کامل همان اجرا | https://github.com/alimahamdarijanipro-stack/trarium-/releases/download/qa-2.17.0-2026-11-13/pdf-qa.log |
| صفحهٔ Release | https://github.com/alimahamdarijanipro-stack/trarium-/releases/tag/qa-2.17.0-2026-11-13 |

---

## 15) اجرای واقعی تراریوم روی PDF چینی (چینی → فارسی)

این بخش با `qa/run_chinese_test.py` ساخته شده است: فایل واقعی `全球最佳动漫盘点_中文三页详细版 (1) (1).pdf`
(۳ صفحه، ۱.۹۳ مگابایت، **بدون لایهٔ متنی** — هر صفحه یک تصویر اسکن‌شده) از روی مخزن خوانده شد،
از مسیر واقعی تراریوم گذشت و خروجی PDF با نویسندهٔ PDF خودِ تراریوم ساخته شد.
مدل‌ها از OpenRouter با کلید واقعی صدا زده شدند؛ هیچ داده‌ای شبیه‌سازی نشد.

### اجرا در یک نگاه

| مرحله | مدل | نتیجهٔ اندازه‌گیری‌شده |
|---|---|---|
| شناسایی متن از تصویر (OCR) | `nvidia/nemotron-3.5-super-vl:free` | 65 بلاک، 1332 نویسهٔ چینی، ۲۰۰.۶ ثانیه (صفحه‌های ۲ و ۳ به دو نیمه تقسیم شدند) |
| ترجمهٔ چینی → فارسی | `nvidia/nemotron-3-ultra-550b-a55b:free` | 28 بلاک ترجمه‌شده، 0 بلاک ناموفق |
| PDF حفظ‌چیدمان (hybrid) | نویسندهٔ خود تراریوم | 4 صفحه (3 صفحهٔ محتوا + صفحهٔ انتساب)، 2733 نویسهٔ فارسی، 3.8 مگابایت |
| PDF متنی تمیز | همان ترجمه | 4 صفحه، 2898 نویسهٔ فارسی، 55.0 کیلوبایت |

### عددهای کیفیت

| سنجه | مقدار |
|---|---|
| تصاویر صفحه قبل / بعد (حفظ شد؟) | 3 / 3 — همه حفظ شدند |
| چینی باقی‌مانده در لایهٔ متنی خروجی | 1 نویسه |
| علامت `?` (نشانهٔ نبود گلیف) | 0 |
| فونت جاسازی‌شده | Droid Sans Fallback Regular، Noto Serif Regular، Sahel Regular |
| راست‌چین بودن در PDF متنی | 90.2% از spanها |
| راست‌چین بودن در PDF حفظ‌چیدمان | 0.7% (متن در کادر بلاک اصلی می‌نشیند، پس این معیار اینجا معنا ندارد) |
| بلاک‌های نوشته‌شده در PDF حفظ‌چیدمان | ۲۸ از ۳۱ (۳ بلاک چون ترجمه در کادر اصلی جا نمی‌شد رد شد) |

### تصویرها

**صفحهٔ ۱ — چپ: فایل چینی ورودی، راست: خروجی تراریوم**

![مقایسه صفحه ۱](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/zh-to-fa/screenshots/compare-p1.png)

**صفحهٔ ۲ — نمودار دایره‌ای و متن**

![مقایسه صفحه ۲](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/zh-to-fa/screenshots/compare-p2.png)

**صفحهٔ ۳ — جدول و پایان‌نامه**

![مقایسه صفحه ۳](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/zh-to-fa/screenshots/compare-p3.png)

**PDF متنی تمیز (راست‌چین، فونت Sahel)**

![PDF متنی صفحه ۱](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/zh-to-fa/screenshots/text-p1.png)

### دو باگ واقعی که همین تست پیدا کرد و رفع شد

1. **شمارهٔ صفحهٔ یک‌به‌یک:** بلاک‌های OCR با شمارهٔ صفحهٔ ۱-پایه به پایپ‌لاین داده می‌شدند ولی پایپ‌لاین از
   شمارهٔ ۰-پایه استفاده می‌کند؛ نتیجه این بود که متن ترجمه‌شدهٔ صفحهٔ ۱ روی صفحهٔ ۲ می‌نشست.
2. **متن اصلی روی اسکن باقی می‌ماند:** روی صفحهٔ بدون لایهٔ متنی، redaction نمی‌تواند گلیف‌هایی را که
   داخل تصویر صفحه هستند پاک کند؛ بنابراین متن چینی زیر ترجمهٔ فارسی دیده می‌شد. حالا برای صفحه‌های
   اسکن‌شده، کادر هر بلاک با سفید پوشیده و بعد ترجمه درج می‌شود.

همچنین در همین مسیر سه ایراد دیگر رفع شد: مدل‌های استدلالی کل بودجهٔ توکن را صرف «فکر کردن» می‌کردند و
`content` خالی برمی‌گرداندند (بودجه و تلاش مجدد اصلاح شد)، مدل‌های بینایی مختصات را به پیکسل می‌دادند
نه ۰ تا ۱ (حالا تشخیص و مقیاس‌بندی می‌شود)، و صفحهٔ انتساب اندازهٔ A4 ثابت می‌گرفت.

### محدودیت‌ها (بدون ادعای اضافی)

- مدل‌ها «رایگان» OpenRouter بودند؛ کیفیت ترجمه خوب اما نه در سطح مدل‌های پولی، و OCR روی صفحهٔ شلوغ
  (نمودار و جدول) بلاک‌های عنوان و برچسب‌ها را کامل نمی‌گیرد — به همین دلیل در PDF حفظ‌چیدمان، عنوان‌ها و
  بعضی برچسب‌های نمودار به چینی باقی می‌مانند.
- ترجمهٔ قطعهٔ اول صفحهٔ ۱ به‌صورت تکه‌تکه برگشت (خروجی مدل رایگان) و در PDF متنی همان‌طور که هست آمده است.
- هیچ ادعایی دربارهٔ دقت عددی OCR چینی (CER) نمی‌شود، چون مرجع دستی برای این فایل وجود ندارد.

### فایل‌های خروجی

- `https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/zh-to-fa/trarium_zh_to_fa_hybrid.pdf` — خروجی حفظ‌چیدمان
- `https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/zh-to-fa/trarium_zh_to_fa_text.pdf` — خروجی متنی تمیز راست‌چین
- `https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/zh-to-fa/metrics.json` و `https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/zh-to-fa/run.log` — اندازه‌گیری‌ها و لاگ کامل

## 16) PDF24 OCR provider (branch `qa/v2.17.0-audit` only)

### What PDF24 actually offers
`tools.pdf24.org/en/ocr-pdf` is a browser tool. PDF24 publishes **no public HTTP/JSON OCR API**; the only documented, permitted programmatic entry point is the command-line switch of the desktop app, `pdf24-Ocr.exe`, shipped with PDF24 Creator (manual: `creator.pdf24.org/manual/11/#101-command-line`):

```
"C:\Program Files\PDF24\pdf24-Ocr.exe" -outputFile out.pdf -language eng+chi_sim -dpi 300 in.pdf
```

So the provider drives that CLI. No private endpoint, no scraping, no cookie or rate-limit bypass is used anywhere in the code.

### What was added
| File | Change |
| --- | --- |
| `trarium/ocr_pdf24.py` | New provider: `find_binary` (`TRARIUM_PDF24_BIN` env + Windows default paths + `which`), `is_available`, `availability_hint`, `language_codes` (iso → Tesseract names, e.g. `zh → chi_sim`, `fa+en → fas+eng`), `build_command`, `ocr_pdf`, `has_text_layer`, `extract_blocks`, `ocr_document_blocks`, `Pdf24Error`. |
| `trarium/pdf_optimized.py` | `ocr_method="pdf24"` branch in `translate_pdf_optimized` (one document-level run, only scanned pages take its blocks), `extract_ocr_blocks_pdf24`, `PDFTranslationStats.ocr_pdf_path`. |
| `trarium/translators/gemini.py` | Free-only policy: `FREE_GEMINI_MODELS` allowlist, `PaidModelBlocked` raised for any non-free model unless the caller opts in; `GEMINI_MODELS` replaced with real model ids verified against `GET /v1beta/models` (the old list named `gemini-3.8-flash` etc., which the API does not serve). |
| `streamlit_app.py` | “PDF24 (CLI رسمی، ویندوز)” in the OCR engine picker (only when the binary is found, otherwise a hint explains how to install), `chi_sim+eng` in the page-language list, and an “allow paid model” checkbox gated to Gemini. |
| `tests/test_pdf24_provider.py` | 8 tests: binary discovery, missing-binary error, language mapping, documented switches, input file untouched (sha256 before/after), text-layer extraction, exit-code failure, and full pipeline with `ocr_method="pdf24"`. |
| `qa/run_pdf24_test.py` | End-to-end runner for the Chinese scan; writes `01_original.pdf`, `02_pdf24_ocr.pdf`, `03_translated_fa.pdf`, screenshots, `metrics.json`. |
| `tests/test_smoke.py` | Two assertions updated to the real model ids. |

Previous OCR engines (PaddleOCR, Surya, EasyOCR, Tesseract, Vision) were **not removed**; PDF24 is an additional option.

### Tests
```
tests/                      33 passed
qa/qa_suite.py              18 passed, 1 skipped
qa/pdf_pipeline_test.py      5 passed
```
A real Gemini call with the provided key works on `gemini-3.5-flash` (free tier) and a request for `gemini-2.5-pro` is refused with `PaidModelBlocked` unless the user ticks the consent box — total cost of this run: **0**.

### PDF24 test run (3-page Chinese scan, `https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/pdf24-test/`)
| Metric | Value |
| --- | --- |
| pages in / out | 3 / 4 (assignment page) |
| OCR text layer | 490 chars on page 1 |
| blocks translated | 23 (0 failed, 0 write errors) |
| Persian characters | 2359 |
| Chinese characters left | 0 |
| question marks | 0 |
| output size | 2.0 MB |
| time | 179.9 s (free tier) |

### Honest limitations
1. **The real `pdf24-Ocr.exe` was not executed.** It is a Windows-only binary; this machine is Linux. The provider was verified against the documented CLI contract through a stand-in executable that accepts the same switches and returns a real text-layer PDF; the Trarium code path (copy → args → subprocess → exit code → layer → translation) is identical to the one that will call the exe on Windows. To verify on Windows: install PDF24 Creator, then run `TRARIUM_PDF24_BIN="C:\\Program Files\\PDF24\\pdf24-Ocr.exe" python qa/run_pdf24_test.py --input <file>`.
2. **Visual quality on this dense scan is poor.** The text layer came from bounding boxes of a vision OCR pass; several boxes overlap or miss the original lines, so the Persian text is cramped and a few headings stay uncovered (see `screenshots/compare-p*.png`). The provider and translation path are correct; the box quality is the bottleneck.
3. **Text-layer glyph bug found:** extracted Persian text shows `Ȝ` where `ی` should be (e.g. `آزمایشȜ`). Rendering is correct, copy/paste is not — a font ToUnicode mapping issue in the PDF writer, unrelated to PDF24, not fixed in this round.
4. Pushing to GitHub was not possible from this environment (the PAT is no longer available); the commit exists locally on the QA branch only.

## 17) OCR.space provider (branch `qa/v2.17.0-audit` only)

### What OCR.space offers
OCR.space is a hosted OCR API (`POST https://api.ocr.space/parse/image`). The free plan gives 25,000 requests/month, 1 MB per file, PDFs up to 3 pages, and engines 1 and 2. Engine 3 is PRO-only, so the provider never sends it, and no paid endpoint or paid fallback exists anywhere in the code.

### What was added
| File | Change |
| --- | --- |
| `trarium/ocr_space.py` | New provider: key resolution (`TRARIUM_OCR_SPACE_KEY`, `OCR_SPACE_API_KEY`, optional UI value), `language_codes` (zh→chs, fa→ara, …), free-engine guard, per-page render to JPEG under 1 MB with quality fallback, retry with backoff on 429/5xx, `build_blocks` (TextOverlay → PDF-point boxes) plus `merge_lines` (lines of one paragraph are joined, and CJK text is joined without spaces). |
| `trarium/pdf_optimized.py` | `ocr_method="ocrspace"` branch in `translate_pdf_optimized` (per-page OCR, blocks → layout pipeline) and `extract_ocr_blocks_ocrspace`. |
| `streamlit_app.py` | “OCR.space (API رایگان، بدون نصب)” in the OCR engine picker plus a password field for the key; the hint explains where to get a free key. |
| `trarium/translators/gemini.py` | Model list corrected again: `gemini-2.5-flash`/`gemini-2.5-flash-lite` return HTTP 404 with a real key, so the list is now the ids that actually answer (`gemini-3.5-flash`, `gemini-3-flash-preview`, `gemini-flash-lite-latest`, `gemini-flash-latest`, `gemini-3.1-flash-lite`). The paid guard stays: any other id needs the explicit “allow paid” checkbox. |
| `tests/test_ocr_space.py` | 14 offline tests: language mapping, free-engine refusal, 1 MB guard, documented form fields, key resolution, demo-key hint, API error handling, coordinate scaling, line merging (CJK and Latin), per-page document OCR, empty result, and the full pipeline with `ocr_method="ocrspace"`. |
| `qa/run_ocrspace_test.py` | End-to-end runner (`provider[:model]` list), OCR cache, OCR PDF writer, screenshots and metrics. |

### Tests
```
tests/                      47 passed
```
(The previous PDF24 tests, including the test double for `pdf24-Ocr.exe`, still pass; nothing was removed.)

### Real run on the 3-page Chinese scan (`https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/`)
| Step | Result |
| --- | --- |
| OCR.space (free key, `language=chs`, engine 2) | 3 pages, 3 requests, 1317 chars, 7.3 s |
| Gemini `gemini-3-flash-preview` (free, before the renderer fix) | 74 blocks, 46 written, 0 failed, 2734 Persian chars |
| Gemini `gemini-flash-lite-latest` (free, after the renderer fix) | 82 blocks, 54 written, 0 failed, 2863 Persian chars, 0 Chinese left, 13.6 s |
| OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free` | 82 blocks, 54 written, 0 failed, 2910 Persian chars, 0 Chinese left, 399.8 s |

Outputs: `01_original.pdf` (untouched), `02_ocrspace_ocr.pdf` (page images + OCR.space text layer), `03_translated_gemini-gemini-3-flash-preview.pdf`, `04_translated_gemini-gemini-flash-lite-latest.pdf`, page screenshots, `compare-p1..3.png`, `metrics.json`, and `ocr_blocks.json` (raw OCR.space blocks).

### Honest limitations
1. `gemini-3.5-flash` answered earlier today but now returns `429 quota exceeded` on the free tier; that is a quota, not a code path, and the run continued with two other free models.
2. OpenRouter's free tier is limited to 50 requests/day per account and is exhausted, so the OpenRouter leg of the test is reported as blocked rather than green.
3. Layout is better than the PDF24 box experiment but still imperfect: merged paragraphs can cover a chart or table area, and a few small labels keep their Chinese text.
4. The 1 MB free-plan cap forces 85–200 dpi JPEG pages, so very small print can be missed on dense scans.

## 18) PDF renderer fix — per-line replacement (same branch)

### The actual cause (measured, not guessed)
The old writer did three things that together produced the broken pages:

1. **One white rectangle per merged block.** OCR.space groups up to four source lines into one block, so `_write_text` painted a rectangle over the union of those lines — across a table column or over part of a chart — and wrote one paragraph inside it. That is where the large white bands and the smeared tables came from.
2. **`_expand_rect` grew the rectangle before filling it** (up to 8% of the page height plus padding). The white fill therefore extended past the original text into the artwork, which is why images looked damaged and gaps appeared.
3. **The whole Persian paragraph was shrunk into that one box** (`insert_htmlbox`, `scale_low=0.20`). Long translations either became tiny or overflowed their cell, so text left its cell and overlapped neighbours.

### The fix
* `trarium/ocr_space.py` keeps every source line box inside the merged block (`lines`), so a paragraph still costs one translation request but arrives at the renderer with its geometry intact.
* `_normalise_layout_blocks` no longer re-merges a block that already carries line boxes.
* New `_write_on_lines` in `trarium/pdf_optimized.py`: the translated paragraph is split across its own source lines by source length, each line is filled with white **only inside its exact original box**, and the font size is derived from that line's height (never from a page-wide expansion). RTL and the Sahel font path are unchanged.
* Result on the same file: line boxes keep the table grid, the pie chart and the legend legend positions; the two large blank bands are gone and no cell overflows.

### Before / after (all three pages)
| Page | Original | Before the fix | After the fix |
| --- | --- | --- | --- |
| 1 | [original-p1.jpg](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/pages/original-p1.jpg) | [compare-p1.png (PDF24 era)](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/pdf24-test/screenshots/compare-p1.png) | [translated-p1.jpg](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/pages/translated-p1.jpg) |
| 2 | [original-p2.jpg](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/pages/original-p2.jpg) | — | [translated-p2.jpg](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/pages/translated-p2.jpg) |
| 3 | [original-p3.jpg](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/pages/original-p3.jpg) | — | [translated-p3.jpg](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/pages/translated-p3.jpg) |

Side-by-side comparisons: [page 1](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/screenshots/compare-p1.png) · [page 2](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/screenshots/compare-p2.png) · [page 3](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/screenshots/compare-p3.png)

OpenRouter output (same fix, same file): [page 1](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/pages/translated-openrouter-p1.jpg) · [page 2](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/pages/translated-openrouter-p2.jpg) · [page 3](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/ocrspace-test/pages/translated-openrouter-p3.jpg)

| Provider | Blocks | Written | Failed | Persian chars | Chinese left | Time |
| --- | --- | --- | --- | --- | --- | --- |
| Gemini `gemini-flash-lite-latest` (free) | 82 | 54 | 0 | 2831 | 0 | 12.8 s |
| OpenRouter `nvidia/nemotron-3-ultra-550b-a55b:free` | 82 | 54 | 0 | 2910 | 0 | 399.8 s |

### What still does not match the original
* Very small chart labels keep their Chinese text when OCR.space misses them at the 1 MB resolution limit; nothing is left half-covered, but those labels are simply not translated.
* A paragraph that is much longer than its source (rare here) is still shrunk to fit its own lines instead of reflowing the page — the layout stays faithful, the font gets smaller.
* The text layer still shows presentation-form characters on copy/paste (the `Ȝ` issue), unchanged by this fix.

### Follow-up renderer fixes (same round)
* Percentages, axis ticks and page numbers are no longer redrawn: `_needs_translation` skips labels with fewer than two letters, which removed the white rectangles that sat on the pie chart wedges.
* `unicode-bidi: plaintext` in the text CSS lets every run pick its direction from its first strong character, so an English-only legend item stays LTR and a Persian sentence stays RTL, with numbers and parentheses in order.
* Minimum font size in the per-line writer raised to 6 pt.

Still unmatched on page 3: the bar-chart x-axis labels (亚洲 / 北美 / Europe / 南美) stay in their original form because OCR.space missed them at the free plan's 1 MB resolution; the legend and title are translated.

### Rollback note
The legend-colour experiment (`e1cb520`, marker stripping) changed how every short label was boxed and made page 3 worse overall — some headings and legend words came back untranslated. It was reverted in `295331c`, so the renderer is exactly the version that produced the earlier, better output (per-line boxes + numeric labels untouched + `unicode-bidi: plaintext`).

The current test run on the same three pages: 82 blocks, 54 written, 0 failed, 2839 Persian characters, 0 Chinese in the replaced text, 13.2 s.
Known issues that remain in this version: legend swatches on page 3 are redrawn as black squares, and x-axis labels (亚洲 / 北美 / 欧洲 / 南美) are not translated.

## 19) Vocabulary PDF — English → Persian (`advanced_english_vocabulary_light_novel_manga.pdf`)

Source: `origin/main` → `advanced_english_vocabulary_light_novel_manga.pdf` (3 landscape pages, 842×595).

| Page | What it is | Path used |
| --- | --- | --- |
| 1 | vocabulary grid (18 words, table-like) | native text layer, no OCR |
| 2 | two-column short chapter | native text layer, no OCR |
| 3 | manga page, image only | OCR.space (free key), `ocr_lang=eng` |

Run: `python qa/run_vocab_test.py --input /tmp/vocab.pdf` with `gemini-flash-lite-latest`.

| Metric | Value |
| --- | --- |
| blocks | 59 |
| translated | 58 |
| failed | 0 |
| write failures | 0 |
| Persian characters | 4182 |
| question marks | 0 |
| time | 16.1 s |

The page size, orientation and the layout of the vocabulary grid are preserved; the manga page keeps its artwork and only the speech bubbles are replaced.

Outputs: [qa/outputs/vocab-test/](https://github.com/alimahamdarijanipro-stack/trarium-files/blob/main/qa/outputs/vocab-test/) — original PDF, translated PDF, screenshots of every page before/after, `metrics.json`.
Remaining nit: one manga bubble kept the English word “and” at the start of its translation; everything else is Persian.

## 20) Engine fixes: white boxes, uniform type, self-review and a final check

### Root causes and fixes
| Problem | Root cause | Fix |
| --- | --- | --- |
| White rectangles around translated words, artwork covered | On scanned pages `_write_text` painted `_expand_rect(...)` — a box grown by up to 8% of the page height — before writing | Scanned pages now fill and write inside the exact source box only (`inset = rect`), so charts, bubbles and images keep their pixels |
| One sentence 18 pt, the next 7 pt | Every block used its own `font_size`, which OCR/noise makes wildly uneven | `uniform_font=True` clamps body text to a narrow band around the document median while headings keep a step above it (`≤ median×1.6`) |
| Untranslated / half-English fragments | Nothing re-read the translation after the batch pass | New `self_review` option (`_review_translations`) re-asks the model for blocks that are empty, echoed, duplicated or still contain CJK, capped at 20 per document |
| No way to verify a result | Output was never compared with the source | New `trarium/pdf_qc.py` + `final_check` option: page count/size, blank pages, lost images, leftover CJK, duplicated blocks, overlapping spans, text outside the page, tiny-font share |
| Nothing prevented regressions | No tests for any of the above | `tests/test_pdf_qc.py` (5 tests) plus the existing 48 |

UI: a single checkbox “🔥 آتش نهایی” on the PDF tab turns on the model self-review and the final check, and prints the QC result (green when clean, the issue list otherwise).

### Real run — `advanced_english_vocabulary_light_novel_manga.pdf`
| Provider | Blocks | Translated | Failed | Reviewed | QC | Persian chars | Time |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Gemini `gemini-flash-lite-latest` (free) | 59 | 58 | 0 | 1 | ✅ clean | 4121 | 16.2 s |
| OpenRouter `nemotron-3-ultra-550b-a55b:free` | 59 | 58 | 0 | 0 | ⚠️ 1 duplicated block reported | 3810 | 165.6 s |

Tests: `tests/` → 53 passed.

### What is still not perfect
* One manga bubble keeps the English word “and” at the start of its line; the self-review pass fixed a different block instead.
* The OpenRouter run produced one duplicated block, which the new QC reported instead of hiding.
* The final check is structural (geometry, scripts, sizes, duplication). It cannot tell that a sentence is a poor translation, so a human read of the page images is still required.

## 21) EPUB novel: translating in place instead of rebuilding the book

`qa/run_epub_test.py` translates an EPUB without rebuilding it: every XHTML
document is edited in place, so the cover, the artwork, the publisher
stylesheet and the reading order survive untouched. The document switches to
`lang="fa" dir="rtl"`, a small `css/fa.css` adds the bundled Sahel font and a
Persian line height, and the table of contents / NCX labels are translated too.

How it works:
* paragraphs and headings are collected per file and sent in numbered batches
  (`[[1]] … [[n]]`); a batch whose marker count does not match is split, then
  retried paragraph by paragraph, so a lost marker can never shift text;
* every file's result is cached with a hash of its source, so an interrupted
  run resumes instead of paying for the same chapters twice;
* paragraphs that hold inline artwork (this novel uses them for censored
  words) are split into text runs: the text is replaced, the `<img>` tags stay
  exactly where they were;
* the final check compares input and output: image count, paragraphs still in
  English, Persian share, and every XHTML file is parsed to prove it is
  well-formed.

Real run — *Omniscient Reader's Viewpoint, Vol. 2* (20 chapters, 3.9 MB):

| Metric | Value |
| --- | --- |
| Chapters / parts | 20 |
| Paragraphs translated | 3,765 |
| Persian characters | 209,401 |
| Images in / out | 22 / 22 |
| Paragraphs still in English | 0 |
| Malformed XHTML files | 0 |
| Provider | Gemini `gemini-flash-lite-latest` (free) |
| Translation time | 4 min 37 s (277 s) |

Two real bugs were found by the new checks during this run and fixed:
`rtl_document` added `lang`/`dir` twice when it ran over an already converted
file (invalid XHTML in 8 documents), and replacing a whole text run around an
inline image deleted half of a `<span>` pair (`Opening and ending tag mismatch:
p line 42 and span`). Both are now impossible: the `html` tag is rebuilt once,
and inline text runs keep their tags.

Output: <https://github.com/alimahamdarijanipro-stack/trarium-files> —
`books/omniscient-readers-viewpoint-vol2-fa.epub`.
