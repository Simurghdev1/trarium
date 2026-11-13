"""Rebuild the real-world input fixtures used by the PDF QA workflow.

Every source is a real, public document — nothing is invented:

* ``en_source.pdf``  – pages 1-3 of "Attention Is All You Need" (arXiv:1706.03762v7)
* ``scan_source.pdf`` – one scanned leaf of *The Modern Hercules* (Internet Archive
  item ``amodernherculest00wins``) wrapped into a PDF, used for the OCR path
* ``fa_source.pdf``  – real Persian prose from Persian Wikipedia
  (articles سعدی / گلستان / حافظ, CC BY-SA 4.0) typeset with the project's Sahel font
  so the file has a genuine Persian text layer to measure against

The downloads need network access; the generated PDFs are committed so CI runs
offline and repeatably. Run from the repository root:

    python qa/fixtures/build_fixtures.py
"""
from __future__ import annotations

import json
import pathlib
import urllib.parse
import urllib.request

import pymupdf

HERE = pathlib.Path(__file__).resolve().parent
FONT = HERE.parent.parent / "trarium" / "fonts" / "Sahel.ttf"
UA = {"User-Agent": "TrariumQA/1.0 (wordpress-free academic audit)"}
ARXIV = "https://arxiv.org/pdf/1706.03762v7"
SCAN = "https://archive.org/download/amodernherculest00wins/page/n12_w2000.jpg"
WIKI_TITLES = ("سعدی", "گلستان سعدی", "حافظ")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def build_english(tmp: pathlib.Path) -> None:
    raw = fetch(ARXIV)
    tmp.write_bytes(raw)
    src = pymupdf.open(tmp)
    out = pymupdf.open()
    out.insert_pdf(src, from_page=0, to_page=2)
    out.save(HERE / "en_source.pdf")
    out.close()
    src.close()


def build_scan(tmp: pathlib.Path) -> None:
    tmp.write_bytes(fetch(SCAN))
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), filename=str(tmp))
    doc.save(HERE / "scan_source.pdf")
    doc.close()


def build_persian() -> None:
    texts = []
    for title in WIKI_TITLES:
        query = urllib.parse.urlencode(
            {
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "format": "json",
                "titles": title,
                "exchars": 4000,
            }
        )
        payload = json.loads(fetch(f"https://fa.wikipedia.org/w/api.php?{query}"))
        for page in payload["query"]["pages"].values():
            extract = page.get("extract") or ""
            if extract.strip():
                texts.append(extract.strip())
    text = "\n\n".join(texts)

    font = pymupdf.Font(fontfile=str(FONT))
    doc = pymupdf.open()
    page = None
    y = 60.0
    for paragraph in [p for p in text.split("\n") if p.strip()]:
        # Insert the logical (unshaped) text so the fixture keeps a clean,
        # machine-readable Persian text layer. Trarium's own PDF writer reshapes
        # its output; that difference is exactly what the report measures.
        line = paragraph
        words, current = line.split(), ""
        wrapped = []
        for word in words:
            candidate = f"{current} {word}".strip()
            if font.text_length(candidate, 11) <= 480:
                current = candidate
            else:
                wrapped.append(current)
                current = word
        wrapped.append(current)
        for piece in wrapped:
            if page is None or y > 780:
                page = doc.new_page(width=595, height=842)
                page.insert_font(fontname="Sahel", fontfile=str(FONT))
                y = 60.0
            page.insert_text((510 - font.text_length(piece, 11), y), piece, fontname="Sahel", fontsize=11)
            y += 18
        y += 8
    doc.save(HERE / "fa_source.pdf")
    doc.close()
    (HERE / "fa_source.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    tmp = pathlib.Path("/tmp/trarium-fixture-download")
    build_english(tmp)
    build_scan(tmp)
    build_persian()
    for name in ("en_source.pdf", "scan_source.pdf", "fa_source.pdf", "fa_source.txt"):
        path = HERE / name
        print(f"{name}: {path.stat().st_size / 1024:.0f} KB")
