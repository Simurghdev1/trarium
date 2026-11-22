"""زبان‌های تب دوبله و زیرنویس‌ساز.

هر زبان: برچسب نمایشی، کد BCP-47 (برای Live Translate و Gemini)، نام انگلیسی
(برای پرامپت) و صدای edge-tts زن/مرد. زبان آزاد هم پذیرفته می‌شود؛ آن وقت
کد همان متن کاربر است و صدای edge-tts از روی کد حدس زده می‌شود.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    label: str
    code: str
    english: str
    female: str = ""
    male: str = ""
    rtl: bool = False


LANGUAGES: list[Language] = [
    Language("فارسی", "fa", "Persian", "fa-IR-DilaraNeural", "fa-IR-FaridNeural", True),
    Language("English", "en", "English", "en-US-AriaNeural", "en-US-GuyNeural"),
    Language("العربية", "ar", "Arabic", "ar-SA-ZariyahNeural", "ar-SA-HamedNeural", True),
    Language("Türkçe", "tr", "Turkish", "tr-TR-EmelNeural", "tr-TR-AhmetNeural"),
    Language("اردو", "ur", "Urdu", "ur-PK-UzmaNeural", "ur-PK-AsadNeural", True),
    Language("עברית", "he", "Hebrew", "he-IL-HilaNeural", "he-IL-AvriNeural", True),
    Language("Français", "fr", "French", "fr-FR-DeniseNeural", "fr-FR-HenriNeural"),
    Language("Deutsch", "de", "German", "de-DE-KatjaNeural", "de-DE-ConradNeural"),
    Language("Español", "es", "Spanish", "es-ES-ElviraNeural", "es-ES-AlvaroNeural"),
    Language("Italiano", "it", "Italian", "it-IT-ElsaNeural", "it-IT-DiegoNeural"),
    Language("Português (BR)", "pt-BR", "Brazilian Portuguese", "pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"),
    Language("Русский", "ru", "Russian", "ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"),
    Language("Українська", "uk", "Ukrainian", "uk-UA-PolinaNeural", "uk-UA-OstapNeural"),
    Language("中文 (简体)", "zh-Hans", "Simplified Chinese", "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"),
    Language("日本語", "ja", "Japanese", "ja-JP-NanamiNeural", "ja-JP-KeitaNeural"),
    Language("한국어", "ko", "Korean", "ko-KR-SunHiNeural", "ko-KR-InJoonNeural"),
    Language("हिन्दी", "hi", "Hindi", "hi-IN-SwaraNeural", "hi-IN-MadhurNeural"),
    Language("Bahasa Indonesia", "id", "Indonesian", "id-ID-GadisNeural", "id-ID-ArdiNeural"),
    Language("Azərbaycan", "az", "Azerbaijani", "az-AZ-BanuNeural", "az-AZ-BabekNeural"),
    Language("Kurdî", "ku", "Kurdish", "", ""),
    Language("پښتو", "ps", "Pashto", "ps-AF-LatifaNeural", "ps-AF-GulNawazNeural", True),
]

_BY_CODE = {lang.code.lower(): lang for lang in LANGUAGES}
_BY_LABEL = {lang.label.lower(): lang for lang in LANGUAGES}
_BY_ENGLISH = {lang.english.lower(): lang for lang in LANGUAGES}
RTL_CODES = {"fa", "ar", "he", "ur", "ps", "ug", "sd", "yi", "dv", "ckb"}


def resolve(value: str) -> Language:
    """برچسب، کد یا نام انگلیسی → Language. زبان ناشناخته هم برگردانده می‌شود."""
    text = (value or "").strip()
    if not text:
        return _BY_CODE["fa"]
    key = text.lower()
    for table in (_BY_CODE, _BY_LABEL, _BY_ENGLISH):
        if key in table:
            return table[key]
    base = key.split("-")[0]
    if base in _BY_CODE:
        return _BY_CODE[base]
    return Language(text, text, text, rtl=base in RTL_CODES)


def is_rtl(value: str) -> bool:
    lang = resolve(value)
    return lang.rtl or lang.code.split("-")[0].lower() in RTL_CODES


def edge_voice(value: str, gender: str = "female") -> str:
    lang = resolve(value)
    voice = lang.male if gender == "male" else lang.female
    return voice or lang.female or lang.male or ""
