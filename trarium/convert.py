"""تبدیل بین PDF، EPUB، DOCX، HTML و TXT با حفظ ساختار تا حد امکان.

فایل ورودی هرگز تغییر نمی‌کند و خروجی همیشه در مسیر تازه ساخته می‌شود.
عنوان‌ها، پاراگراف‌ها و جدول‌ها به ساختار میانی منتقل و از آنجا دوباره ساخته
می‌شوند؛ متن فارسی راست‌چین می‌ماند و گزارش می‌گوید چه چیزی حفظ نشده است.
"""
from __future__ import annotations

import html as html_module
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

SUPPORTED_TARGETS = ("pdf", "epub", "docx", "html", "txt")
FORMAT_LABELS = {"pdf": "PDF", "epub": "EPUB", "docx": "DOCX", "html": "HTML", "txt": "TXT"}
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg")


@dataclass
class ConversionDocument:
    title: str = "تبدیل‌شده"
    author: str = ""
    blocks: list[dict] = field(default_factory=list)
    images: int = 0
    tables: int = 0
    headings: int = 0

    @property
    def chars(self) -> int:
        total = 0
        for block in self.blocks:
            if block.get("type") == "table":
                total += sum(len(str(cell)) for row in block.get("rows") or [] for cell in row)
            else:
                total += len(str(block.get("text") or ""))
        return total


@dataclass
class ConversionReport:
    input_format: str
    output_format: str
    title: str
    output_path: str
    blocks: int
    headings: int
    tables: int
    images: int
    chars: int
    warnings: list[str] = field(default_factory=list)
    source_unchanged: bool = True

    def to_dict(self) -> dict:
        return {
            "input_format": self.input_format, "output_format": self.output_format,
            "title": self.title, "output_path": self.output_path, "blocks": self.blocks,
            "headings": self.headings, "tables": self.tables, "images": self.images,
            "chars": self.chars, "warnings": self.warnings,
            "source_unchanged": self.source_unchanged,
        }


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


# --------------------------------------------------------------------- readers
def read_docx(path: Path) -> ConversionDocument:
    from lxml import etree

    document = ConversionDocument(title=path.stem)
    with zipfile.ZipFile(path) as archive:
        try:
            xml = archive.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("این فایل DOCX سند اصلی را ندارد.") from exc
        names = archive.namelist()
    document.images = sum(1 for name in names if name.startswith("word/media/"))
    root = etree.fromstring(xml)
    body = root.find(".//{*}body")
    if body is None:
        return document
    for child in body:
        name = _local(child.tag)
        if name == "p":
            texts = [node.text or "" for node in child.iter() if _local(node.tag) == "t"]
            text = "".join(texts).strip()
            if not text:
                continue
            style = ""
            for node in child.iter():
                if _local(node.tag) == "pStyle":
                    style = str(node.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val")
                                or node.get("val") or "")
                    break
            level = 0
            match = re.search(r"heading\s*([1-6])", style, re.IGNORECASE)
            if match:
                level = int(match.group(1))
            elif style.lower() in {"title", "subtitle"}:
                level = 1
            if level:
                document.blocks.append({"type": "heading", "text": text, "level": level})
                document.headings += 1
            else:
                document.blocks.append({"type": "paragraph", "text": text})
        elif name == "tbl":
            rows = []
            for row in child.iter():
                if _local(row.tag) != "tr":
                    continue
                cells = []
                for cell in row:
                    if _local(cell.tag) != "tc":
                        continue
                    cells.append(" ".join((node.text or "") for node in cell.iter()
                                          if _local(node.tag) == "t").strip())
                rows.append(cells)
            rows = [row for row in rows if any(cell for cell in row)]
            if rows:
                document.blocks.append({"type": "table", "rows": rows})
                document.tables += 1
    return document


def read_html(path: Path) -> ConversionDocument:
    from bs4 import BeautifulSoup

    raw = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw, "lxml")
    document = ConversionDocument(title=(soup.title.string.strip() if soup.title and soup.title.string
                                         else path.stem))
    document.images = len(soup.find_all("img"))
    for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre",
                                  "blockquote", "div"]):
        if element.find_parent("table") is not None and element.name != "table":
            continue
        # An element nested in another block is already covered by it; keeping both
        # produced duplicated paragraphs (e.g. <li><p>text</p></li>).
        if element.find_parent(["p", "li", "pre", "blockquote"]) is not None:
            continue
        if element.name == "div" and element.find(["p", "li", "table", "pre", "blockquote", "div"]) is not None:
            continue
        if element.name == "table":
            rows = []
            for row in element.find_all("tr"):
                cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
                if any(cells):
                    rows.append(cells)
            if rows:
                document.blocks.append({"type": "table", "rows": rows})
                document.tables += 1
            continue
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name.startswith("h"):
            document.blocks.append({"type": "heading", "text": text,
                                    "level": int(element.name[1])})
            document.headings += 1
        else:
            document.blocks.append({"type": "paragraph", "text": text})
    return document


def read_txt(path: Path) -> ConversionDocument:
    raw = path.read_text(encoding="utf-8", errors="replace")
    document = ConversionDocument(title=path.stem)
    for chunk in re.split(r"\n\s*\n", raw):
        text = chunk.strip()
        if not text:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", text)
        if heading:
            document.blocks.append({"type": "heading", "text": heading.group(2).strip(),
                                    "level": len(heading.group(1))})
            document.headings += 1
        else:
            document.blocks.append({"type": "paragraph", "text": text})
    return document


def read_pdf(path: Path) -> ConversionDocument:
    import pymupdf

    document = ConversionDocument(title=path.stem)
    pdf = pymupdf.open(str(path))
    try:
        sizes: list[float] = []
        for page in pdf:
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if str(span.get("text") or "").strip():
                            sizes.append(float(span.get("size") or 0))
        body = sum(sizes) / len(sizes) if sizes else 11.0
        for page_index, page in enumerate(pdf, 1):
            document.images += len(page.get_images(full=True))
            for block in page.get_text("dict").get("blocks", []):
                lines = []
                largest = 0.0
                for line in block.get("lines", []):
                    text = "".join(str(span.get("text") or "") for span in line.get("spans", []))
                    if text.strip():
                        lines.append(text)
                    for span in line.get("spans", []):
                        largest = max(largest, float(span.get("size") or 0))
                text = " ".join(lines).strip()
                if not text:
                    continue
                if largest >= body * 1.28 and len(text) < 120:
                    document.blocks.append({"type": "heading", "text": text, "level": 2})
                    document.headings += 1
                else:
                    document.blocks.append({"type": "paragraph", "text": text})
    finally:
        pdf.close()
    return document


def read_document(path: str | Path) -> ConversionDocument:
    path = Path(path)
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "docx":
        return read_docx(path)
    if suffix in {"html", "htm", "xhtml"}:
        return read_html(path)
    if suffix in {"txt", "md", "markdown", "text"}:
        return read_txt(path)
    if suffix == "pdf":
        return read_pdf(path)
    if suffix == "epub":
        return _read_epub(path)
    # بقیهٔ فرمت‌ها از مسیر استخراج متن معمولی خوانده می‌شوند.
    from trarium.extractors import get_extractor
    book = get_extractor(str(path), use_ocr=False).extract(str(path))
    document = ConversionDocument(title=book.title or path.stem, author=book.author or "")
    for chapter in book.chapters:
        title = str(chapter.get("title") or "").strip()
        if title:
            document.blocks.append({"type": "heading", "text": title, "level": 2})
            document.headings += 1
        for paragraph in re.split(r"\n\s*\n", str(chapter.get("content") or "")):
            if paragraph.strip():
                document.blocks.append({"type": "paragraph", "text": paragraph.strip()})
    return document


def _epub_spine_order(archive, names: list[str]) -> list[str]:
    """Chapter files in reading order (OPF spine) instead of alphabetical order."""
    documents = [name for name in names if name.lower().endswith((".xhtml", ".html", ".htm"))]
    try:
        import xml.etree.ElementTree as ET
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        opf_name = rootfile.get("full-path") if rootfile is not None else ""
        if not opf_name:
            return sorted(documents)
        opf = ET.fromstring(archive.read(opf_name))
        base = opf_name.rsplit("/", 1)[0] + "/" if "/" in opf_name else ""
        manifest = {}
        for item in opf.iter():
            if _local(item.tag) == "item" and item.get("id") and item.get("href"):
                manifest[item.get("id")] = base + item.get("href")
        ordered: list[str] = []
        for itemref in opf.iter():
            if _local(itemref.tag) != "itemref":
                continue
            target = manifest.get(itemref.get("idref"))
            if target and target in names:
                ordered.append(target)
        if ordered:
            return ordered
    except Exception:
        pass
    return sorted(documents)


def _read_epub(path: Path) -> ConversionDocument:
    from bs4 import BeautifulSoup

    document = ConversionDocument(title=path.stem)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        document.images = sum(1 for name in names if name.lower().endswith(_IMAGE_EXTENSIONS))
        pages = _epub_spine_order(archive, names)
        for name in pages:
            soup = BeautifulSoup(archive.read(name), "lxml")
            for tag in soup.find_all(["h1", "h2", "h3", "h4", "p", "table"]):
                if tag.name == "table":
                    rows = [[cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
                            for row in tag.find_all("tr")]
                    rows = [row for row in rows if any(row)]
                    if rows:
                        document.blocks.append({"type": "table", "rows": rows})
                        document.tables += 1
                    continue
                text = tag.get_text(" ", strip=True)
                if not text:
                    continue
                if tag.name.startswith("h"):
                    document.blocks.append({"type": "heading", "text": text,
                                            "level": int(tag.name[1])})
                    document.headings += 1
                else:
                    document.blocks.append({"type": "paragraph", "text": text})
    return document


# --------------------------------------------------------------------- writers
def write_txt(document: ConversionDocument, path: Path, rtl: bool) -> list[str]:
    warnings = []
    parts: list[str] = []
    for block in document.blocks:
        if block.get("type") == "heading":
            parts.append("#" * max(1, min(6, int(block.get("level") or 1))) + " " + block["text"])
        elif block.get("type") == "table":
            for row in block.get("rows") or []:
                parts.append(" | ".join(row))
        else:
            parts.append(str(block.get("text") or ""))
        parts.append("")
    path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")
    if document.tables:
        warnings.append("جدول‌ها در TXT فقط با ستون‌های متنی نوشته شدند.")
    if document.images:
        warnings.append(f"{document.images} تصویر در TXT قابل نگهداری نیست.")
    if rtl:
        warnings.append("راست‌چین‌بودن در TXT به خوانندهٔ فایل وابسته است.")
    return warnings


def write_html(document: ConversionDocument, path: Path, rtl: bool) -> list[str]:
    direction = "rtl" if rtl else "ltr"
    body: list[str] = []
    for block in document.blocks:
        if block.get("type") == "heading":
            level = max(1, min(6, int(block.get("level") or 1)))
            body.append(f"<h{level}>{html_module.escape(block['text'])}</h{level}>")
        elif block.get("type") == "table":
            rows = []
            for row in block.get("rows") or []:
                cells = "".join(f"<td>{html_module.escape(str(cell))}</td>" for cell in row)
                rows.append(f"<tr>{cells}</tr>")
            body.append("<table>" + "".join(rows) + "</table>")
        else:
            body.append(f"<p>{html_module.escape(str(block.get('text') or ''))}</p>")
    page = f"""<!doctype html>
<html lang="fa" dir="{direction}"><head><meta charset="utf-8">
<title>{html_module.escape(document.title)}</title>
<style>body{{font-family:Tahoma,Segoe UI,sans-serif;max-width:860px;margin:36px auto;padding:0 22px;
line-height:1.95;color:#1c1c1c}}table{{border-collapse:collapse;width:100%;margin:1.2em 0}}
td,th{{border:1px solid #ccc;padding:.45em .6em}}h1,h2,h3{{line-height:1.5}}</style></head>
<body>{''.join(body)}</body></html>"""
    path.write_text(page, encoding="utf-8")
    warnings = []
    if document.images:
        warnings.append(f"{document.images} تصویر در HTML جاسازی نشد؛ متن و ساختار حفظ شد.")
    return warnings


def write_docx(document: ConversionDocument, path: Path, rtl: bool) -> list[str]:
    _write_minimal_docx(document, path, rtl)
    warnings = []
    if document.images:
        warnings.append(f"{document.images} تصویر در DOCX جاسازی نشد؛ متن، عنوان و جدول حفظ شد.")
    return warnings


def write_epub(document: ConversionDocument, path: Path, rtl: bool) -> list[str]:
    from trarium.output import save_as_epub
    from trarium.processors.pipeline import TranslationResult

    body = _sections_text(document)
    result = TranslationResult(title=document.title, author=document.author, original_lang="auto",
                               target_lang="fa" if rtl else "en", translated_text=body,
                               total_chunks=1, translated_chunks=1, failed_chunks=0)
    save_as_epub(result, str(path))
    warnings = []
    if document.tables:
        warnings.append("جدول‌ها در EPUB به متن تبدیل می‌شوند.")
    if document.images:
        warnings.append(f"{document.images} تصویر در EPUB جاسازی نشد.")
    return warnings


def write_pdf(document: ConversionDocument, path: Path, rtl: bool) -> list[str]:
    from trarium.output import save_as_pdf
    from trarium.processors.pipeline import TranslationResult

    body = _sections_text(document)
    result = TranslationResult(title=document.title, author=document.author, original_lang="auto",
                               target_lang="fa" if rtl else "en", translated_text=body,
                               total_chunks=1, translated_chunks=1, failed_chunks=0)
    save_as_pdf(result, str(path))
    warnings = []
    if document.tables:
        warnings.append("جدول‌ها در PDF به متن تبدیل می‌شوند.")
    if document.images:
        warnings.append(f"{document.images} تصویر در PDF جاسازی نشد.")
    return warnings


def _sections_text(document: ConversionDocument) -> str:
    sections: list[str] = []
    current_title = ""
    current: list[str] = []
    for block in document.blocks:
        if block.get("type") == "heading":
            if current_title or current:
                sections.append(f"--- {current_title or document.title}\n\n" + "\n\n".join(current))
            current_title = block["text"]
            current = []
        elif block.get("type") == "table":
            for row in block.get("rows") or []:
                current.append(" | ".join(row))
        else:
            current.append(str(block.get("text") or ""))
    if current_title or current:
        sections.append(f"--- {current_title or document.title}\n\n" + "\n\n".join(current))
    return "\n\n".join(section for section in sections if section.strip())


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _write_minimal_docx(document: ConversionDocument, path: Path, rtl: bool) -> None:
    """DOCX معتبر و مستقل می‌سازد (بدون وابستگی تازه، فقط XML)."""
    paragraphs: list[str] = []
    for block in document.blocks:
        if block.get("type") == "table":
            rows_xml = []
            for row in block.get("rows") or []:
                cells = "".join(
                    f"<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr>"
                    f"<w:p>{_run(_xml_safe(str(cell)), rtl)}</w:p></w:tc>" for cell in row)
                rows_xml.append(f"<w:tr>{cells}</w:tr>")
            paragraphs.append(
                "<w:tbl><w:tblPr><w:tblW w:w=\"5000\" w:type=\"pct\"/></w:tblPr>"
                + "".join(rows_xml) + "</w:tbl><w:p/>")
            continue
        text = _xml_safe(str(block.get("text") or ""))
        if block.get("type") == "heading":
            level = max(1, min(6, int(block.get("level") or 1)))
            paragraphs.append(f'<w:p><w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>{_run(text, rtl)}</w:p>')
        else:
            paragraphs.append(f"<w:p><w:pPr>{_bidi(rtl)}</w:pPr>{_run(text, rtl)}</w:p>")

    document_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    f'<w:document xmlns:w="{_W_NS}"><w:body>{"".join(paragraphs)}'
                    f'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                    f'<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>'
                    f'</w:sectPr></w:body></w:document>')
    styles = [f'<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/>'
              f'<w:rPr>{_rtl_run(rtl)}</w:rPr></w:style>']
    for level in range(1, 7):
        size = max(22, 38 - level * 2)
        styles.append(f'<w:style w:type="paragraph" w:styleId="Heading{level}">'
                      f'<w:name w:val="heading {level}"/><w:basedOn w:val="Normal"/>'
                      f'<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/></w:pPr>'
                      f'<w:rPr><w:b/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr></w:style>')
    styles_xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  f'<w:styles xmlns:w="{_W_NS}">{"".join(styles)}</w:styles>')
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>'''
    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    core = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
            f' xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{xml_escape(document.title)}</dc:title>'
            f'<dc:creator>{xml_escape(document.author or "Trarium")}</dc:creator>'
            f'</cp:coreProperties>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
        archive.writestr("word/styles.xml", styles_xml)
        archive.writestr("docProps/core.xml", core)


def _xml_safe(text: str) -> str:
    """Drop control characters (form feed, vertical tab, NUL) that make DOCX invalid."""
    import re as _re
    return _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(text or ""))


def _bidi(rtl: bool) -> str:
    return "<w:bidi/>" if rtl else ""


def _rtl_run(rtl: bool) -> str:
    return "<w:rtl/>" if rtl else ""


def _run(text: str, rtl: bool) -> str:
    return (f"<w:r><w:rPr>{_rtl_run(rtl)}</w:rPr>"
            f'<w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r>')


# ------------------------------------------------------------------- public API
def convert_file(source: str | Path, output_path: str | Path, target_format: str,
                 *, rtl: bool = True) -> ConversionReport:
    source = Path(source)
    output = Path(output_path)
    if source.resolve() == output.resolve():
        raise ValueError("فایل خروجی نباید همان فایل ورودی باشد.")
    target = (target_format or "").lower().lstrip(".")
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"فرمت خروجی پشتیبانی نمی‌شود: {target_format}")
    document = read_document(source)
    if not document.blocks:
        raise ValueError("هیچ متن یا ساختاری برای تبدیل پیدا نشد.")
    if target == "pdf":
        warnings = write_pdf(document, output, rtl)
    elif target == "epub":
        warnings = write_epub(document, output, rtl)
    elif target == "docx":
        warnings = write_docx(document, output, rtl)
    elif target == "html":
        warnings = write_html(document, output, rtl)
    else:
        warnings = write_txt(document, output, rtl)
    return ConversionReport(
        input_format=source.suffix.lower().lstrip(".") or "unknown",
        output_format=target, title=document.title, output_path=str(output),
        blocks=len(document.blocks), headings=document.headings, tables=document.tables,
        images=document.images, chars=document.chars, warnings=warnings,
    )
