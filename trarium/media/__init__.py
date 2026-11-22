"""دوبله و زیرنویس‌ساز تراریوم (ویدیو/صدا/یوتیوب).

ماژول‌ها:
    langs          زبان‌ها، کد BCP-47 و صدای edge-tts
    timeline       بخش‌های زمان‌دار، SRT/VTT، برنامهٔ جاگذاری دوبله (خالص)
    ass            ساخت ASS برای زیرنویس حک‌شده (فونت فارسی، راست‌به‌چپ)
    mix            ترک دوبله و میکس با صدای اصلی (numpy)
    ffmpeg_tools   ffmpeg/ffprobe
    source         yt-dlp یا فایل آپلودی
    gemini_client  REST جمینای با چند کلید و جابه‌جایی روی 429/سهمیه
    transcribe     Gemini → faster-whisper
    translate      ترجمهٔ بخش‌ها با ترجمه‌گرهای خود تراریوم
    tts            Gemini TTS → edge-tts
    live           Gemini Live Translate (WebSocket)
    pipelines      مسیرهای کامل زیرنویس، دوبلهٔ عادی و دوبلهٔ زنده
    ui             تب‌های Streamlit «دوبله» و «زیرنویس»
"""
