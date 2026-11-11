"""ذخیره نتیجه ترجمه در فایل."""

import re
import uuid
import zipfile
from pathlib import Path
from datetime import datetime
from xml.sax.saxutils import escape as xml_escape
from rich.console import Console

from trarium.utils import is_rtl_lang, reshape_rtl

console = Console()

_FONT_DIR = Path(__file__).parent.parent / "fonts"
_FONT_PATH = _FONT_DIR / "Sahel.ttf"
_FONT_NAME = "Sahel"


def _safe_stem(title: str) -> str:
    return "".join(c for c in (title or "") if c.isalnum() or c in " _-").strip() or "translated"


def save_as_text(result, output_path: str = None) -> str:
    if output_path is None:
        output_path = f"{_safe_stem(result.title)}_translated.txt"
    path = Path(output_path)
    path.write_text(f"{result.translated_text}\n\n---\nترجمه شده با Trarium\n", encoding="utf-8")
    console.print(f"  💾 ذخیره شد: [bold green]{path}[/] ({path.stat().st_size:,} بایت)")
    return str(path)


def _wrap_lines(font, text: str, width: float, fontsize: float) -> list[str]:
    """شکستن متن به خطوطی که با عرض داده‌شده جا می‌شوند."""
    lines: list[str] = []
    for raw in text.split("\n"):
        words = raw.split(" ")
        current = ""
        for word in words:
            trial = word if not current else current + " " + word
            if font.text_length(trial, fontsize) <= width:
                current = trial
                continue
            if current:
                lines.append(current)
            current = word
            # کلمهٔ بسیار بلند (URL یا واژهٔ بدون فاصله) را هم بشکن
            while font.text_length(current, fontsize) > width and len(current) > 1:
                cut = len(current)
                while cut > 1 and font.text_length(current[:cut], fontsize) > width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
        lines.append(current)
    return [line for line in lines if line != ""] or [""]


def save_as_pdf(result, output_path: str = None) -> str:
    """ترجمه را به PDF می‌نویسد — با فونت یونیکد فارسی و چیدمان راست‌به‌چپ."""
    import pymupdf

    if output_path is None:
        output_path = f"{_safe_stem(result.title)}_translated.pdf"

    if not _FONT_PATH.exists():
        raise RuntimeError(f"فونت فارسی پیدا نشد: {_FONT_PATH}")

    path = Path(output_path)
    rtl = is_rtl_lang(getattr(result, "target_lang", "fa"))
    font = pymupdf.Font(fontfile=str(_FONT_PATH))

    page_w, page_h = 595.0, 842.0
    margin, fontsize, leading = 56.0, 12.0, 19.0
    usable = page_w - 2 * margin

    doc = pymupdf.open()
    page = doc.new_page(width=page_w, height=page_h)
    y = margin

    paragraphs = [p.strip() for p in (result.translated_text or "").split("\n") if p.strip()]
    if not paragraphs:
        paragraphs = [""]

    for para in paragraphs:
        # برای فارسی/عربی متن پیش از نوشتن به ترتیب بصری تبدیل می‌شود؛
        # فونت Sahel همراه فایل نوشته می‌شود تا هیچ گلیفی گم نشود.
        display = reshape_rtl(para) if rtl else para
        for line in _wrap_lines(font, display, usable, fontsize):
            if y + leading > page_h - margin:
                page = doc.new_page(width=page_w, height=page_h)
                y = margin
            if line:
                x = (page_w - margin - font.text_length(line, fontsize)) if rtl else margin
                page.insert_text(
                    (x, y + fontsize),
                    line,
                    fontname=_FONT_NAME,
                    fontfile=str(_FONT_PATH),
                    fontsize=fontsize,
                    color=(0.13, 0.13, 0.13),
                )
            y += leading
        y += leading * 0.4

    doc.set_metadata({
        "title": result.title or "",
        "author": result.author or "",
        "producer": "Trarium",
    })
    doc.save(str(path))
    doc.close()
    console.print(f"  💾 ذخیره شد: [bold green]{path}[/] ({path.stat().st_size:,} بایت)")
    return str(path)


def save_as_epub(result, output_path: str = None) -> str:
    """ترجمه رو به EPUB ذخیره می‌کنه."""
    if output_path is None:
        output_path = f"{_safe_stem(result.title)}_translated.epub"

    path = Path(output_path)
    book_id = str(uuid.uuid4())
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    title_safe = xml_escape(result.title or "")
    lang = getattr(result, "target_lang", "fa") or "fa"
    direction = "rtl" if is_rtl_lang(lang) else "ltr"

    # مسیر فونت نسبت به OEBPS/style.css نوشته می‌شود (fonts/Sahel.ttf)؛
    # «../fonts» به بیرون از OEBPS اشاره می‌کرد و فونت هرگز بارگذاری نمی‌شد.
    css = (b"@font-face{font-family:Sahel;src:url(fonts/Sahel.ttf)} "
           b"body{font-family:Sahel,Tahoma,Arial,sans-serif;line-height:1.8;margin:2em;"
           b"color:#333;direction:RTL_DIR;text-align:ALIGN_TEXT}\n"
           b"h1{text-align:center;color:#2c3e50}\n"
           b"h2{color:#34495e}\n"
           b"p{margin:.5em 0;text-align:ALIGN_TEXT}").replace(
        b"RTL_DIR", direction.encode()
    ).replace(b"ALIGN_TEXT", b"right" if direction == "rtl" else b"left")

    sections = [s.strip() for s in re.split(r"\n---\s+", result.translated_text or "") if s.strip()]
    chapter_files, nav_points, manifest_items, spine_refs = [], "", "", ""

    for i, section in enumerate(sections):
        lines = section.split("\n", 1)
        ch_title = xml_escape(lines[0].strip().replace("---", "").strip() or f"بخش {i+1}")
        fname = f"ch{i+1}.xhtml"
        chapter_files.append(fname)
        nav_points += f'<navPoint id="np{i+1}" playOrder="{i+1}"><navLabel><text>{ch_title}</text></navLabel><content src="{fname}"/></navPoint>\n'
        manifest_items += f'<item id="ch{i+1}" href="{fname}" media-type="application/xhtml+xml"/>\n'
        spine_refs += f'<itemref idref="ch{i+1}"/>\n'

    titles_list = [s.split("\n", 1)[0].strip().replace("---", "").strip() or f"بخش {j+1}" for j, s in enumerate(sections)]
    nav_li = "".join(f'<li><a href="{chapter_files[i]}">{xml_escape(titles_list[i])}</a></li>\n' for i in range(len(titles_list)))

    font_item = '<item id="font" href="fonts/Sahel.ttf" media-type="font/ttf"/>'

    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr("OEBPS/content.opf", f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="BookId">urn:uuid:{book_id}</dc:identifier><dc:title>{title_safe}</dc:title><dc:language>{lang}</dc:language><dc:creator>Trarium</dc:creator><meta property="dcterms:modified">{now}</meta></metadata><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="css" href="style.css" media-type="text/css"/>{font_item}<item id="tp" href="title.xhtml" media-type="application/xhtml+xml"/>{manifest_items}<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine toc="ncx"><itemref idref="tp"/>{spine_refs}</spine></package>')
        zf.writestr("OEBPS/toc.ncx", f'<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><head><meta name="dtb:uid" content="urn:uuid:{book_id}"/></head><docTitle><text>{title_safe}</text></docTitle><navMap>{nav_points}</navMap></ncx>')
        zf.writestr("OEBPS/style.css", css)
        if _FONT_PATH.exists():
            zf.write(str(_FONT_PATH), "OEBPS/fonts/Sahel.ttf")
        zf.writestr("OEBPS/title.xhtml", f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" dir="{direction}"><head><title>{title_safe}</title><link rel="stylesheet" href="style.css"/></head><body><h1>{title_safe}</h1></body></html>'.encode("utf-8"))
        zf.writestr("OEBPS/nav.xhtml", f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" dir="{direction}"><head><title>فهرست</title></head><body><nav epub:type="toc"><h1>فهرست</h1><ol>{nav_li}</ol></nav></body></html>'.encode("utf-8"))
        for i, section in enumerate(sections):
            lines = section.split("\n", 1)
            ch_title = xml_escape(lines[0].strip().replace("---", "").strip() or f"بخش {i+1}")
            content = lines[1].strip() if len(lines) > 1 else ""
            paras = "".join(f"<p>{xml_escape(p.strip())}</p>\n" for p in content.split("\n") if p.strip())
            zf.writestr(f"OEBPS/{chapter_files[i]}", f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" dir="{direction}"><head><title>{ch_title}</title><link rel="stylesheet" href="style.css"/></head><body><h2>{ch_title}</h2>{paras}</body></html>'.encode("utf-8"))

    console.print(f"  💾 ذخیره شد: [bold green]{path}[/] ({path.stat().st_size:,} بایت)")
    return str(path)
