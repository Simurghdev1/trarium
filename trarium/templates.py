"""Project templates (پروفایل‌های آماده) برای مانگا، وب‌ناول و کتاب.

هر قالب فقط یک دیکشنری تنظیمات است که روی تنظیمات فعلی پروژه اعمال می‌شود؛
هیچ مسیر ترجمه‌ای را دور نمی‌زند و هیچ درخواست API‌ای نمی‌سازد. قالب‌های
اختصاصی در همان ``settings.json`` پروژه ذخیره می‌شوند تا با ری‌استارت از بین
نروند.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from trarium.local_state import load_settings, save_settings

_BUILTIN: dict[str, dict] = {
    "manga": {
        "name": "Manga",
        "description": "مانگای ژاپنی؛ متن کوتاه هر حباب، OCR ژاپنی و خروجی PDF.",
        "settings": {
            "chunk_size": 6000, "workers": 2, "delay": 0.2,
            "ocr_lang": "jpn", "ocr_method": "auto", "enable_ocr": True,
            "output_format": "pdf", "protect_spans": True, "safe_optimization": True,
            "short_prompt": "متن داخل حباب‌های مانگا را کوتاه، محاوره‌ای و با حفظ لحن شخصیت ترجمه کن.",
        },
    },
    "manhwa": {
        "name": "Manhwa",
        "description": "مانهوای کره‌ای؛ OCR کره‌ای، جمله‌های محاوره‌ای و خروجی PDF.",
        "settings": {
            "chunk_size": 6000, "workers": 2, "delay": 0.2,
            "ocr_lang": "kor", "ocr_method": "auto", "enable_ocr": True,
            "output_format": "pdf", "protect_spans": True, "safe_optimization": True,
            "short_prompt": "دیالوگ‌های مانهوا را محاوره‌ای، روان و بدون خلاصه‌سازی ترجمه کن.",
        },
    },
    "manhua": {
        "name": "Manhua",
        "description": "مانهوای چینی؛ OCR چینی، اصطلاحات رزمی و خروجی PDF.",
        "settings": {
            "chunk_size": 6000, "workers": 2, "delay": 0.2,
            "ocr_lang": "chi_sim", "ocr_method": "auto", "enable_ocr": True,
            "output_format": "pdf", "protect_spans": True, "safe_optimization": True,
            "short_prompt": "اصطلاحات رزمی و نام‌های چینی را یکدست و وفادار ترجمه کن.",
        },
    },
    "web-novel": {
        "name": "Web Novel",
        "description": "وب‌ناول با فصل‌های زیاد؛ دستهٔ بزرگ‌تر، هم‌زمانی بیشتر و خروجی EPUB.",
        "settings": {
            "chunk_size": 14000, "workers": 4, "delay": 0.0,
            "output_format": "epub", "use_checkpoint": True, "adaptive_batch": True,
            "short_prompt": "روایت وب‌ناول را روان و سریع ترجمه کن؛ پاراگراف‌ها را نشکن.",
        },
    },
    "light-novel": {
        "name": "Light Novel",
        "description": "لایت‌نوول ژاپنی؛ حفظ لحن، نام‌های ژاپنی و خروجی EPUB.",
        "settings": {
            "chunk_size": 12000, "workers": 3, "delay": 0.0,
            "output_format": "epub", "use_checkpoint": True,
            "short_prompt": "نام‌های ژاپنی را همان‌طور که هستند بنویس و لحن راوی را حفظ کن.",
        },
    },
    "academic": {
        "name": "Academic Book",
        "description": "کتاب علمی؛ جمله‌بندی رسمی، پانویس‌ها و اصطلاحات تخصصی یکدست.",
        "settings": {
            "chunk_size": 10000, "workers": 2, "delay": 0.0,
            "output_format": "pdf", "protect_spans": True, "safe_optimization": True,
            "short_prompt": "لحن علمی و رسمی را حفظ کن؛ اصطلاحات تخصصی را یکدست ترجمه کن و پانویس‌ها را نگه دار.",
        },
    },
    "technical": {
        "name": "Technical Document",
        "description": "سند فنی؛ کد و دستورها دست‌نخورده می‌مانند و خروجی HTML یا PDF.",
        "settings": {
            "chunk_size": 12000, "workers": 2, "delay": 0.0,
            "output_format": "html", "protect_spans": True, "safe_optimization": True,
            "short_prompt": "نام دستورها، کد، مسیرها و شناسه‌ها را ترجمه نکن؛ فقط توضیح‌ها را ترجمه کن.",
        },
    },
}

# کلیدهایی که «اعمال قالب» اجازه دارد تغییر دهد.
TEMPLATE_KEYS = {
    "chunk_size", "workers", "delay", "ocr_lang", "ocr_method", "enable_ocr",
    "output_format", "protect_spans", "safe_optimization", "use_checkpoint",
    "adaptive_batch", "use_cache", "short_prompt", "template",
}


@dataclass
class ProjectTemplate:
    key: str
    name: str
    description: str
    settings: dict = field(default_factory=dict)
    builtin: bool = True

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "description": self.description,
                "settings": dict(self.settings), "builtin": self.builtin}


def _custom_bucket() -> dict:
    data = load_settings()
    bucket = data.get("project_templates")
    return bucket if isinstance(bucket, dict) else {}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9\-]+", "-", (value or "").strip().lower()).strip("-")
    return slug[:40] or "profile"


def list_templates() -> list[ProjectTemplate]:
    templates = [ProjectTemplate(key, item["name"], item.get("description", ""),
                                 dict(item.get("settings") or {}), True)
                 for key, item in _BUILTIN.items()]
    for key, item in _custom_bucket().items():
        if isinstance(item, dict):
            templates.append(ProjectTemplate(str(key), str(item.get("name") or key),
                                             str(item.get("description") or ""),
                                             dict(item.get("settings") or {}), False))
    return templates


def get_template(key: str) -> ProjectTemplate | None:
    for template in list_templates():
        if template.key == key:
            return template
    return None


def save_custom(name: str, settings: dict, description: str = "") -> ProjectTemplate:
    name = (name or "").strip()
    if not name:
        raise ValueError("نام قالب خالی است.")
    key = "custom-" + _slug(name)
    data = load_settings()
    bucket = data.setdefault("project_templates", {})
    if not isinstance(bucket, dict):
        bucket = {}
        data["project_templates"] = bucket
    bucket[key] = {
        "name": name,
        "description": description or "قالب اختصاصی ساخته‌شده توسط کاربر",
        "settings": {k: v for k, v in (settings or {}).items() if k in TEMPLATE_KEYS},
    }
    save_settings(data)
    return ProjectTemplate(key, name, description, dict(bucket[key]["settings"]), False)


def delete_custom(key: str) -> bool:
    data = load_settings()
    bucket = data.get("project_templates")
    if not isinstance(bucket, dict) or key not in bucket:
        return False
    bucket.pop(key, None)
    save_settings(data)
    return True


def apply_template(key: str, base: dict | None = None) -> dict:
    """تنظیمات قالب را روی یک کپی از تنظیمات فعلی می‌نشاند."""
    template = get_template(key)
    result = dict(base or {})
    if not template:
        return result
    for name, value in template.settings.items():
        if name in TEMPLATE_KEYS:
            result[name] = value
    result["template"] = template.key
    return result


def resolve(key: str | None, base: dict | None = None) -> dict:
    """اگر قالب نامعتبر بود، تنظیمات پایه دست‌نخورده برمی‌گردد."""
    return apply_template(key, base) if key else dict(base or {})
