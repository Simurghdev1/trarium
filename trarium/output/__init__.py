"""Output helpers for text, PDF, EPUB and HTML results."""
from __future__ import annotations

import html
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from rich.console import Console

console = Console()
_FONT_PATH = Path(__file__).parent.parent / "fonts" / "Sahel.ttf"
ATTRIBUTION_TEXT = "ترجمه شده توسط تراریوم"


def _safe_name(value: str, fallback: str = "translated") -> str:
    name = "".join(c for c in (value or "") if c.isalnum() or c in " _-").strip()
    return name or fallback


def save_as_text(result, output_path: str | None = None) -> str:
    path = Path(output_path or f"{_safe_name(result.title)}_translated.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = result.translated_text.rstrip() + "\n\n" + ("=" * 64) + "\n" + ATTRIBUTION_TEXT + "\n"
    path.write_text(content, encoding="utf-8")
    return str(path)


def save_as_html(result, output_path: str | None = None) -> str:
    path = Path(output_path or f"{_safe_name(result.title)}_translated.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = []
    for section in re.split(r"\n---\s*", result.translated_text):
        section = section.strip()
        if not section:
            continue
        lines = section.split("\n", 1)
        title = lines[0].strip().strip("-")
        content = lines[1] if len(lines) > 1 else ""
        paras = "".join(f"<p>{html.escape(p.strip())}</p>" for p in re.split(r"\n{2,}", content) if p.strip())
        body.append(f"<section><h2>{html.escape(title)}</h2>{paras}</section>")
    document = f'''<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(result.title)}</title><style>
@font-face{{font-family:Sahel;src:url(data:font/ttf;base64,{_font_data()})}}
body{{font-family:Sahel,Tahoma,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;background:#f6f8f7;color:#1f2b2a;line-height:2.05}}
main{{background:white;padding:36px;border-radius:18px;box-shadow:0 8px 35px rgba(0,0,0,.06)}}h1{{text-align:center}}h2{{margin-top:2em}}p{{white-space:pre-wrap}}.attribution{{min-height:70vh;display:flex;align-items:center;justify-content:center;page-break-before:always;font-size:2rem;font-weight:800;text-align:center;color:#c20c0c}}
</style></head><body><main><h1>{html.escape(result.title)}</h1>{''.join(body)}<div class="attribution">{html.escape(ATTRIBUTION_TEXT)}</div></main></body></html>'''
    path.write_text(document, encoding="utf-8")
    return str(path)


def _font_data() -> str:
    import base64
    if _FONT_PATH.exists():
        return base64.b64encode(_FONT_PATH.read_bytes()).decode("ascii")
    return ""


def _wrap_lines(font, text: str, width: float, size: float) -> list[str]:
    """Break text into lines that actually fit, measured with the real font."""
    lines: list[str] = []
    for raw in text.split("\n"):
        current = ""
        for word in raw.split(" "):
            candidate = word if not current else current + " " + word
            if font.text_length(candidate, size) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            while font.text_length(current, size) > width and len(current) > 1:
                cut = len(current)
                while cut > 1 and font.text_length(current[:cut], size) > width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return [line for line in lines if line.strip()] or [""]


def save_as_pdf(result, output_path: str | None = None) -> str:
    """Render a clean RTL PDF using the bundled Sahel font.

    Pagination is measured with the embedded font instead of a character-count
    guess: the previous heuristic silently dropped whole paragraphs (a 2000-char
    paragraph produced 19 visible letters).
    """
    import pymupdf

    path = Path(output_path or f"{_safe_name(result.title)}_translated.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _FONT_PATH.exists():
        raise RuntimeError(f"فونت Sahel پیدا نشد: {_FONT_PATH}")

    rtl = (getattr(result, "target_lang", "fa") or "fa") in {"fa", "ar", "he", "ur"}
    font = pymupdf.Font(fontfile=str(_FONT_PATH))
    width, height, margin = 595, 842, 52
    body_size, leading = 12.5, 19.0
    usable = width - 2 * margin

    doc = pymupdf.open()

    def new_page():
        page = doc.new_page(width=width, height=height)
        page.insert_font(fontname="Sahel", fontfile=str(_FONT_PATH))
        return page

    page = new_page()
    y = margin

    def write(text: str, size: float, color, space_after: float):
        nonlocal page, y
        display = _reshape_for_pdf(text) if rtl else text
        for line in _wrap_lines(font, display, usable, size):
            if y + size * 1.6 > height - margin:
                page = new_page()
                y = margin
            x = (width - margin - font.text_length(line, size)) if rtl else margin
            page.insert_text((x, y + size), line, fontname="Sahel", fontfile=str(_FONT_PATH),
                             fontsize=size, color=color)
            y += size * 1.52
        y += space_after

    sections = [s.strip() for s in re.split(r"\n---\s*", result.translated_text or "") if s.strip()]
    if not sections:
        sections = [result.translated_text or ""]

    for section in sections:
        lines = section.split("\n", 1)
        title = lines[0].strip().strip("-").strip()
        content = lines[1] if len(lines) > 1 else ""
        if title:
            # The title is reshaped too; otherwise Persian headings render as
            # disconnected, reversed letters even though the body is correct.
            write(title, 15.0, (0.05, 0.28, 0.23), 6.0)
        for para in [p.strip() for p in re.split(r"\n{2,}", content) if p.strip()]:
            write(para, body_size, (0.10, 0.10, 0.10), 8.0)

    attr_page = new_page()
    attr_rect = pymupdf.Rect(margin, height * 0.40, width - margin, height * 0.60)
    if hasattr(attr_page, "insert_htmlbox"):
        archive = pymupdf.Archive(str(_FONT_PATH.parent))
        css = "@font-face{font-family:Sahel;src:url('Sahel.ttf')} body{font-family:Sahel;font-size:22pt;text-align:center;direction:rtl;color:#c20c0c;margin:0;}"
        try:
            spare, _scale = attr_page.insert_htmlbox(
                attr_rect, f"<body>{html.escape(ATTRIBUTION_TEXT)}</body>", css=css, archive=archive, scale_low=0.5
            )
        except Exception:
            spare = -1
        if spare < 0:
            attr_page.insert_textbox(attr_rect, _reshape_for_pdf(ATTRIBUTION_TEXT), fontname="Sahel",
                                     fontsize=22, color=(0.78, 0.03, 0.03), align=pymupdf.TEXT_ALIGN_CENTER)
    else:
        attr_page.insert_textbox(attr_rect, _reshape_for_pdf(ATTRIBUTION_TEXT), fontname="Sahel",
                                 fontsize=22, color=(0.78, 0.03, 0.03), align=pymupdf.TEXT_ALIGN_CENTER)

    doc.set_metadata({"title": result.title or "", "author": result.author or "", "producer": "Trarium"})
    doc.save(str(path), garbage=4, deflate=True, clean=True)
    doc.close()
    return str(path)


def _reshape_for_pdf(text: str) -> str:
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def save_as_epub(result, output_path: str | None = None) -> str:
    path = Path(output_path or f"{_safe_name(result.title)}_translated.epub")
    path.parent.mkdir(parents=True, exist_ok=True)
    book_id = str(uuid.uuid4())
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = xml_escape(result.title or "Trarium")

    sections = [s.strip() for s in re.split(r"\n---\s*", result.translated_text) if s.strip()]
    chapters = []
    for i, section in enumerate(sections, 1):
        lines = section.split("\n", 1)
        ch_title = lines[0].strip().strip("-") or f"فصل {i}"
        content = lines[1] if len(lines) > 1 else ""
        chapters.append((i, ch_title, content))
    chapters.append((len(chapters) + 1, ATTRIBUTION_TEXT, ""))

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>', '<item id="css" href="style.css" media-type="text/css"/>']
        if _FONT_PATH.exists():
            # The font file was bundled but never declared, so readers ignored @font-face.
            manifest.append('<item id="font" href="fonts/Sahel.ttf" media-type="font/ttf"/>')
        spine = []
        nav = []
        for i, ch_title, _ in chapters:
            href = f"chapter_{i}.xhtml"
            manifest.append(f'<item id="c{i}" href="{href}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
            nav.append(f'<li><a href="{href}">{xml_escape(ch_title)}</a></li>')
        opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="bookid">urn:uuid:{book_id}</dc:identifier><dc:title>{title}</dc:title><dc:language>{xml_escape(result.target_lang or 'fa')}</dc:language><meta property="dcterms:modified">{modified}</meta></metadata><manifest>{''.join(manifest)}</manifest><spine>{''.join(spine)}</spine></package>'''
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/style.css", "@font-face{font-family:Sahel;src:url(fonts/Sahel.ttf)}body{font-family:Sahel,Tahoma,sans-serif;direction:rtl;text-align:right;line-height:1.9;margin:1.5em}h1{text-align:center}p{margin:.6em 0}")
        zf.writestr("OEBPS/nav.xhtml", f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" dir="rtl"><head><title>{title}</title></head><body><nav epub:type="toc"><ol>{"".join(nav)}</ol></nav></body></html>')
        if _FONT_PATH.exists():
            zf.write(_FONT_PATH, "OEBPS/fonts/Sahel.ttf")
        for i, ch_title, content in chapters:
            paras = "".join(f"<p>{xml_escape(p.strip())}</p>" for p in re.split(r"\n{2,}", content) if p.strip())
            zf.writestr(f"OEBPS/chapter_{i}.xhtml", f'<html xmlns="http://www.w3.org/1999/xhtml" dir="rtl"><head><title>{xml_escape(ch_title)}</title><link rel="stylesheet" href="style.css"/></head><body><h1>{xml_escape(ch_title)}</h1>{paras}</body></html>')
    return str(path)
