"""استخراج متن از هر فایلی که متن دارد.

ترتیب کار: پسوند → محتوای واقعی (magic bytes) → بهترین تلاش.
اگر فایل ذاتاً متن ندارد (ویدیو، صدا، عکس، آرشیو فشردهٔ باینری) با
`UnsupportedFormatError` و پیام فارسی روشن رد می‌شود.
"""
from __future__ import annotations

import json
import re
import zipfile
from html import unescape
from pathlib import Path, PurePosixPath

from trarium.extractors.base import BaseExtractor, ExtractedBook
from trarium.extractors.formats import (
    ARCHIVE_EXTENSIONS,
    AUDIO_EXTENSIONS,
    HTML_LIKE_EXTENSIONS,
    IMAGE_EXTENSIONS,
    LEGACY_DOCUMENT_EXTENSIONS,
    PLAIN_TEXT_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ZIP_DOCUMENT_EXTENSIONS,
    is_translatable_extension,
)

MAX_TEXT_BYTES = 24 * 1024 * 1024
MIN_MEANINGFUL_CHARS = 20
CHAPTER_TARGET_CHARS = 9000


class UnsupportedFormatError(ValueError):
    """فایلی که متن قابل استخراج ندارد."""


# ---------------------------------------------------------------------------
# کمکی‌های متن
# ---------------------------------------------------------------------------
def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    good = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return good / len(text)


def decode_text(data: bytes) -> str:
    """رمزگشایی متن با چند encoding رایج."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "utf-8", "cp1256", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if "\x00" in text and encoding != "latin-1":
            continue
        if encoding == "latin-1" or _printable_ratio(text) >= 0.85:
            return text
    raise UnsupportedFormatError("متن فایل با هیچ encoding شناخته‌شده‌ای خوانده نشد.")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|section|article|blockquote|title)>", "\n", text)
    text = re.sub(r"(?i)</t[dh]>", "\t", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text)
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clean_plain(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return re.sub(r"\n{4,}", "\n\n\n", text).strip()


def _tag_text(fragment: str, tag: str) -> str:
    parts = re.findall(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>", fragment)
    return " ".join(unescape(re.sub(r"(?s)<[^>]+>", "", p)).strip() for p in parts).strip()


def _split_chapters(text: str, prefix: str = "بخش") -> list[dict]:
    """متن بلند را به بخش‌های قابل ترجمه می‌شکند (بدون تغییر محتوا)."""
    text = text.strip()
    if not text:
        return []
    paragraphs = re.split(r"\n\s*\n", text)
    chapters: list[dict] = []
    buffer: list[str] = []
    size = 0
    for para in paragraphs:
        para_len = len(para) + 2
        if buffer and size + para_len > CHAPTER_TARGET_CHARS:
            chapters.append({"title": f"{prefix} {len(chapters) + 1}", "content": "\n\n".join(buffer)})
            buffer, size = [], 0
        buffer.append(para)
        size += para_len
    if buffer:
        chapters.append({"title": f"{prefix} {len(chapters) + 1}", "content": "\n\n".join(buffer)})
    return chapters


def _metadata_of(path: Path) -> tuple[str, str]:
    """عنوان/نویسنده از متادیتای فایل‌های آفیس و EPUB."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            for candidate in ("docProps/core.xml", "meta.xml"):
                if candidate not in names:
                    continue
                raw = zf.read(candidate).decode("utf-8", errors="replace")
                title = _tag_text(raw, "dc:title") or _tag_text(raw, "title")
                creator = _tag_text(raw, "dc:creator") or _tag_text(raw, "creator")
                if title or creator:
                    return title or path.stem, creator
    except Exception:
        pass
    return path.stem, ""


# ---------------------------------------------------------------------------
# خواننده‌های فرمت‌های خاص
# ---------------------------------------------------------------------------
def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        parts = [n for n in names if re.fullmatch(r"word/(document|footnotes|endnotes|header\d*|footer\d*)\.xml", n)]
        if not parts:
            raise UnsupportedFormatError("ساختار فایل DOCX قابل خواندن نبود.")
        chunks = []
        for name in parts:
            raw = zf.read(name).decode("utf-8", errors="replace")
            for para in re.findall(r"(?is)<w:p\b[^>]*>.*?</w:p>", raw):
                para = re.sub(r"(?i)<w:tab\s*/>", "\t", para)
                para = re.sub(r"(?i)<w:br\s*/>", "\n", para)
                pieces = re.findall(r"(?is)<w:t\b[^>]*>(.*?)</w:t>", para)
                line = unescape("".join(pieces)).strip()
                if line:
                    chunks.append(line)
        return "\n".join(chunks)


def _pptx_text(path: Path) -> str:
    blocks = []
    with zipfile.ZipFile(path) as zf:
        slides = [n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        slides.sort(key=lambda n: int(re.search(r"(\d+)", PurePosixPath(n).name).group(1)))
        for index, name in enumerate(slides, 1):
            raw = zf.read(name).decode("utf-8", errors="replace")
            lines = []
            for para in re.findall(r"(?is)<a:p\b[^>]*>.*?</a:p>", raw):
                line = unescape("".join(re.findall(r"(?is)<a:t\b[^>]*>(.*?)</a:t>", para))).strip()
                if line:
                    lines.append(line)
            if lines:
                blocks.append((f"اسلاید {index}", "\n".join(lines)))
    if not blocks:
        raise UnsupportedFormatError("متنی داخل اسلایدها پیدا نشد.")
    return "\n\n".join(f"{title}\n{text}" for title, text in blocks)


def _xlsx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            raw = zf.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
            for item in re.findall(r"(?is)<si>(.*?)</si>", raw):
                shared.append(unescape("".join(re.findall(r"(?is)<t\b[^>]*>(.*?)</t>", item))))
        blocks = []
        for name in sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)):
            raw = zf.read(name).decode("utf-8", errors="replace")
            rows = []
            for row in re.findall(r"(?is)<row\b[^>]*>(.*?)</row>", raw):
                cells = []
                for cell in re.findall(r"(?is)<c\b[^>]*>.*?</c>", row):
                    if 't="s"' in cell:
                        idx = re.search(r"(?is)<v>(\d+)</v>", cell)
                        if idx and int(idx.group(1)) < len(shared):
                            cells.append(shared[int(idx.group(1))])
                        continue
                    inline = re.findall(r"(?is)<t\b[^>]*>(.*?)</t>", cell)
                    if inline:
                        cells.append(unescape("".join(inline)))
                        continue
                    value = re.search(r"(?is)<v>(.*?)</v>", cell)
                    if value:
                        cells.append(unescape(value.group(1)))
                line = " | ".join(c.strip() for c in cells if c.strip())
                if line:
                    rows.append(line)
            if rows:
                blocks.append("\n".join(rows))
        return "\n\n".join(blocks)


def _odf_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        if "content.xml" not in zf.namelist():
            raise UnsupportedFormatError("ساختار فایل OpenDocument قابل خواندن نبود.")
        raw = zf.read("content.xml").decode("utf-8", errors="replace")
    raw = re.sub(r"(?is)<text:(note|tracked-changes)\b.*?</text:\1>", " ", raw)
    lines = []
    for match in re.finditer(r"(?is)<text:(p|h)\b[^>]*>(.*?)</text:\1>", raw):
        inner = match.group(2)
        inner = re.sub(r"(?i)<text:(s|tab)\s*/>", " ", inner)
        inner = re.sub(r"(?i)<text:line-break\s*/>", "\n", inner)
        line = unescape(re.sub(r"(?s)<[^>]+>", "", inner)).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _rtf_text(path: Path) -> str:
    raw = path.read_bytes().decode("latin-1", errors="replace")

    def unicode_escape(match: re.Match) -> str:
        code = int(match.group(1))
        if code < 0:
            code += 65536
        return chr(code)

    raw = re.sub(r"\\u(-?\d+)\s?\??", unicode_escape, raw)

    def hex_escape(match: re.Match) -> str:
        try:
            return bytes([int(match.group(1), 16)]).decode("cp1256")
        except Exception:
            return ""

    raw = re.sub(r"\\'([0-9a-fA-F]{2})", hex_escape, raw)
    raw = re.sub(r"\\par[d]?\b", "\n", raw)
    raw = re.sub(r"\\line\b", "\n", raw)
    raw = re.sub(r"\\tab\b", "\t", raw)
    raw = re.sub(r"\{\\\*.*?\}", " ", raw, flags=re.S)
    raw = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", raw)
    raw = raw.replace("{", "").replace("}", "")
    return _clean_plain(raw)


def _subtitle_text(path: Path, ext: str) -> str:
    text = decode_text(path.read_bytes())
    text = text.replace("\ufeff", "")
    if ext in {".ass", ".ssa"}:
        lines = []
        for line in text.splitlines():
            if not line.lower().startswith("dialogue:"):
                continue
            fields = line.split(",", 9)
            if len(fields) < 10:
                continue
            body = fields[9].replace("\\N", "\n").replace("\\n", "\n")
            body = re.sub(r"\{[^}]*\}", "", body).strip()
            if body:
                lines.append(body)
        return "\n".join(lines)
    if ext == ".lrc":
        lines = []
        for line in text.splitlines():
            line = re.sub(r"^\[[^\]]*\]", "", line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)
    # SRT / VTT / SMI / SBV
    text = _strip_html(text) if ext in {".smi", ".sami"} else unescape(text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.upper().startswith("WEBVTT"):
            continue
        if stripped.startswith("NOTE ") or stripped.startswith("STYLE"):
            continue
        if "-->" in stripped or re.fullmatch(r"\d{1,6}", stripped):
            continue
        if re.fullmatch(r"[\d:.,\s]+", stripped):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def _notebook_text(path: Path) -> str:
    try:
        data = json.loads(decode_text(path.read_bytes()))
    except json.JSONDecodeError as exc:
        raise UnsupportedFormatError(f"فایل Jupyter معتبر نبود: {exc}") from exc
    cells = []
    for cell in data.get("cells", []):
        source = cell.get("source", "")
        body = "".join(source) if isinstance(source, list) else str(source)
        if body.strip():
            cells.append(body.strip())
    return "\n\n".join(cells)


def _zip_bundle_text(path: Path) -> str:
    """آرشیو ZIP: فایل‌های متنی داخلش را پشت‌سرهم می‌خواند."""
    blocks = []
    budget = MAX_TEXT_BYTES
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.file_size == 0 or info.file_size > budget:
                continue
            name = info.filename
            if "__MACOSX" in name or name.startswith("."):
                continue
            ext = PurePosixPath(name).suffix.lower()
            if not (is_translatable_extension(ext) or ext in PLAIN_TEXT_EXTENSIONS):
                continue
            data = zf.read(info)
            try:
                text = decode_text(data)
            except UnsupportedFormatError:
                continue
            if text.count("\x00") > 0 or _printable_ratio(text) < 0.9:
                continue
            budget -= len(data)
            blocks.append((name, text))
            if budget <= 0:
                break
    if not blocks:
        raise UnsupportedFormatError(
            "داخل این آرشیو فایل متنی قابل ترجمه‌ای پیدا نشد. "
            "آرشیو صرفاً یک بستهٔ باینری است و ترجمه نمی‌شود."
        )
    return "\n\n".join(f"## {name}\n{text.strip()}" for name, text in blocks)


def _pymupdf_text(path: Path) -> str:
    """MOBI/AZW3/XPS/CBZ و مشابه‌ها را با MuPDF می‌خواند."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return ""
    try:
        doc = pymupdf.open(str(path))
    except Exception:
        return ""
    try:
        pages = [page.get_text("text") for page in doc]
    finally:
        doc.close()
    return _clean_plain("\n\n".join(p for p in pages if p.strip()))


def _salvage_text(path: Path) -> str:
    """بهترین تلاش برای اسناد باینری قدیمی: بیرون کشیدن رشته‌های خوانا."""
    data = path.read_bytes()[:MAX_TEXT_BYTES]
    ascii_runs = re.findall(rb"[\x20-\x7e\r\n\t]{4,}", data)
    text = "\n".join(run.decode("latin-1", errors="replace").strip() for run in ascii_runs)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < 200:
        raise UnsupportedFormatError(
            "این فایل باینری است و متن قابل استخراجی ندارد. "
            "اگر سند Word/متنی است، لطفاً آن را به DOCX یا PDF تبدیل کنید."
        )
    return text


# ---------------------------------------------------------------------------
# تشخیص محتوا
# ---------------------------------------------------------------------------
def sniff_container(path: Path) -> str:
    """نوع واقعی فایل را از چند بایت اول تشخیص می‌دهد."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return "unknown"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith((b"\xff\xd8\xff", b"GIF8", b"BM", b"II*\x00", b"MM\x00*")):
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head.startswith((b"ID3", b"OggS", b"fLaC", b"FORM", b"\x1aE\xdf\xa3")):
        return "audio" if head.startswith((b"ID3", b"OggS", b"fLaC", b"FORM")) else "video"
    if len(head) > 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"avif", b"mif1", b"msf1"):
            return "image"
        return "video"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c") or head.startswith(b"Rar!") or head.startswith((b"\x1f\x8b", b"BZh", b"\xfd7zXZ")):
        return "archive"
    if head.startswith((b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe")):
        return "executable"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if b"\x00" in head[:512]:
        return "binary"
    return "text"


def _zip_kind(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return "binary"
    if "mimetype" in names:
        try:
            with zipfile.ZipFile(path) as zf:
                mime = zf.read("mimetype").decode("ascii", errors="ignore")
        except Exception:
            mime = ""
        if "epub" in mime:
            return "epub"
    if any(n.startswith("word/") for n in names):
        return "docx"
    if any(n.startswith("ppt/") for n in names):
        return "pptx"
    if any(n.startswith("xl/") for n in names):
        return "xlsx"
    if "content.xml" in names:
        return "odf"
    return "zip"


# ---------------------------------------------------------------------------
# استخراج‌کنندهٔ عمومی
# ---------------------------------------------------------------------------
class GenericTextExtractor(BaseExtractor):
    """هر فایل متنی/سندی را می‌خواند؛ وگرنه دلیل روشن می‌دهد."""

    def extract(self, file_path: str) -> ExtractedBook:
        path = self.validate_file(file_path)
        ext = path.suffix.lower()
        container = sniff_container(path)

        if container == "image" or ext in IMAGE_EXTENSIONS:
            raise UnsupportedFormatError(
                f"«{path.name}» یک تصویر است؛ تراریوم متن داخل عکس را بدون OCR نمی‌تواند بخواند. "
                "اگر عکس شامل متن است، آن را به PDF تبدیل یا OCR کنید و بعد بفرستید."
            )
        if container == "audio" or ext in AUDIO_EXTENSIONS:
            raise UnsupportedFormatError(
                f"«{path.name}» فایل صوتی است و تراریوم صدا را به متن تبدیل نمی‌کند؛ "
                "امکان ترجمهٔ آن وجود ندارد. لطفاً زیرنویس یا متن آن را بفرستید."
            )
        if container == "video" or ext in VIDEO_EXTENSIONS:
            raise UnsupportedFormatError(
                f"«{path.name}» ویدیو است و تراریوم نمی‌تواند ویدیو را ترجمه کند. "
                "فقط فایل‌های متنی، کتاب و زیرنویس قابل ترجمه‌اند."
            )
        if ext in ARCHIVE_EXTENSIONS and container in {"archive", "executable", "binary"}:
            raise UnsupportedFormatError(
                f"«{path.name}» یک فایل فشرده/اجرایی است و متن قابل ترجمه ندارد. "
                "اگر داخلش فایل متنی هست، ابتدا از حالت فشرده خارج کنید."
            )

        text = ""
        title, author = _metadata_of(path)
        strategy = "متن مستقیم"

        if container == "pdf" and ext != ".pdf":
            from trarium.extractors.pdf import PDFExtractor

            book = PDFExtractor().extract(str(path))
            book.title = book.title or path.stem
            return book

        if ext in {".ipynb"}:
            text, strategy = _notebook_text(path), "نوت‌بوک Jupyter"
        elif container == "zip" or ext in ZIP_DOCUMENT_EXTENSIONS:
            kind = _zip_kind(path)
            if ext == ".epub" or container == "epub" or kind == "epub":
                from trarium.extractors.epub import EPUBExtractor

                book = EPUBExtractor().extract(str(path))
                return book
            if kind == "docx":
                text, strategy = _docx_text(path), "سند Word"
            elif kind == "pptx":
                text, strategy = _pptx_text(path), "ارائه PowerPoint"
            elif kind == "xlsx":
                text, strategy = _xlsx_text(path), "صفحه‌گسترده Excel"
            elif kind == "odf":
                text, strategy = _odf_text(path), "سند OpenDocument"
            else:
                text, strategy = _zip_bundle_text(path), "محتوای متنی آرشیو"
        elif ext in {".rtf"} or container == "rtf":
            text, strategy = _rtf_text(path), "Rich Text"
        elif ext in SUBTITLE_EXTENSIONS:
            text, strategy = _subtitle_text(path, ext), "زیرنویس"
        elif ext in LEGACY_DOCUMENT_EXTENSIONS or ext in {".xps", ".oxps"}:
            text = _pymupdf_text(path)
            strategy = "خواندن با MuPDF"
            if len(text) < MIN_MEANINGFUL_CHARS:
                text, strategy = _salvage_text(path), "بهترین تلاش (سند قدیمی)"
        elif ext in HTML_LIKE_EXTENSIONS:
            raw = decode_text(path.read_bytes())
            text = _strip_html(raw)
            html_title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
            if html_title:
                title = unescape(re.sub(r"\s+", " ", html_title.group(1))).strip() or title
            strategy = "HTML/XML"
        else:
            # متن ساده، کد، داده، یا هر پسوند ناشناسی که واقعاً متن است.
            data = path.read_bytes()[:MAX_TEXT_BYTES]
            try:
                text = decode_text(data)
            except UnsupportedFormatError:
                if ext in LEGACY_DOCUMENT_EXTENSIONS or ext == "":
                    text = _salvage_text(path)
                    strategy = "بهترین تلاش"
                else:
                    raise
            if text.count("\x00") > 0 or _printable_ratio(text) < 0.85:
                text = _salvage_text(path)
                strategy = "بهترین تلاش"
            else:
                strategy = "متن مستقیم"

        text = _clean_plain(text)
        if len(text) < MIN_MEANINGFUL_CHARS:
            raise UnsupportedFormatError(
                f"«{path.name}» متن قابل ترجمه‌ای ندارد (روش: {strategy})."
            )

        chapters = _split_chapters(text)
        book = ExtractedBook(
            title=title or path.stem,
            author=author or "ناشناس",
            chapters=chapters,
            full_text=text,
            total_chars=len(text),
        )
        return book
