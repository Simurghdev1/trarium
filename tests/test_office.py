import io
import zipfile
from xml.etree import ElementTree as ET

from trarium.office import translate_office


class FakePipeline:
    def translate_block(self, text):
        return "\n".join("TR: " + line if line.strip() else line for line in str(text).splitlines())


def _make_package(path, parts):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return path


def _read(path, name):
    with zipfile.ZipFile(path) as zf:
        return zf.read(name).decode("utf-8")


def test_docx_keeps_paragraphs_and_translates_text(tmp_path):
    source = _make_package(
        tmp_path / "doc.docx",
        {
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                '<w:p w:rsidR="1"><w:pPr><w:jc w:val="center"/></w:pPr>'
                '<w:r><w:rPr><w:b/></w:rPr><w:t>Chapter One</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>It was a </w:t></w:r><w:r><w:t>dark night.</w:t></w:r></w:p>'
                '</w:body></w:document>'
            ),
            "word/styles.xml": "<styles><style w:styleId=\"Normal\"/></styles>",
        },
    )
    result = translate_office(source, ".docx", FakePipeline())
    target = tmp_path / "out.docx"
    target.write_bytes(result)
    xml = _read(target, "word/document.xml")
    assert "TR: Chapter One" in xml
    assert "TR: It was a dark night." in xml
    assert xml.count("<w:p>") + xml.count("<w:p ") == 2
    assert '<w:jc w:val="center"/>' in xml
    assert "<w:b/>" in xml
    ET.fromstring(xml.encode("utf-8"))
    assert _read(target, "word/styles.xml").startswith("<styles>")


def test_pptx_translates_slide_text(tmp_path):
    source = _make_package(
        tmp_path / "deck.pptx",
        {
            "ppt/slides/slide1.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Slide title</a:t></a:r></a:p></p:sld>',
        },
    )
    result = translate_office(source, ".pptx", FakePipeline())
    target = tmp_path / "out.pptx"
    target.write_bytes(result)
    xml = _read(target, "ppt/slides/slide1.xml")
    assert "TR: Slide title" in xml
    ET.fromstring(xml.encode("utf-8"))


def test_xlsx_translates_shared_strings(tmp_path):
    source = _make_package(
        tmp_path / "sheet.xlsx",
        {
            "xl/sharedStrings.xml": "<sst><si><t>Full name</t></si><si><t>Description of the item</t></si></sst>",
            "xl/worksheets/sheet1.xml": '<worksheet><sheetData><row><c t="s"><v>0</v></c></row></sheetData></worksheet>',
        },
    )
    result = translate_office(source, ".xlsx", FakePipeline())
    target = tmp_path / "out.xlsx"
    target.write_bytes(result)
    xml = _read(target, "xl/sharedStrings.xml")
    assert "TR: Full name" in xml
    assert "TR: Description of the item" in xml
    ET.fromstring(xml.encode("utf-8"))
    # شیت باید دست‌نخورده بماند
    assert "<v>0</v>" in _read(target, "xl/worksheets/sheet1.xml")
