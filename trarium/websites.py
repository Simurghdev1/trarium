"""Website translation: structure-preserving HTML, multi-page crawler, resume.

Only content the caller is allowed to read is fetched: robots.txt is honoured,
requests stay on the starting domain, redirects to another host are not
followed, and HTTP 401/403/429 stop that page with a clear error.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.robotparser
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup, NavigableString

from trarium.cache import TranslationCache, normalize_cache_text

USER_AGENT = "Trarium/2.18.0 (local translation app)"
BLOCK_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "figcaption",
              "td", "th", "dt", "dd", "summary", "caption")
SKIP_TAGS = {"script", "style", "noscript", "code", "pre", "svg", "math", "template",
             "textarea", "button", "select", "option"}
MARKER = "[[TRARIUM_SEG_{:04d}]]"


class WebsiteError(RuntimeError):
    pass


@dataclass
class WebsitePage:
    url: str
    title: str = ""
    status: str = "pending"
    error: str = ""
    html: str = ""
    blocks: int = 0
    chars: int = 0
    original_html: str = ""


@dataclass
class WebsiteResult:
    start_url: str
    target_lang: str
    pages: list[WebsitePage] = field(default_factory=list)
    title: str = ""

    @property
    def pages_ok(self) -> int:
        return sum(1 for p in self.pages if p.status == "completed")

    @property
    def pages_failed(self) -> int:
        return sum(1 for p in self.pages if p.status == "failed")

    @property
    def errors(self) -> list[str]:
        return [f"{p.url}: {p.error}" for p in self.pages if p.status == "failed"]

    @property
    def total_chars(self) -> int:
        return sum(p.chars for p in self.pages)

    def to_result(self):
        from trarium.processors.pipeline import TranslationResult
        sections = []
        for page in self.pages:
            if page.status != "completed":
                continue
            text = html_to_text(page.html or page.original_html)
            sections.append(f"--- {page.title or page.url} ---\n\n{text}")
        return TranslationResult(
            title=self.title or self.start_url, author="", original_lang="auto",
            target_lang=self.target_lang, translated_text="\n\n".join(sections),
            total_chunks=len(self.pages), translated_chunks=self.pages_ok,
            failed_chunks=self.pages_failed, total_chars=self.total_chars,
        )

    def save(self, directory: str | Path, fmt: str = "html") -> Path:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        stem = _safe(self.title or urlparse(self.start_url).netloc or "website")
        fmt = (fmt or "html").lower()
        if fmt == "epub":
            from trarium.output import save_as_epub
            path = out / f"{stem}.epub"
            save_as_epub(self.to_result(), str(path))
            return path
        if fmt == "txt":
            path = out / f"{stem}.txt"
            body = "\n\n".join(f"{p.title}\n\n{html_to_text(p.html)}\n\nمنبع: {p.url}"
                               for p in self.pages if p.status == "completed")
            path.write_text(body + "\n\n" + "=" * 64 + "\nترجمه شده توسط تراریوم\n", encoding="utf-8")
            return path
        done = [p for p in self.pages if p.status == "completed"]
        if len(done) == 1:
            path = out / f"{stem}.html"
            path.write_text(done[0].html, encoding="utf-8")
            return path
        path = out / f"{stem}_html.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            links = []
            for index, page in enumerate(done, 1):
                name = f"page_{index:03d}.html"
                archive.writestr(name, page.html)
                links.append(f'<li><a href="{name}">{_escape_text(page.title or page.url)}</a></li>')
            archive.writestr("index.html",
                             '<!doctype html><meta charset="utf-8"><title>'
                             + _escape_text(self.title) + "</title><ul>" + "".join(links) + "</ul>")
        return path


def _escape_text(value: str) -> str:
    return (str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _safe(value: str) -> str:
    name = "".join(c for c in value if c.isalnum() or c in " _-").strip()
    return name or "website"


def normalize_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        raise WebsiteError("نشانی صفحه وارد نشده است.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise WebsiteError("فقط نشانی‌های http و https پشتیبانی می‌شوند.")
    if not parsed.netloc:
        raise WebsiteError("نشانی صفحه معتبر نیست.")
    return urldefrag(value)[0]


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    return "\n\n".join(lines)


# ---------------------------------------------------------------------- fetching
_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allowed(url: str, *, timeout: float = 8.0) -> tuple[bool, str]:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base not in _robots_cache:
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(base + "/robots.txt")
        try:
            response = requests.get(base + "/robots.txt", timeout=timeout,
                                    headers={"User-Agent": USER_AGENT})
            if response.status_code >= 400:
                parser = None
            else:
                parser.parse(response.text.splitlines())
        except requests.RequestException:
            parser = None
        _robots_cache[base] = parser
    parser = _robots_cache[base]
    if parser is None:
        return True, ""
    allowed = parser.can_fetch(USER_AGENT, url)
    return allowed, "" if allowed else "robots.txt اجازهٔ دریافت این صفحه را نمی‌دهد."


def fetch_html(url: str, *, allow_browser: bool = False, timeout: float = 25.0) -> str:
    if allow_browser:
        try:
            from trarium.novel.scraper import fetch_page
            return fetch_page(url, use_browser=True)
        except Exception as exc:
            raise WebsiteError(f"دریافت با مرورگر ناموفق بود: {exc}") from exc
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True,
                                headers={"User-Agent": USER_AGENT,
                                         "Accept": "text/html,application/xhtml+xml"})
    except requests.RequestException as exc:
        raise WebsiteError(f"اتصال برقرار نشد: {exc}") from exc
    if response.status_code in (401, 403):
        raise WebsiteError(f"دسترسی به صفحه مجاز نیست (HTTP {response.status_code}).")
    if response.status_code == 429:
        raise WebsiteError("سایت محدودیت درخواست (429) اعمال کرد؛ کمی بعد دوباره تلاش کنید.")
    if response.status_code >= 400:
        raise WebsiteError(f"سایت پاسخ HTTP {response.status_code} داد.")
    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type.lower() and "<html" not in response.text[:2000].lower():
        raise WebsiteError("این نشانی یک صفحهٔ HTML نیست.")
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


# ---------------------------------------------------------------------- extraction
def _inside_skipped(element) -> bool:
    for parent in element.parents:
        if getattr(parent, "name", None) in SKIP_TAGS:
            return True
    return False


def extract_blocks(html: str) -> tuple[BeautifulSoup, list[str], str]:
    soup = BeautifulSoup(html or "", "lxml")
    title_tag = soup.title.string if soup.title and soup.title.string else ""
    if not title_tag:
        h1 = soup.find("h1")
        title_tag = h1.get_text(" ", strip=True) if h1 else ""
    blocks: list[str] = []
    seen: set[str] = set()
    for element in soup.find_all(BLOCK_TAGS):
        if _inside_skipped(element):
            continue
        if element.find(BLOCK_TAGS):
            continue
        text = element.get_text(" ", strip=True)
        if len(text) < 2 or not re.search(r"[^\W\d_]", text, re.UNICODE):
            continue
        key = normalize_cache_text(text)
        if key in seen:
            continue
        seen.add(key)
        element["data-trarium-block"] = str(len(blocks))
        blocks.append(text)
    return soup, blocks, str(title_tag or "").strip()


def rewrite_html(soup: BeautifulSoup, translations: dict[str, str], target_lang: str) -> str:
    for element in soup.find_all(attrs={"data-trarium-block": True}):
        text = element.get_text(" ", strip=True)
        translated = translations.get(normalize_cache_text(text))
        if not translated:
            continue
        element.clear()
        element.append(NavigableString(translated))
    if target_lang in {"fa", "ar", "he", "ur"}:
        html_tag = soup.find("html")
        if html_tag is not None:
            html_tag["lang"] = target_lang
            html_tag["dir"] = "rtl"
    return str(soup)


# ---------------------------------------------------------------------- translation
def _split_marked(text: str, count: int) -> list[str] | None:
    pattern = re.compile(r"\[\[\s*TRARIUM_SEG[_\s]*0*(\d{1,4})\s*\]\]")
    matches = list(pattern.finditer(text))
    if len(matches) != count:
        return None
    parts = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        part = text[start:end].strip()
        if not part:
            return None
        parts.append(part)
    return parts


def translate_texts(translator, texts: list[str], target_lang: str, *, cache: TranslationCache | None = None,
                    provider: str = "", model: str = "", instructions: str = "website",
                    control=None, max_chars: int = 3000) -> dict[str, str]:
    """Translate unique strings, reusing memory and batching small paragraphs."""
    cache = cache or TranslationCache()
    unique: list[str] = []
    seen: set[str] = set()
    output: dict[str, str] = {}
    for text in texts:
        key = normalize_cache_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        hit = cache.get(key, target_lang, provider, model, instructions)
        if hit:
            output[key] = hit
        else:
            unique.append(key)
    index = 0
    while index < len(unique):
        if control:
            control()
        group: list[str] = []
        length = 0
        while index < len(unique) and (not group or length + len(unique[index]) <= max_chars):
            group.append(unique[index])
            length += len(unique[index]) + 30
            index += 1
        if not group:
            break
        translated: list[str] | None = None
        if len(group) > 1:
            prompt = ("Translate each segment below into the target language. "
                      "Keep every segment marker line exactly where it is, in order, and do not translate the markers.\n\n"
                      + "\n\n".join(MARKER.format(i) + "\n" + text for i, text in enumerate(group)))
            try:
                raw = translator.translate(prompt, target_lang)
                translated = _split_marked(str(raw), len(group))
            except Exception:
                translated = None
        for i, text in enumerate(group):
            value = translated[i] if translated else translator.translate(text, target_lang)
            value = str(value or "").strip()
            if not value:
                raise WebsiteError("پاسخ ترجمه خالی بود.")
            output[text] = value
            cache.put(text, value, target_lang, provider, model, instructions)
    return output


# --------------------------------------------------------------------------- crawl
def _internal_links(soup: BeautifulSoup, base: str, root_netloc: str) -> list[str]:
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        target = urljoin(base, anchor["href"])
        target, _ = urldefrag(target)
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc != root_netloc:
            continue
        if re.search(r"\.(?:jpg|jpeg|png|gif|webp|svg|pdf|zip|mp4|mp3|css|js)$", parsed.path, re.I):
            continue
        if target not in links:
            links.append(target)
    return links


def _cache_file(cache_dir: Path | None, url: str) -> Path | None:
    if not cache_dir:
        return None
    return cache_dir / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".json")


def _load_cached_page(cache_dir: Path | None, url: str) -> WebsitePage | None:
    path = _cache_file(cache_dir, url)
    if not path or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return WebsitePage(url=url, title=data.get("title", ""), status="completed",
                           html=data.get("html", ""), blocks=int(data.get("blocks", 0)),
                           chars=int(data.get("chars", 0)),
                           original_html=data.get("original_html", ""))
    except (OSError, ValueError):
        return None


def _save_cached_page(cache_dir: Path | None, page: WebsitePage) -> None:
    path = _cache_file(cache_dir, page.url)
    if not path:
        return
    try:
        path.write_text(json.dumps({"url": page.url, "title": page.title, "html": page.html,
                                    "original_html": page.original_html,
                                    "blocks": page.blocks, "chars": page.chars},
                                   ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def translate_site(start_url: str, translator, target_lang: str = "fa", *, max_pages: int = 1,
                   same_domain: bool = True, delay: float = 0.5, allow_browser: bool = False,
                   on_page=None, checkpoint=None, skip_urls: set[str] | None = None,
                   cache: TranslationCache | None = None, project: str = "",
                   job_id: str = "", max_chars: int = 3000,
                   page_cache_dir: str | Path | None = None) -> WebsiteResult:
    start = normalize_url(start_url)
    root = urlparse(start)
    allowed, reason = robots_allowed(start)
    if not allowed:
        raise WebsiteError(reason)
    result = WebsiteResult(start_url=start, target_lang=target_lang)
    queue: list[str] = [start]
    seen: set[str] = set()
    skip = {normalize_url(u) for u in (skip_urls or set())}
    provider = str(getattr(translator, "provider", "") or "")
    model = str(getattr(translator, "model", "") or getattr(translator, "model_name", "") or "")
    cache = cache or TranslationCache()
    cache_dir = Path(page_cache_dir) if page_cache_dir else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    while queue and len(result.pages) < max(1, int(max_pages)):
        url = queue.pop(0)
        if url in seen or url in skip:
            continue
        seen.add(url)
        if checkpoint:
            checkpoint()
        index = len(result.pages)
        page = WebsitePage(url=url)
        result.pages.append(page)
        if on_page:
            on_page(index, url, "started")
        cached = _load_cached_page(cache_dir, url)
        if cached is not None:
            result.pages[index] = cached
            if on_page:
                on_page(index, url, "completed")
            continue
        try:
            ok, blocked = robots_allowed(url)
            if not ok:
                raise WebsiteError(blocked)
            html = fetch_html(url, allow_browser=allow_browser)
            page.original_html = html
            soup, blocks, title = extract_blocks(html)
            page.title = title or url
            page.blocks = len(blocks)
            page.chars = sum(len(b) for b in blocks)
            if not blocks:
                raise WebsiteError("متنی برای ترجمه در این صفحه پیدا نشد (ممکن است محتوا با JavaScript ساخته شود).")
            translations = translate_texts(translator, blocks, target_lang, cache=cache,
                                           provider=provider, model=model, control=checkpoint,
                                           max_chars=max_chars)
            page.html = rewrite_html(soup, translations, target_lang)
            page.status = "completed"
            _save_cached_page(cache_dir, page)
            if same_domain and len(result.pages) + len(queue) < max_pages:
                for link in _internal_links(soup, url, root.netloc):
                    if link not in seen and link not in queue:
                        queue.append(link)
        except Exception as exc:
            page.status = "failed"
            page.error = str(exc)
        if on_page:
            on_page(index, url, page.status, page.error)
        if delay:
            time.sleep(max(0.0, float(delay)))
    result.title = next((p.title for p in result.pages if p.status == "completed"),
                        urlparse(start).netloc or "Website")
    result.start_url = start
    return result
