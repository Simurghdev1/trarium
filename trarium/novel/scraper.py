"""
🌿 تراریوم — اسکرپر وب‌نول (بهبود یافته)
پشتیبانی از استخراج عکس + تشخیص خودکار سایت
"""
import re
import time
import io
import os
import sys
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse

# bs4 — install if missing
try:
    from bs4 import BeautifulSoup
except ImportError:
    print("⚠️ beautifulsoup4 not installed. Run: pip install beautifulsoup4 lxml")
    BeautifulSoup = None

from rich.console import Console

console = Console()

# ── مدل‌های داده ──────────────────────────────────────────

@dataclass
class ImageInfo:
    """اطلاعات یک عکس."""
    url: str
    alt: str = ""
    width: int = 0
    height: int = 0
    data: bytes = None  # داده دانلود شده

    @property
    def filename(self) -> str:
        """نام فایل از هش URL."""
        ext = ".jpg"
        if ".png" in self.url.lower():
            ext = ".png"
        elif ".gif" in self.url.lower():
            ext = ".gif"
        elif ".webp" in self.url.lower():
            ext = ".webp"
        h = hashlib.md5(self.url.encode()).hexdigest()[:8]
        return f"img_{h}{ext}"


@dataclass
class ScrapeResult:
    """نتیجه اسکرپ یک فصل."""
    number: int = 0
    title: str = ""
    text: str = ""
    url: str = ""
    next_url: str = ""
    images: list = field(default_factory=list)  # لیست ImageInfo
    site_type: str = "generic"  # wordpress, novelbin, syosetu, etc.

    @property
    def word_count(self) -> int:
        return len(self.text.split()) if self.text else 0

    @property
    def char_count(self) -> int:
        return len(self.text) if self.text else 0


# ── تشخیص نوع سایت ──────────────────────────────────────

class SiteDetector:
    """تشخیص خودکار نوع سایت وب‌نول."""

    # الگوهای WordPress
    WORDPRESS_PATTERNS = [
        r"wp-content",
        r"wordpress",
        r"entry-content",
        r"post-content",
    ]

    # الگوهای سایت‌های خاص
    SITE_PATTERNS = {
        "novelbin": [r"novelbin", r"novel-bin"],
        "wuxia": [r"wuxiaworld", r"wuxia"],
        "lightnovel": [r"lightnovelworld", r"lightnovel"],
        "syosetu": [r"syosetu", r"ncode"],
        "webnovel": [r"webnovel\.com"],
        "tapas": [r"tapas\.io"],
        "royalroad": [r"royalroad\.com"],
        "witchcult": [r"witchculttranslation"],
    }

    @classmethod
    def detect(cls, html: str, url: str) -> str:
        """تشخیص نوع سایت."""
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()

        # بررسی دامنه
        for site_type, patterns in cls.SITE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, domain, re.IGNORECASE):
                    return site_type

        # بررسی HTML برای WordPress
        for pattern in cls.WORDPRESS_PATTERNS:
            if re.search(pattern, html[:10000], re.IGNORECASE):
                return "wordpress"

        return "generic"


# ── انتخابگر HTML ────────────────────────────────────────

def _select_all(html: str, selector: str) -> list[str]:
    """انتخاب المان‌ها با CSS selector."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        return [el.get_text(separator="\n", strip=True) for el in soup.select(selector)]
    except ImportError:
        raise RuntimeError("pip install beautifulsoup4 lxml")


def _select_one(html: str, selector: str) -> str | None:
    """انتخاب یک المان با CSS selector."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one(selector)
        return el.get_text(separator="\n", strip=True) if el else None
    except ImportError:
        raise RuntimeError("pip install beautifulsoup4 lxml")


def _split_selectors(selector_string: str) -> list[str]:
    """CSS selector list را مستقل از فاصله بعد از comma جدا می‌کند."""
    return [part.strip() for part in re.split(r",\s*", selector_string or "") if part.strip()]


def _select_attr(html: str, selector: str, attr: str) -> str | None:
    """انتخاب attribute از یک المان."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        el = soup.select_one(selector)
        return el.get(attr) if el else None
    except ImportError:
        raise RuntimeError("pip install beautifulsoup4 lxml")


def _find_next_link(html: str, current_url: str) -> str | None:
    """پیدا کردن لینک فصل بعدی."""
    try:
        from urllib.parse import urljoin
    except ImportError:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # روش ۱: جستجو بر اساس متن
    next_patterns = [
        r"next\s*chapter",
        r"forward",
        r"next",
        r"adam\s*forwarding",
        r"adana",
    ]
    for a_tag in soup.find_all("a", href=True):
        text = a_tag.get_text(strip=True).lower()
        href = a_tag["href"]
        for pattern in next_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return urljoin(current_url, href)
            if re.search(pattern, href, re.IGNORECASE):
                return urljoin(current_url, href)

    # روش ۲: لینک بعدی با rel
    next_link = soup.find("a", rel="next")
    if next_link and next_link.get("href"):
        return urljoin(current_url, next_link["href"])

    return None


# ── استخراج عکس ──────────────────────────────────────────

def _extract_images_from_content(content_element, base_url: str) -> list[ImageInfo]:
    """استخراج عکس‌ها از داخل content area."""
    images = []
    if not content_element:
        return images

    for img in content_element.find_all("img"):
        src = img.get("src", "")
        if not src:
            continue

        # تبدیل به URL مطلق
        full_url = urljoin(base_url, src)

        # فیلتر کردن عکس‌های تبلیغاتی و کوچک
        if _is_ad_image(img, full_url):
            continue

        alt = img.get("alt", "")
        try:
            width = int(str(img.get("width", 0) or 0).strip())
        except (TypeError, ValueError):
            width = 0
        try:
            height = int(str(img.get("height", 0) or 0).strip())
        except (TypeError, ValueError):
            height = 0

        images.append(ImageInfo(
            url=full_url,
            alt=alt,
            width=width,
            height=height,
        ))

    return images


def _is_ad_image(img_element, url: str) -> bool:
    """تشخیص عکس تبلیغاتی."""
    url_lower = url.lower()

    # الگوهای تبلیغاتی
    ad_patterns = [
        r"ad[s]?[-_]",
        r"banner",
        r"sponsor",
        r"affiliate",
        r"tracking",
        r"pixel",
        r"analytics",
        r"1x1\.",
        r"spacer",
        r"blank\.",
        r"transparent",
        r"\.svg$",  # آیکون‌ها معمولاً SVG هستن
    ]

    for pattern in ad_patterns:
        if re.search(pattern, url_lower):
            return True

    # بررسی سایز کوچک (آیکون‌ها)
    try:
        width = int(str(img_element.get("width", 100) or 100).strip())
    except (TypeError, ValueError):
        width = 100
    try:
        height = int(str(img_element.get("height", 100) or 100).strip())
    except (TypeError, ValueError):
        height = 100
    if width < 50 or height < 50:
        return True

    return False


def _download_image(url: str, timeout: int = 15) -> bytes | None:
    """دانلود یک عکس."""
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type.startswith("image/"):
            return resp.content
        # بعضی سایت‌ها Content-Type را درست نمی‌فرستند؛ فقط امضای فایل‌های تصویر را قبول کن.
        signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")
        if any(resp.content.startswith(sig) for sig in signatures):
            return resp.content
    except Exception:
        pass
    return None


# ── پاک‌سازی متن ────────────────────────────────────────

from trarium.novel.cleaner import clean_novel_text  # noqa: F401 — re-export


# ── آداپتورهای سایت ────────────────────────────────────

class GenericAdapter:
    """آداپتور عمومی — برای اکثر سایت‌های وب‌نول."""

    # CSS selectors پیش‌فرض — قابل سفارشی‌سازی
    DEFAULTS = {
        "content": "div.text-left, div.chapter-content, div#chapter-content, "
                   "div.entry-content, article, div.content, "
                   "div#TextContent, div.text, div.novel-content, "
                   "div.chapter-text, div#chaptertext, "
                   "div.post-content, div.page-content, "
                   "div.chapter_body, div#content",
        "title": "h1, h2.title, h2.chapter-title, .chapter-header h2, "
                 "div.chapter-title, span.chapter-title, "
                 "div.post-title, h1.entry-title",
        "next": "a.next, a[rel=next], a:contains('Next'), "
                "a:contains('Next Chapter'), a:contains('Forwarding')",
    }

    def __init__(self, selectors: dict = None):
        self.selectors = {**self.DEFAULTS, **(selectors or {})}

    def extract(self, html: str, url: str) -> ScrapeResult:
        """استخراج اطلاعات فصل از HTML."""
        from urllib.parse import urljoin

        soup = BeautifulSoup(html, "html.parser")

        # تشخیص نوع سایت
        site_type = SiteDetector.detect(html, url)

        # عنوان
        title = ""
        for sel in _split_selectors(self.selectors["title"]):
            el = soup.select_one(sel.strip())
            if el:
                title = el.get_text(strip=True)
                break

        # متن اصلی + عکس‌ها
        text = ""
        images = []
        for sel in _split_selectors(self.selectors["content"]):
            els = soup.select(sel.strip())
            if els:
                texts = [el.get_text(separator="\n", strip=True) for el in els]
                text = "\n\n".join(texts)

                # استخراج عکس‌ها از هر content element
                for el in els:
                    imgs = _extract_images_from_content(el, url)
                    images.extend(imgs)
                break

        # شماره فصل
        number = self._extract_chapter_number(title, url, html)

        # لینک بعدی
        next_url = _find_next_link(html, url)

        # پاک‌سازی متن
        text = clean_novel_text(text)

        return ScrapeResult(
            number=number,
            title=title,
            text=text,
            url=url,
            next_url=next_url or "",
            images=images,
            site_type=site_type,
        )

    def _extract_chapter_number(self, title: str, url: str, html: str) -> int:
        """استخراج شماره فصل از عنوان یا URL."""
        # از عنوان
        if title:
            m = re.search(r"(?:chapter|ch|فصل)\s*(\d+)", title, re.IGNORECASE)
            if m:
                return int(m.group(1))

        # از URL
        m = re.search(r"(?:chapter|ch|فصل)[/\-_]?(\d+)", url, re.IGNORECASE)
        if m:
            return int(m.group(1))

        # از عدد در انتهای URL
        m = re.search(r"[/\-_](\d+)(?:[/#?]|$)", url)
        if m:
            return int(m.group(1))

        # از محتوا — اولین فصل
        m = re.search(r"(?:chapter|ch)\s*(\d+)", html[:5000], re.IGNORECASE)
        if m:
            return int(m.group(1))

        return 0


class WordPressAdapter(GenericAdapter):
    """آداپتور وردپرس — برای سایت‌های WordPress مثل witchculttranslation."""

    DEFAULTS = {
        "content": "div.entry-content, div.post-content, article .content",
        "title": "h1.entry-title, h1.post-title, h1",
        "next": "a.next, a[rel=next], a.nav-next",
    }


class SyosetuAdapter(GenericAdapter):
    """آداپتور syosetu.com / ncode.syosetu.com."""
    DEFAULTS = {
        "content": "div#novel_honbun",
        "title": "div.novel_h + h1, h1",
        "next": "a#novel_next",
    }


class WuxiaAdapter(GenericAdapter):
    """آداپتور wuxiaworld, lightnovelworld, etc."""
    DEFAULTS = {
        "content": "div#chapter-content, div#TextContent, div.text-left, "
                   "div.chapter_body, div#content",
        "title": "h1",
        "next": "a.next, a:contains('Next')",
    }


class NovelbinAdapter(GenericAdapter):
    """آداپتور novelbin.me / novelbin.com."""
    DEFAULTS = {
        "content": "div#chapter-content, div#chapter-contentp",
        "title": "h2.chapter-title, h1",
        "next": "a#btn-next, a.next",
    }


class RoyalRoadAdapter(GenericAdapter):
    """آداپتور royalroad.com."""
    DEFAULTS = {
        "content": "div.chapter-content, div.portlet-body",
        "title": "h1-fiction-info h2, h2",
        "next": "a.btn-primary:contains('Next')",
    }


class GenericManualAdapter(GenericAdapter):
    """آداپتور دستی — کاربر selectors رو مشخص میکنه."""
    def __init__(self, content_selector: str, title_selector: str = "h1",
                 next_selector: str = "", chapter_regex: str = ""):
        super().__init__({
            "content": content_selector,
            "title": title_selector,
            "next": next_selector,
        })
        self.chapter_regex = chapter_regex

    def _extract_chapter_number(self, title: str, url: str, html: str) -> int:
        if self.chapter_regex:
            m = re.search(self.chapter_regex, title + " " + url)
            if m:
                return int(m.group(1))
        return super()._extract_chapter_number(title, url, html)


# ── اسکرپر اصلی ────────────────────────────────────────

class NovelScraper:
    """اسکرپر اصلی وب‌نول."""

    # آداپتورها بر اساس دامنه
    ADAPTERS = {
        "syosetu.com": SyosetuAdapter(),
        "ncode.syosetu.com": SyosetuAdapter(),
        "wuxiaworld.com": WuxiaAdapter(),
        "lightnovelworld.com": WuxiaAdapter(),
        "lightnovelworld.co": WuxiaAdapter(),
        "novelbin.com": NovelbinAdapter(),
        "novelbin.me": NovelbinAdapter(),
        "royalroad.com": RoyalRoadAdapter(),
        "witchculttranslation.com": WordPressAdapter(),
    }

    def __init__(self, adapter: GenericAdapter = None, use_browser: bool = False,
                 delay: float = 1.0, max_chapters: int = 0,
                 on_progress: Callable = None):
        self.adapter = adapter
        self.use_browser = use_browser
        self.delay = delay
        self.max_chapters = max_chapters
        self.on_progress = on_progress
        self._stop = False

    def stop(self):
        self._stop = True

    def scrape_chapter(self, url: str) -> ScrapeResult:
        """اسکرپ یک فصل."""
        adapter = self.adapter or self.detect_adapter(url)
        html = fetch_page(url, use_browser=self.use_browser)
        return adapter.extract(html, url)

    def scrape_novel(self, start_url: str, on_chapter: Callable = None) -> list[ScrapeResult]:
        """اسکرپ تمام فصل‌ها از یه URL شروع."""
        results = []
        current_url = start_url
        count = 0
        visited_urls = set()

        console.print(f"  🔗 شروع از: [cyan]{start_url}[/]")

        while current_url and not self._stop:
            if current_url in visited_urls:
                console.print(f"  ⚠️ حلقه لینک شناسایی شد: {current_url}")
                break
            visited_urls.add(current_url)

            if self.max_chapters and count >= self.max_chapters:
                console.print(f"  ⏹️ حداکثر {self.max_chapters} فصل رسید")
                break

            result = None
            try:
                result = self.scrape_chapter(current_url)

                if not result.text:
                    console.print(f"  ⚠️ فصل {result.number}: متن خالی — رد شد")
                    if result.next_url:
                        current_url = result.next_url
                        continue
                    else:
                        break

                results.append(result)
                count += 1

                status = f"  ✅ فصل {result.number}: {result.title[:50]} " \
                         f"({result.word_count} کلمه, {len(result.images)} عکس)"
                console.print(status)

                if on_chapter:
                    on_chapter(result)

                current_url = result.next_url

                if current_url and self.delay:
                    time.sleep(self.delay)

            except Exception as e:
                console.print(f"  ❌ خطا [{current_url[:60]}]: {e}")
                if result is not None and hasattr(result, 'next_url') and result.next_url:
                    current_url = result.next_url
                    continue
                else:
                    break

        console.print(f"  📊 {count} فصل اسکرپ شد")
        return results

    @classmethod
    def detect_adapter(cls, url: str) -> GenericAdapter:
        """تشخیص خودکار آداپتور بر اساس URL."""
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        for key, adapter in cls.ADAPTERS.items():
            normalized_key = key.lower().lstrip(".")
            if domain == normalized_key or domain.endswith("." + normalized_key):
                return adapter
        return GenericAdapter()

    @classmethod
    def scrape_one(cls, url: str, use_browser: bool = False,
                   adapter: GenericAdapter = None) -> ScrapeResult:
        """اسکرپ سریع یک فصل — بدون حلقه."""
        if not adapter:
            adapter = cls.detect_adapter(url)
        scraper = cls(adapter=adapter, use_browser=use_browser)
        return scraper.scrape_chapter(url)

    @classmethod
    def download_all_images(cls, images: list[ImageInfo],
                           progress_callback: Callable = None) -> list[ImageInfo]:
        """دانلود تمام عکس‌ها."""
        downloaded = []
        total = len(images)
        if total == 0:
            return downloaded
        for i, img in enumerate(images):
            if progress_callback:
                progress_callback((i + 1) / total, f"دانلود عکس {i + 1}/{total}")
            data = _download_image(img.url)
            if data:
                img.data = data
                downloaded.append(img)
        return downloaded


# ── دریافت صفحه ────────────────────────────────────────

def fetch_page(url: str, use_browser: bool = False) -> str:
    """دریافت HTML صفحه."""
    if use_browser:
        return _fetch_with_playwright(url)
    else:
        return _fetch_with_requests(url)


def _fetch_with_requests(url: str) -> str:
    """دریافت با requests — سریع‌تر."""
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def _fetch_with_playwright(url: str) -> str:
    """دریافت با Playwright — برای سایت‌های JS سنگین."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        with p.chromium.launch(headless=True) as browser:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            return page.content()


# ── تشخیص الگوی لینک چپتر ──────────────────────────────

class ChapterPatternDetector:
    """تشخیص خودکار الگوی لینک چپترهای بعدی."""

    # الگوهای رایج URL چپتر
    NUMERIC_PATTERNS = [
        # /chapter/123
        (r'(/chapter[s]?/)(\d+)', 1),
        # /ch/123
        (r'(/ch/)(\d+)', 1),
        # /chapter-123
        (r'(/chapter-)(\d+)', 1),
        # /123 (at end)
        (r'(/)(\d+)(?:/?)$', 1),
        # ?chapter=123
        (r'([?&]chapter=)(\d+)', 1),
        # /vol-1/chapter-123
        (r'(/vol-\d+/chapter-)(\d+)', 1),
        # /123.html
        (r'(/)(\d+)\.html?$', 1),
    ]

    @classmethod
    def detect_and_generate(cls, url: str, num_chapters: int = 5) -> list[str] | None:
        """
        تشخیص الگو و تولید لینک‌های چپترهای بعدی.
        اگه الگو پیدا نشد، None برمیگردونه.
        """
        for pattern, _ in cls.NUMERIC_PATTERNS:
            match = re.search(pattern, url)
            if match:
                # All patterns must expose the chapter number as group 2.
                start = match.start(2)
                end = match.end(2)
                current_num = int(match.group(2))

                urls = []
                for i in range(1, num_chapters + 1):
                    new_num = current_num + i
                    new_url = url[:start] + str(new_num) + url[end:]
                    urls.append(new_url)

                return urls

        # تشخیص الگوی next chapter link
        return None

    @classmethod
    def find_next_chapters_from_html(cls, html: str, current_url: str, max_chapters: int = 10) -> list[str]:
        """پیدا کردن لینک چپترهای بعدی از HTML صفحه."""
        if not BeautifulSoup:
            return []

        soup = BeautifulSoup(html, "html.parser")
        urls = []

        # جستجو بر اساس متن
        next_patterns = [
            r"next\s*chapter", r"forward", r"next",
            r"adam\s*forwarding", r"chapter\s*\d+",
        ]

        for a_tag in soup.find_all("a", href=True):
            text = a_tag.get_text(strip=True).lower()
            href = a_tag["href"]
            for pattern in next_patterns:
                if re.search(pattern, text, re.IGNORECASE) or re.search(pattern, href, re.IGNORECASE):
                    full_url = urljoin(current_url, href)
                    if full_url not in urls:
                        urls.append(full_url)
                        if len(urls) >= max_chapters:
                            return urls

        return urls
