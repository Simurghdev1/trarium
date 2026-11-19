"""ترجمهٔ اسناد Word/PowerPoint/Excel با حفظ ساختار و قالب اصلی.

به‌جای ساختن یک سند تازه (که صفحه‌آرایی را از بین می‌برد)، بستهٔ اصلی کپی
می‌شود و فقط متن پاراگراف‌ها/سلول‌ها با نسخهٔ ترجمه‌شده جایگزین می‌شود.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, unescape

SUPPORTED_EXTENSIONS = {".docx", ".pptx", ".xlsx"}

# (الگوی شروع بلوک، الگوی پایان بلوک، الگوی تگ متنی) برای هر فرمت
_RULES = {
    ".docx": (r"<w:p\b[^>]*>", r"</w:p\s*>", r"<w:t\b[^>]*>(.*?)</w:t\s*>"),
    ".pptx": (r"<a:p\b[^>]*>", r"</a:p\s*>", r"<a:t\b[^>]*>(.*?)</a:t\s*>"),
    ".xlsx": (r"<si\b[^>]*>", r"</si\s*>", r"<t\b[^>]*>(.*?)</t\s*>"),
}

# بخش‌هایی از بسته که متن دارند (بقیهٔ فایل‌ها دست‌نخورده کپی می‌شوند).
_TARGET_PARTS = {
    ".docx": re.compile(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$"),
    ".pptx": re.compile(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml$"),
    ".xlsx": re.compile(r"xl/sharedStrings\.xml$"),
}


def _iter_blocks(xml: str, open_re: str, close_re: str):
    """بلوک‌های هم‌سطح را با شمارش عمق پیدا می‌کند (پاراگراف‌های تودرتو هم امن است)."""
    pattern = re.compile(f"{open_re}|{close_re}")
    depth = 0
    start = 0
    for match in pattern.finditer(xml):
        token = match.group(0)
        if token.startswith("</"):
            depth -= 1
            if depth <= 0:
                depth = 0
                yield start, match.end()
        else:
            if token.rstrip().endswith("/>"):
                continue
            if depth == 0:
                start = match.start()
            depth += 1


def _segment_blocks(xml: str, suffix: str):
    """بلوک‌ها و متن هر بلوک را جدا می‌کند تا بتوان همهٔ اسناد را یک‌جا ترجمه کرد."""
    open_re, close_re, text_re = _RULES[suffix]
    blocks = list(_iter_blocks(xml, open_re, close_re))
    if not blocks:
        return [], []

    infos: list[tuple[int, int, str, list[tuple[int, int, str]]]] = []
    segments: list[str] = []
    for start, end in blocks:
        block = xml[start:end]
        matches = [
            (match.start(1), match.end(1), unescape(match.group(1)))
            for match in re.finditer(text_re, block, flags=re.S)
        ]
        matches = [item for item in matches if item[2].strip()]
        infos.append((start, end, block, matches))
        if matches:
            # run‌ها تکه‌های یک پاراگراف‌اند و فاصله‌شان داخل خود متن است.
            segments.append("".join(item[2] for item in matches))
    return infos, segments


def rewrite_part(xml: str, suffix: str, translate) -> str:
    """متن هر بلوک را ترجمه و در همان بلوک (در اولین run) جایگزین می‌کند."""
    infos, segments = _segment_blocks(xml, suffix)
    if not segments:
        return xml
    translated = list(translate(segments))
    return _apply_blocks(xml, infos, segments, translated)


def _apply_blocks(xml: str, infos, segments, translated) -> str:
    if len(translated) < len(segments):
        translated = list(translated) + segments[len(translated):]

    output: list[str] = []
    cursor = 0
    position = 0
    for start, end, block, matches in infos:
        output.append(xml[cursor:start])
        if not matches:
            output.append(block)
        else:
            value = translated[position].strip() or matches[0][2]
            position += 1
            pieces: list[str] = []
            last = 0
            for index, (m_start, m_end, original) in enumerate(matches):
                pieces.append(block[last:m_start])
                pieces.append(escape(value) if index == 0 else "")
                last = m_end
            pieces.append(block[last:])
            output.append("".join(pieces))
        cursor = end
    output.append(xml[cursor:])
    return "".join(output)


def translate_office(source: str | Path, suffix: str, pipeline) -> bytes:
    """یک کپی ترجمه‌شده از سند می‌سازد؛ همهٔ بخش‌های دیگر بایت‌به‌بایت می‌مانند."""
    from trarium.structured import translate_segments

    source = Path(source)
    target = _TARGET_PARTS[suffix]
    with zipfile.ZipFile(source) as src:
        items = [(item, src.read(item.filename)) for item in src.infolist()]

    # Collect every text block of the document first: a 10-slide deck used to
    # trigger 10 separate API calls, one per slide file.
    pending: dict[str, tuple[str, list, list]] = {}
    all_segments: list[str] = []
    for item, data in items:
        if not target.search(item.filename):
            continue
        xml = data.decode("utf-8", errors="replace")
        infos, segments = _segment_blocks(xml, suffix)
        if segments:
            pending[item.filename] = (xml, infos, segments)
            all_segments.extend(segments)

    translated_all = translate_segments(pipeline, all_segments) if all_segments else []
    rendered: dict[str, bytes] = {}
    offset = 0
    for name, (xml, infos, segments) in pending.items():
        chunk = list(translated_all[offset:offset + len(segments)])
        offset += len(segments)
        rendered[name] = _apply_blocks(xml, infos, segments, chunk).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as dst:
        for item, data in items:
            dst.writestr(item, rendered.get(item.filename, data))
    return buffer.getvalue()
