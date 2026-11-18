"""استخراج متن از هر فایلی که متن دارد.

ترتیب کار: پسوند → محتوای واقعی (magic bytes) → بهترین تلاش.
اگر فایل ذاتاً متن ندارد (ویدیو، صدا، عکس، آرشیو فشردهٔ باینری) با
`UnsupportedFormatError` و پیام فارسی روشن رد می‌شود.
"""
from __future__ import annotations

import email
import json
import re
import tempfile
import zipfile
from email import policy
from html import unescape
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from trarium.extractors.base import BaseExtractor, ExtractedBook
from trarium.extractors.catalog import COMIC_EXTENSIONS
from trarium.extractors.formats import (
    ARCHIVE_EXTENSIONS,
    AUDIO_EXTENSIONS,
    BINARY_ONLY_EXTENSIONS,
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

# پسوندهایی که فقط ظرف زیپ‌اند اما سند متنی داخلشان نیست (بستهٔ نرم‌افزاری).
PACKAGE_EXTENSIONS = {
    ".jar", ".war", ".ear", ".apk", ".aab", ".ipa", ".whl", ".egg", ".gem",
    ".crate", ".nupkg", ".snupkg", ".vsix", ".xpi", ".crx", ".oxt", ".xlam",
    ".xla", ".xll", ".xlb", ".xlc", ".xlw", ".deb", ".rpm", ".msi", ".msix",
    ".appx", ".class", ".pyc", ".pyo", ".pyd",
}

# اعضای زیپ که خودشان سندند و باید بازگشتی خوانده شوند.
NESTED_DOCUMENT_EXTENSIONS = {
    ".docx", ".docm", ".dotx", ".dotm", ".pptx", ".pptm", ".ppsx", ".xlsx",
    ".xlsm", ".xltx", ".odt", ".ott", ".ods", ".ots", ".odp", ".otp", ".rtf",
    ".epub", ".pdf", ".html", ".htm", ".txt", ".md", ".xml", ".fb2",
}


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


def _utf16_without_bom(data: bytes) -> str | None:
    """UTF-16 بدون BOM را از الگوی بایت‌های صفر تشخیص می‌دهد."""
    sample = data[:4096]
    if len(sample) < 8:
        return None
    half = len(sample) // 2
    even_nuls = sample[0::2].count(0)
    odd_nuls = sample[1::2].count(0)
    if odd_nuls > half * 0.7 and even_nuls < half * 0.15:
        return data.decode("utf-16-le", errors="replace")
    if even_nuls > half * 0.7 and odd_nuls < half * 0.15:
        return data.decode("utf-16-be", errors="replace")
    return None


def _control_ratio(text: str) -> float:
    """نسبت کاراکترهای کنترلی/خراب؛ برای انتخاب بین latin-1 و UTF-16."""
    if not text:
        return 1.0
    bad = sum(
        1
        for ch in text
        if (ord(ch) < 32 and ch not in "\n\r\t") or ch == "\ufffd" or 0xE000 <= ord(ch) <= 0xF8FF
    )
    return bad / len(text)


def _utf16_candidate(data: bytes) -> str | None:
    """اگر بایت‌ها شبیه UTF-16 باشند، متن رمزگشایی‌شده را برمی‌گرداند."""
    if len(data) < 4 or len(data) % 2 != 0:
        return None
    best: tuple[float, str] | None = None
    for encoding in ("utf-16-le", "utf-16-be"):
        try:
            text = data.decode(encoding, errors="replace")
        except LookupError:
            continue
        score = _control_ratio(text)
        if best is None or score < best[0]:
            best = (score, text)
    if best is None or best[0] > 0.01:
        return None
    return best[1]


def decode_text(data: bytes) -> str:
    """رمزگشایی متن با چند encoding رایج (شامل UTF-16 بدون BOM)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    utf16_with_nuls = _utf16_without_bom(data)
    if utf16_with_nuls is not None:
        return utf16_with_nuls
    for encoding in ("utf-8-sig", "utf-8", "cp1256", "cp1252"):
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if "\x00" in text:
            continue
        if _printable_ratio(text) >= 0.85:
            return text
    latin = data.decode("latin-1", errors="replace")
    utf16 = _utf16_candidate(data)
    # اگر UTF-16 تمیزتر از latin-1 بود (متن فارسی/عربی بدون BOM)، همان را بده.
    if utf16 is not None and _control_ratio(utf16) < _control_ratio(latin):
        return utf16
    return latin


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


def _strip_inline_markup(text: str) -> str:
    """تگ‌های درون‌خطی زیرنویس (i/b/u/font) و کدهای ASS را حذف می‌کند."""
    text = re.sub(r"(?is)</?(?:i|b|u|s|em|strong|font|c|v)\b[^>]*>", "", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    return unescape(text).strip()


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
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_regex_lines(xml_bytes: bytes) -> list[str]:
    """پشتیبان برای XML بدون namespace (سندهایی که xmlns ندارند)."""
    raw = xml_bytes.decode("utf-8", errors="replace")
    lines: list[str] = []
    for paragraph in re.findall(r"(?is)<w:p\b[^>]*>.*?</w:p>", raw):
        paragraph = re.sub(r"(?i)<w:tab\s*/>", "\t", paragraph)
        paragraph = re.sub(r"(?i)<w:br\s*/>", "\n", paragraph)
        pieces = re.findall(r"(?is)<w:t\b[^>]*>(.*?)</w:t>", paragraph)
        line = unescape("".join(pieces)).strip()
        if line:
            lines.append(line)
    return lines


def _docx_paragraph_lines(xml_bytes: bytes) -> list[str]:
    """پاراگراف‌ها را به ترتیب سند می‌خواند؛ تکست‌باکس‌ها هم جدا نمی‌شوند."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return _docx_regex_lines(xml_bytes)
    lines: list[str] = []
    for paragraph in root.iter(f"{_W}p"):
        pieces: list[str] = []

        def walk(element) -> None:
            for child in element:
                if child.tag == f"{_W}p":  # پاراگراف تودرتو (تکست‌باکس) جدا پردازش می‌شود
                    continue
                if child.tag == f"{_W}t":
                    pieces.append(child.text or "")
                elif child.tag == f"{_W}tab":
                    pieces.append("\t")
                elif child.tag in (f"{_W}br", f"{_W}cr"):
                    pieces.append("\n")
                walk(child)

        walk(paragraph)
        line = "".join(pieces).strip()
        if line:
            lines.append(line)
    return lines


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        body_parts = [
            name
            for name in names
            if re.fullmatch(r"word/(document|footnotes|endnotes)\.xml", name)
        ]
        if not body_parts:
            raise UnsupportedFormatError("ساختار فایل DOCX قابل خواندن نبود.")
        body_parts.sort(key=lambda name: 0 if name.endswith("document.xml") else 1)
        chunks: list[str] = []
        for name in body_parts:
            chunks.extend(_docx_paragraph_lines(zf.read(name)))
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
        sheets = [n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
        # ترتیب عددی شیت‌ها، نه رشته‌ای (وگرنه ۱، ۱۰، ۱۱، ۲ می‌شود).
        sheets.sort(key=lambda n: int(re.search(r"sheet(\d+)\.xml$", n).group(1)))
        for name in sheets:
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


# گروه‌های RTF که متن محسوب نمی‌شوند (نام فونت، رنگ، استایل، تصویر، متادیتا).
_RTF_SKIP_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer",
    "headerl", "headerr", "headerf", "footerl", "footerr", "footerf",
    "listtable", "listoverridetable", "rsidtbl", "generator", "xmlnstbl",
    "themedata", "colorschememapping", "latentstyles", "datastore", "filetbl",
    "revtbl", "pgdsctbl", "bkmkstart", "bkmkend", "field", "fldinst", "object",
    "objdata", "template", "pn", "pntext", "wgrffmtfilter", "mmathPr",
}


def _rtf_text(path: Path) -> str:
    raw = path.read_bytes().decode("latin-1", errors="replace")
    codepage_match = re.search(r"\\ansicpg(\d+)", raw)
    codepage = int(codepage_match.group(1)) if codepage_match else 1256
    out: list[str] = []
    byte_buffer = bytearray()
    skip_stack: list[bool] = []

    def flush_bytes() -> None:
        if byte_buffer:
            try:
                out.append(byte_buffer.decode(f"cp{codepage}", errors="replace"))
            except LookupError:
                out.append(byte_buffer.decode("cp1256", errors="replace"))
            byte_buffer.clear()

    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char == "\\":
            nxt = raw[index + 1] if index + 1 < length else ""
            if nxt == "'":
                try:
                    byte_buffer.append(int(raw[index + 2:index + 4], 16))
                except ValueError:
                    pass
                index += 4
                continue
            if nxt in "\\{}":
                flush_bytes()
                out.append(nxt)
                index += 2
                continue
            if nxt == "~":
                flush_bytes()
                out.append("\u00a0")
                index += 2
                continue
            if nxt == "*":  # گروه مقصد ناشناخته
                flush_bytes()
                if skip_stack:
                    skip_stack[-1] = True
                index += 2
                continue
            word_match = re.match(r"([a-zA-Z]+)(-?\d+)? ?", raw[index + 1:])
            if word_match:
                name = word_match.group(1)
                argument = word_match.group(2)
                consumed = len(word_match.group(0)) + 1
                flush_bytes()
                if name == "u" and argument:
                    code = int(argument)
                    if code < 0:
                        code += 65536
                    out.append(chr(code))
                    index += consumed
                    # کاراکتر جایگزین بعد از \u (مثل ? یا \'hh) حذف می‌شود.
                    if raw[index:index + 1] == "?":
                        index += 1
                    elif raw[index:index + 2] == "\\'":
                        index += 4
                    continue
                if name in ("par", "line", "sect", "page"):
                    out.append("\n")
                elif name == "tab":
                    out.append("\t")
                index += consumed
                continue
            index += 1
            continue
        if char == "{":
            flush_bytes()
            lookahead = re.match(r"\\\*?\\?([a-zA-Z]+)", raw[index + 1:index + 40])
            skipped = bool(lookahead and lookahead.group(1).lower() in _RTF_SKIP_DESTINATIONS)
            skip_stack.append(skipped)
            index += 1
            continue
        if char == "}":
            flush_bytes()
            if skip_stack:
                skip_stack.pop()
            index += 1
            continue
        if any(skip_stack):
            index += 1
            continue
        if char in "\r\n":
            index += 1
            continue
        flush_bytes()
        out.append(char)
        index += 1

    flush_bytes()
    return _clean_plain("".join(out))


def _decode_hex_subtitle(text: str) -> str:
    """زیرنویس هگز (مثل SCC) را به متن خوانا تبدیل می‌کند."""
    pieces: list[str] = []
    for line in text.splitlines():
        for token in line.split():
            token = token.strip()
            if re.fullmatch(r"[0-9a-fA-F]{4,}", token):
                raw = bytes.fromhex(token if len(token) % 2 == 0 else token + "0")
                for chunk in re.findall(rb"[\x20-\x7e]{2,}", raw):
                    decoded = chunk.decode("latin-1", errors="replace").strip()
                    if decoded:
                        pieces.append(decoded)
    return "\n".join(pieces)


def _srt_like_blocks(text: str) -> list[str]:
    """SRT/VTT/SBV را بلوک‌به‌بلوک می‌خواند تا شماره و شناسه با متن قاطی نشود."""
    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if lines[0].strip().upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        # اگر خط اول شناسهٔ cue است و خط بعدش زمان‌بندی، شناسه دور ریخته می‌شود.
        if len(lines) > 1 and "-->" in lines[1] and "-->" not in lines[0]:
            lines = lines[1:]
        content = [line for line in lines if "-->" not in line]
        body = _strip_inline_markup("\n".join(content))
        if body:
            blocks.append(body)
    return blocks


def _subtitle_text(path: Path, ext: str) -> str:
    text = decode_text(path.read_bytes()).replace("\ufeff", "")
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
    if ext == ".sub":
        # MicroDVD: {start}{end}متن با | به‌جای خط جدید
        lines = []
        for line in text.splitlines():
            match = re.match(r"^\s*\{\d+\}\{\d+\}(.*)$", line)
            if not match:
                continue
            body = match.group(1).replace("|", "\n")
            body = _strip_inline_markup(body)
            if body:
                lines.append(body)
        if lines:
            return "\n".join(lines)
        return _decode_hex_subtitle(text)
    if ext in {".smi", ".sami"}:
        body = re.sub(r"(?is)<sync\b[^>]*>", "\n", text)
        body = _strip_html(body)
        body = re.sub(r"(?im)^\s*(&nbsp;)+.*$", "", body)
        return _clean_plain(body)
    if ext == ".sbv":
        lines = []
        for line in text.splitlines():
            if "-->" in line or re.fullmatch(r"[\d:.,\s]+", line.strip()):
                continue
            body = _strip_inline_markup(line)
            if body:
                lines.append(body)
        return "\n".join(lines)
    return "\n".join(_srt_like_blocks(text))


def _notebook_text(path: Path) -> str:
    """فقط سلول‌های متن (markdown/raw) را برمی‌گرداند؛ کد ترجمه نمی‌شود."""
    try:
        data = json.loads(decode_text(path.read_bytes()))
    except json.JSONDecodeError as exc:
        raise UnsupportedFormatError(f"فایل Jupyter معتبر نبود: {exc}") from exc
    cells = []
    for cell in data.get("cells", []):
        if cell.get("cell_type") not in {"markdown", "raw"}:
            continue
        source = cell.get("source", "")
        body = "".join(source) if isinstance(source, list) else str(source)
        if body.strip():
            cells.append(body.strip())
    if not cells:
        raise UnsupportedFormatError(
            "این نوت‌بوک سلول متنی (markdown) ندارد؛ کد برنامه ترجمه نمی‌شود."
        )
    return "\n\n".join(cells)


def _fb2_text(path: Path) -> tuple[str, str]:
    """FB2: متن کتاب بدون عکس‌های base64 و بدون چسبیدن عنوان به پاراگراف اول."""
    raw = decode_text(path.read_bytes())
    title = _tag_text(raw, "book-title")
    raw = re.sub(r"(?is)<binary\b.*?</binary>", " ", raw)
    raw = re.sub(r"(?is)<description\b.*?</description>", " ", raw)
    return title, _strip_html(raw)


def _mhtml_text(path: Path) -> str:
    """MHTML: بدنهٔ MIME را باز می‌کند و quoted-printable را دیکد می‌کند."""
    raw = path.read_bytes()
    try:
        message = email.message_from_bytes(raw, policy=policy.default)
    except Exception:
        return _strip_html(decode_text(raw))
    parts: list[str] = []
    for part in message.walk():
        content_type = part.get_content_type()
        if content_type not in {"text/html", "text/plain"}:
            continue
        try:
            body = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            body = decode_text(payload)
        if not isinstance(body, str):
            continue
        parts.append(_strip_html(body) if content_type == "text/html" else _clean_plain(body))
    if not parts:
        return _strip_html(decode_text(raw))
    return _clean_plain("\n\n".join(parts))


def _extract_nested_member(data: bytes, suffix: str) -> str:
    """عضو سند داخل زیپ را با همان استخراج‌کننده‌ها می‌خواند."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        temp_path = Path(handle.name)
    try:
        return GenericTextExtractor().extract(str(temp_path)).full_text
    except Exception:
        return ""
    finally:
        temp_path.unlink(missing_ok=True)


def _zip_bundle_text(path: Path) -> str:
    """آرشیو ZIP: فایل‌های متنی و اسناد داخلش را پشت‌سرهم می‌خواند."""
    blocks = []
    skipped: list[str] = []
    budget = MAX_TEXT_BYTES
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.file_size == 0 or info.file_size > budget:
                continue
            name = info.filename
            if "__MACOSX" in name or PurePosixPath(name).name.startswith("."):
                continue
            ext = PurePosixPath(name).suffix.lower()
            data = zf.read(info)
            if ext in NESTED_DOCUMENT_EXTENSIONS:
                nested = _extract_nested_member(data, ext)
                if nested.strip():
                    blocks.append((name, nested))
                    budget -= len(data)
                else:
                    skipped.append(name)
                continue
            if not (is_translatable_extension(ext) or ext in PLAIN_TEXT_EXTENSIONS):
                skipped.append(name)
                continue
            try:
                text = decode_text(data)
            except UnsupportedFormatError:
                skipped.append(name)
                continue
            if text.count("\x00") > 0 or _printable_ratio(text) < 0.9:
                skipped.append(name)
                continue
            budget -= len(data)
            blocks.append((name, text))
            if budget <= 0:
                break
    if not blocks:
        detail = ""
        if skipped:
            detail = f" فایل‌های داخلش: {', '.join(skipped[:8])}."
        raise UnsupportedFormatError(
            "داخل این آرشیو فایل متنی قابل ترجمه‌ای پیدا نشد." + detail
        )
    body = "\n\n".join(f"## {name}\n{text.strip()}" for name, text in blocks)
    if skipped:
        body += f"\n\n## فایل‌های ترجمه‌نشده\n{', '.join(skipped[:20])}"
    return body


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
    candidates = []
    # متن ASCII/لاین۱ (فایل‌های .doc قدیمی با متن انگلیسی)
    ascii_text = "\n".join(
        run.decode("latin-1", errors="replace").strip()
        for run in re.findall(rb"[\x20-\x7e\r\n\t]{4,}", data)
    )
    candidates.append(ascii_text)
    # متن UTF-16 (فایل‌های .doc قدیمی با متن فارسی/غیرلاتین)
    if _utf16_without_bom(data) is not None or data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            utf16 = decode_text(data)
        except UnsupportedFormatError:
            utf16 = ""
        runs = re.findall(r"[^\x00-\x08\x0b-\x1f\x7f]{4,}", utf16)
        candidates.append("\n".join(run.strip() for run in runs))
    best = max(candidates, key=lambda text: sum(1 for ch in text if ch.isalpha()))
    text = re.sub(r"\n{3,}", "\n\n", best).strip()
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
_AUDIO_FTYP_BRANDS = (b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B ", b"mp4a")


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
        if brand in _AUDIO_FTYP_BRANDS:
            return "audio"
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

        size = path.stat().st_size
        if size > MAX_TEXT_BYTES and ext not in {".pdf"} and container != "pdf":
            raise UnsupportedFormatError(
                f"«{path.name}» {size / (1024 * 1024):.1f} مگابایت است و از سقف "
                f"{MAX_TEXT_BYTES // (1024 * 1024)} مگابایتی تراریوم بزرگ‌تر است. "
                "برای اینکه متنی بی‌صدا حذف نشود، فایل را به بخش‌های کوچک‌تر تقسیم کنید."
            )

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
        if ext in COMIC_EXTENSIONS:
            raise UnsupportedFormatError(
                f"«{path.name}» یک کتاب کمیک/آرشیو تصویری است و متن قابل ترجمه ندارد؛ "
                "داخلش فقط عکس است. اگر زیرنویس یا متن آن را دارید، همان را بفرستید."
            )
        if ext in PACKAGE_EXTENSIONS:
            raise UnsupportedFormatError(
                f"«{path.name}» یک بستهٔ نرم‌افزاری/اجرایی است و متن قابل ترجمه ندارد. "
                "اگر داخلش فایل متنی هست، ابتدا از حالت فشرده خارج کنید."
            )
        if ext in ARCHIVE_EXTENSIONS and container in {"archive", "executable", "binary"}:
            raise UnsupportedFormatError(
                f"«{path.name}» یک فایل فشرده/اجرایی است و متن قابل ترجمه ندارد. "
                "اگر داخلش فایل متنی هست، ابتدا از حالت فشرده خارج کنید."
            )
        if ext in BINARY_ONLY_EXTENSIONS:
            raise UnsupportedFormatError(
                f"«{path.name}» یک فایل باینری/تخصصی است و متن قابل استخراجی ندارد. "
                "این پسوند در فهرست تراریوم شناسایی می‌شود، اما محتوایش متن نیست؛ "
                "لطفاً نسخهٔ متنی، PDF یا DOCX آن را بفرستید."
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
        elif ext == ".fb2" or (container == "text" and ext in {".fb2"}):
            fb2_title, text = _fb2_text(path)
            title = fb2_title or title
            strategy = "کتاب FB2"
        elif ext in {".mhtml", ".mht"}:
            text, strategy = _mhtml_text(path), "صفحهٔ MHTML"
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
