> Python 3.10 یا بالاتر لازم است.

## ✨ چه چیزی در نسخهٔ 2.16.0 اصلاح شد؟

- خروجی **PDF فارسی** حالا با فونت Sahel و چیدمان راست‌به‌چپ نوشته می‌شود (قبلاً `?????` بود).
- در حالت **حفظ قالب**، متن اصلی حذف می‌شود ولی عکس‌ها و صفحه دست‌نخورده می‌مانند.
- **EPUB** دیگر کرش نمی‌کند و فونت داخل فایل به درستی بارگذاری می‌شود.
- **OCR** با تصویر سبک‌تر (JPEG، ۱۸۰dpi) اجرا می‌شود؛ هزینهٔ توکن بیش از ۷۰٪ کمتر شده است.
- تقسیم متن دیگر کلمه‌ها را نصف نمی‌کند و فصل‌های کوتاه در یک درخواست جمع می‌شوند.

جزئیات کامل در [CHANGELOG.md](CHANGELOG.md). نصب سبک برنامه:

```bash
pip install -r requirements.txt          # هستهٔ برنامه
pip install -r requirements-ocr.txt      # فقط اگر OCR محلی سنگین می‌خواهید
```

آزمون‌ها: `python -m pytest tests/ -v`


# 🌿 تراریوم (Trarium)

**ترجمه خودکار کتاب با هوش مصنوعی**

ابزاری ساده و سریع برای ترجمه کتاب‌ها (PDF, EPUB) با استفاده از OpenRouter — **کاملاً رایگان**.

---

## ✨ ویژگی‌ها

- 📖 پشتیبانی از PDF و EPUB
- 🌍 ترجمه به ۱۱+ زبان (فارسی، انگلیسی، عربی، فرانسوی، ...)
- ⚡ سریع: کتاب ۵۰۰ صفحه‌ای در ~۱۵ دقیقه
- 🆓 کاملاً رایگان با OpenRouter
- 📄 خروجی TXT یا PDF
- 🔧 OCR Vision برای PDF اسکن‌شده (بدون نیاز به Tesseract)
- 🎯 انتخاب صفحات/فصل‌ها برای رد کردن (صرفه‌جویی توکن)
- 📊 نمایش پیشرفت و آمار
- 🖥️ رابط وب زیبا با Streamlit

## 🚀 نصب

```bash
# کلون کنید
cd trarium

# نصب با pip
pip install -e .

# یا فقط وابستگی‌ها
pip install -r requirements.txt
```

## 🔑 تنظیم API Key

1. به [OpenRouter](https://openrouter.ai/keys) برید
2. یک API Key رایگان بسازید
3. تنظیم کنید:

```bash
export OPENROUTER_API_KEY="your-api-key-here"
```

## 🖥️ رابط وب (Streamlit)

ساده‌ترین راه استفاده — فقط یه دستور:

```bash
streamlit run streamlit_app.py
```

مرورگر باز میشه روی `http://localhost:8501` و می‌تونید:

- فایل PDF/EPUB رو آپلود کنید
- زبان مقصد رو انتخاب کنید
- API Key رو وارد کنید
- دکمه ترجمه رو بزنید
- خروجی رو دانلود کنید

## 📖 استفاده از خط فرمان (CLI)

```bash
# ساده
trarium book.epub --to fa
trarium book.pdf --to en

# با فایل خروجی دلخواه
trarium book.epub --to fa --output translated.txt

# OCR برای PDF اسکن‌شده
trarium scanned.pdf --ocr --to fa

# رد کردن صفحات خاص (صرفه‌جویی توکن)
trarium book.pdf --to fa --skip-pages "1,5,10-15"

# کمک
trarium --help
```

## 📊 حساب توکن

| اندازه کتاب | توکن تقریبی | زمان تقریبی | هزینه |
|---|---|---|---|
| ۲۰۰ صفحه | ~۱۷۵,۰۰۰ | ۵-۱۰ دقیقه | رایگان |
| ۵۰۰ صفحه | ~۴۴۰,۰۰۰ | ۱۵-۲۰ دقیقه | رایگان |

## 🗂️ ساختار پروژه

```
trarium/
├── trarium/
│   ├── __init__.py
│   ├── cli.py              # CLI اصلی
│   ├── extractors/
│   │   ├── base.py         # کلاس پایه
│   │   ├── pdf.py          # استخراج PDF
│   │   ├── epub.py         # استخراج EPUB
│   │   └── ocr.py          # OCR برای اسکن
│   ├── translators/
│   │   ├── base.py         # کلاس پایه
│   │   └── gemini.py       # Gemini Flash
│   ├── processors/
│   │   ├── __init__.py     # تقسیم‌کننده متن
│   │   └── pipeline.py     # پایپلاین اصلی
│   └── output/
│       └── __init__.py     # ذخیره خروجی
├── main.py                 # نقطه ورود CLI
├── streamlit_app.py        # رابط وب Streamlit
├── pyproject.toml
├── requirements.txt
└── README.md
```

---

ساخته شده با 🌿 توسط تراریوم
