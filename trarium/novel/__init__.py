"""
🌿 تراریوم — ماژول وب‌نول
"""
from trarium.novel.models import Chapter, Novel
from trarium.novel.scraper import (
    NovelScraper, ScrapeResult, ImageInfo,
    GenericAdapter, WordPressAdapter, SyosetuAdapter,
    WuxiaAdapter, NovelbinAdapter, RoyalRoadAdapter,
    GenericManualAdapter, SiteDetector, clean_novel_text,
)
from trarium.novel.cleaner import detect_language, split_into_paragraphs

__all__ = [
    "Chapter", "Novel",
    "NovelScraper", "ScrapeResult", "ImageInfo",
    "GenericAdapter", "WordPressAdapter", "SyosetuAdapter",
    "WuxiaAdapter", "NovelbinAdapter", "RoyalRoadAdapter",
    "GenericManualAdapter", "SiteDetector",
    "clean_novel_text", "detect_language", "split_into_paragraphs",
]
