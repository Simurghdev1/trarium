"""A translated EPUB keeps the source book's cover."""
import zipfile
from types import SimpleNamespace

from trarium.epub_cover import extract_cover
from trarium.output import save_as_epub

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _source(path, style):
    opf_meta, item_props, guide = "", "", ""
    if style == "epub3":
        item_props = ' properties="cover-image"'
    elif style == "epub2":
        opf_meta = '<meta name="cover" content="img"/>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        zf.writestr("OPS/book.opf", f'<package><metadata>{opf_meta}</metadata><manifest>'
                    f'<item id="img" href="images/c.png" media-type="image/png"{item_props}/>'
                    '<item id="p1" href="text/p1.xhtml" media-type="application/xhtml+xml"/></manifest>'
                    f'<spine><itemref idref="p1"/></spine>{guide}</package>')
        zf.writestr("OPS/images/c.png", PNG)
        zf.writestr("OPS/text/p1.xhtml", '<html><body><img src="../images/c.png"/></body></html>')


def test_cover_found_every_way(tmp_path):
    for style in ("epub3", "epub2", "first-page"):
        src = tmp_path / f"{style}.epub"
        _source(src, style)
        cover = extract_cover(src)
        assert cover is not None and cover.data == PNG and cover.media_type == "image/png", style


def test_translated_epub_keeps_cover(tmp_path):
    src = tmp_path / "s.epub"
    _source(src, "epub3")
    result = SimpleNamespace(title="کتاب", translated_text="فصل ۱\nسلام", target_lang="fa")
    out = tmp_path / "o.epub"
    save_as_epub(result, str(out), cover=extract_cover(src))
    again = extract_cover(out)
    assert again is not None and again.data == PNG
    with zipfile.ZipFile(out) as zf:
        opf = zf.read("OEBPS/content.opf").decode()
    assert 'properties="cover-image"' in opf and '<meta name="cover"' in opf
    assert opf.index('idref="cover"') < opf.index('idref="c1"')
