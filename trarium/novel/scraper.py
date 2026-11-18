"""Web-novel scraper with adapter detection, next-link crawling and safe cleaning."""
from __future__ import annotations

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse

try:
    from bs4 import BeautifulSoup
except ImportError:  # imported lazily elsewhere, but give a useful error here
    BeautifulSoup = None

from rich.console import Console
from trarium.novel.cleaner import clean_novel_text

console = Console()


@dataclass
class ImageInfo:
    url: str
    alt: str = ""
    width: int = 0
    height: int = 0
    data: bytes | None = None

    @property
    def filename(self) -> str:
        path = urlparse(self.url).path.lower()
        ext = ".jpg"
        for candidate in (".png", ".webp", ".gif", ".jpeg", ".jpg"):
            if path.endswith(candidate):
                ext = candidate
                break
        return f"img_{hashlib.md5(self.url.encode('utf-8')).hexdigest()[:10]}{ext}"


@dataclass
class ScrapeResult:
    number: int = 0
    title: str = ""
    text: str = ""
    url: str = ""
    next_url: str = ""
    images: list[ImageInfo] = field(default_factory=list)
    site_type: str = "generic"

    @property
    def word_count(self) -> int:
        return len(self.text.split()) if self.text else 0

    @property
    def char_count(self) -> int:
        return len(self.text) if self.text else 0


class SiteDetector:
    SITE_PATTERNS = {
        "novelbin": [r"novelbin", r"novel-bin"],
        "wuxia": [r"wuxiaworld"],
        "lightnovel": [r"lightnovelworld"],
        "syosetu": [r"syosetu", r"ncode"],
        "webnovel": [r"webnovel\.com"],
        "tapas": [r"tapas\.io"],
        "royalroad": [r"royalroad\.com"],
        "witchcult": [r"witchculttranslation"],
    }

    @classmethod
    def detect(cls, html: str, url: str) -> str:
        domain = urlparse(url).netloc.lower()
        for site_type, patterns in cls.SITE_PATTERNS.items():
            if any(re.search(p, domain, re.I) for p in patterns):
                return site_type
        if re.search(r"wp-content|wordpress|entry-content|post-content", html[:12000], re.I):
            return "wordpress"
        return "generic"


def _require_bs4():
    if BeautifulSoup is None:
        raise RuntimeError("وابستگی وب‌ناول نصب نیست: pip install beautifulsoup4 lxml")


def _split_selectors(selector_string: str) -> list[str]:
    return [p.strip() for p in re.split(r",\s*", selector_string or "") if p.strip()]


def _find_next_link(html: str, current_url: str) -> str | None:
    _require_bs4()
    soup = BeautifulSoup(html, "html.parser")

    # 1) Semantic rel=next is the strongest signal.
    current_host = urlparse(current_url).netloc.lower()
    for tag in soup.find_all("a", href=True):
        rel = tag.get("rel") or []
        rel_values = [str(x).lower() for x in rel] if isinstance(rel, (list, tuple)) else [str(rel).lower()]
        if "next" in rel_values:
            candidate=urljoin(current_url, tag["href"])
            if urlparse(candidate).netloc.lower() == current_host:
                return candidate

    candidates = []
    next_re = re.compile(r"\bnext(?:\s+chapter)?\b|\bchapter\s+next\b", re.I)
    reject_re = re.compile(r"\b(previous|prev|back|older)\b", re.I)
    for tag in soup.find_all("a", href=True):
        text = " ".join(tag.stripped_strings)
        href = str(tag.get("href", ""))
        combined = f"{text} {href}"
        if reject_re.search(combined):
            continue
        score = 0
        if re.search(r"\bnext\s+chapter\b", text, re.I):
            score += 10
        elif re.search(r"\bnext\b", text, re.I):
            score += 7
        if next_re.search(href):
            score += 3
        if score:
            url = urljoin(current_url, href)
            if url and url != current_url and urlparse(url).netloc.lower() == current_host:
                candidates.append((score, url))
    return max(candidates, default=(0, None))[1]


def _is_ad_image(tag, url: str) -> bool:
    text = f"{url} {tag.get('alt', '')} {tag.get('class', '')}".lower()
    if re.search(r"ads?[-_]|banner|sponsor|affiliate|tracking|pixel|analytics|1x1|spacer|transparent", text):
        return True
    try:
        w = int(tag.get("width") or 0)
        h = int(tag.get("height") or 0)
        if (0 < w < 50) or (0 < h < 50):
            return True
    except (TypeError, ValueError):
        pass
    return url.lower().endswith(".svg")


def _extract_images(element, base_url: str) -> list[ImageInfo]:
    if not element:
        return []
    items = []
    seen = set()
    for img in element.find_all("img", src=True):
        url = urljoin(base_url, img.get("src", ""))
        if not url or url in seen or _is_ad_image(img, url):
            continue
        seen.add(url)
        try:
            width = int(img.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        try:
            height = int(img.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        items.append(ImageInfo(url=url, alt=str(img.get("alt") or ""), width=width, height=height))
    return items


class GenericAdapter:
    DEFAULTS = {
        "content": "div#chapter-content, div.chapter-content, div.entry-content, article, "
                   "div#TextContent, div.text, div.novel-content, div.chapter-text, "
                   "div.chapter_body, div.post-content, div.page-content, div#content",
        "title": "h1, h2.chapter-title, h2.title, div.chapter-title, h1.entry-title, div.post-title",
    }

    def __init__(self, selectors: dict | None = None):
        self.selectors = {**self.DEFAULTS, **(selectors or {})}

    def extract(self, html: str, url: str) -> ScrapeResult:
        _require_bs4()
        soup = BeautifulSoup(html, "html.parser")
        site_type = SiteDetector.detect(html, url)

        title = ""
        for selector in _split_selectors(self.selectors.get("title", "")):
            try:
                el = soup.select_one(selector)
            except Exception:
                continue
            if el:
                title = el.get_text(" ", strip=True)
                break

        content_el = None
        for selector in _split_selectors(self.selectors.get("content", "")):
            try:
                content_el = soup.select_one(selector)
            except Exception:
                continue
            if content_el:
                break

        images = _extract_images(content_el, url) if content_el else []
        if content_el:
            # Remove obvious non-story descendants before extracting text.
            for tag in content_el.find_all(["script", "style", "noscript", "iframe", "form"]):
                tag.decompose()
            text = content_el.get_text("\n", strip=True)
        else:
            text = ""

        text = clean_novel_text(text)
        next_url = _find_next_link(html, url) or ""
        number = self._extract_chapter_number(title, url, html)
        return ScrapeResult(number=number, title=title, text=text, url=url,
                            next_url=next_url, images=images, site_type=site_type)

    @staticmethod
    def _extract_chapter_number(title: str, url: str, html: str) -> int:
        for value in (title, url, html[:8000]):
            m = re.search(r"(?:chapter|ch|فصل)[\s_\-/]*(\d+)", value, re.I)
            if m:
                return int(m.group(1))
        m = re.search(r"/(\d+)(?:/)?(?:$|[?#])", url)
        return int(m.group(1)) if m else 0


class WordPressAdapter(GenericAdapter):
    DEFAULTS = {"content": "div.entry-content, div.post-content, article", "title": "h1.entry-title, h1.post-title, h1"}


class SyosetuAdapter(GenericAdapter):
    DEFAULTS = {"content": "div#novel_honbun", "title": "div.novel_h + h1, h1"}


class WuxiaAdapter(GenericAdapter):
    DEFAULTS = {"content": "div#chapter-content, div#TextContent, div.text-left, div.chapter_body, div#content", "title": "h1"}


class NovelbinAdapter(GenericAdapter):
    DEFAULTS = {"content": "div#chapter-content, div#chapter-contentp", "title": "h2.chapter-title, h1"}


class RoyalRoadAdapter(GenericAdapter):
    DEFAULTS = {"content": "div.chapter-content, div.portlet-body", "title": "h1, h2"}


class GenericManualAdapter(GenericAdapter):
    def __init__(self, content_selector: str, title_selector: str = "h1", next_selector: str = "", chapter_regex: str = ""):
        super().__init__({"content": content_selector, "title": title_selector})
        self.next_selector = next_selector
        self.chapter_regex = chapter_regex

    def extract(self, html: str, url: str) -> ScrapeResult:
        result = super().extract(html, url)
        if self.next_selector:
            _require_bs4()
            soup = BeautifulSoup(html, "html.parser")
            try:
                tag = soup.select_one(self.next_selector)
                if tag and tag.get("href"):
                    result.next_url = urljoin(url, tag["href"])
            except Exception:
                pass
        return result

    def _extract_chapter_number(self, title: str, url: str, html: str) -> int:
        if self.chapter_regex:
            m = re.search(self.chapter_regex, f"{title} {url}")
            if m:
                try:
                    return int(m.group(1))
                except (ValueError, IndexError):
                    pass
        return super()._extract_chapter_number(title, url, html)


class NovelScraper:
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

    def __init__(self, adapter: GenericAdapter | None = None, use_browser: bool = False,
                 delay: float = 0.5, max_chapters: int = 0, on_progress: Callable | None = None):
        self.adapter = adapter
        self.use_browser = use_browser
        self.delay = max(0.0, delay)
        self.max_chapters = max_chapters
        self.on_progress = on_progress
        self._stop = False

    def stop(self):
        self._stop = True

    @classmethod
    def detect_adapter(cls, url: str) -> GenericAdapter:
        domain = urlparse(url).netloc.lower()
        for host, adapter in cls.ADAPTERS.items():
            if domain == host or domain.endswith("." + host):
                return adapter
        return GenericAdapter()

    def scrape_chapter(self, url: str) -> ScrapeResult:
        adapter = self.adapter or self.detect_adapter(url)
        html = fetch_page(url, use_browser=self.use_browser)
        return adapter.extract(html, url)

    def scrape_novel(self, start_url: str, on_chapter: Callable | None = None) -> list[ScrapeResult]:
        results = []
        current = start_url
        visited = set()
        while current and not self._stop:
            if current in visited:
                console.print(f"  ⚠️ حلقه لینک متوقف شد: {current}")
                break
            if self.max_chapters and len(results) >= self.max_chapters:
                break
            visited.add(current)
            try:
                result = self.scrape_chapter(current)
            except Exception as exc:
                console.print(f"  ❌ خطا در {current}: {exc}")
                break
            if result.text:
                results.append(result)
                if on_chapter:
                    on_chapter(result)
            current = result.next_url
            if current and self.delay:
                time.sleep(self.delay)
        return results

    def scrape_chain(self, start_url: str, count: int) -> list[ScrapeResult]:
        """Follow the actual Next Chapter links; never invent URLs numerically."""
        old = self.max_chapters
        self.max_chapters = max(1, int(count))
        try:
            return self.scrape_novel(start_url)
        finally:
            self.max_chapters = old

    @classmethod
    def scrape_one(cls, url: str, use_browser: bool = False, adapter: GenericAdapter | None = None) -> ScrapeResult:
        return cls(adapter=adapter or cls.detect_adapter(url), use_browser=use_browser).scrape_chapter(url)

    @classmethod
    def download_all_images(cls, images: list[ImageInfo], progress_callback: Callable | None = None,
                            max_workers: int = 6) -> list[ImageInfo]:
        if not images:
            return []
        downloaded = []
        unique = list({img.url: img for img in images}.values())

        def one(img):
            data = _download_image(img.url)
            if data:
                img.data = data
                return img
            return None

        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(unique)))) as pool:
            futures = [pool.submit(one, img) for img in unique]
            for i, future in enumerate(as_completed(futures), 1):
                item = future.result()
                if item:
                    downloaded.append(item)
                if progress_callback:
                    progress_callback(i / len(futures), f"دانلود عکس {i}/{len(futures)}")
        return downloaded


def _download_image(url: str, timeout: int = 20) -> bytes | None:
    try:
        import requests
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        signature_ok = response.content.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8", b"RIFF"))
        if content_type.startswith("image/") or signature_ok:
            return response.content
    except Exception:
        pass
    return None


def fetch_page(url: str, use_browser: bool = False) -> str:
    return _fetch_with_playwright(url) if use_browser else _fetch_with_requests(url)


def _fetch_with_requests(url: str) -> str:
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }
    last = None
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding or "utf-8"
            return response.text
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(1 + attempt)
    raise RuntimeError(f"دریافت صفحه شکست خورد: {last}")


def _fetch_with_playwright(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("برای سایت‌های JavaScript-heavy: pip install playwright و سپس playwright install chromium") from exc
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(750)
            return page.content()
        finally:
            browser.close()
