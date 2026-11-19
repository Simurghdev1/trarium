"""Reproducible before/after proof for the OCR layout repair.

Run from the ``app`` folder:

    .venv/bin/python qa/ocr_deep_repair_proof.py

It does three things with the real sample PDFs:

1. rebuilds the damaged deliverables (EPUB cover, web-novel EPUB) from the
   already-translated text, so no API key is needed;
2. renders the *old* 2.19 output next to the *new* writer output for the same
   Undine page and measures the white-rectangle defect numerically;
3. writes metrics.json next to the images.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pymupdf

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
FILES = ROOT / "trarium-files"
QA_OUT = APP / "qa-out" / "ocr-deep-repair"
PUBLISH = ROOT / "files"

sys.path.insert(0, str(APP))

from trarium.extractors.epub import EPUBExtractor  # noqa: E402
from trarium.output import save_as_epub  # noqa: E402
from trarium.pdf_optimized import translate_pdf_optimized  # noqa: E402
from trarium.processors.pipeline import TranslationResult  # noqa: E402
from trarium.translators.base import BaseTranslator  # noqa: E402


class LayoutProofTranslator(BaseTranslator):
    """Deterministic Persian text: proves placement, not translation quality."""

    def get_token_limit(self):
        return 100000

    def translate(self, text, target_lang="fa"):
        markers = re.findall(r"\[TRARIUM_BLOCK_\d+\]", text)
        body = "این متن آزمایشی برای سنجش جای‌گذاری درست ترجمه روی صفحه است"
        if markers:
            return "\n".join(f"{marker}\n{body}" for marker in markers)
        return body


def render(page, dpi=110) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].astype(np.int16)


def white_defect(before: np.ndarray, after: np.ndarray) -> dict:
    added = ((after >= 248).all(axis=2) & ~(before >= 248).all(axis=2))
    changed = (np.abs(after - before).max(axis=2) > 12)
    return {"added_white_pixels": int(added.sum()),
            "added_white_ratio": round(float(added.mean()), 6),
            "changed_ratio": round(float(changed.mean()), 6)}


def fix_epub_cover() -> dict:
    """Rebuild the translated EPUB with the cover of the original book."""
    source = FILES / "_OceanofPDF.com_Omniscient_readers_viewpoint_novel_2_-_singNsong.epub"
    translated = FILES / "qa" / "outputs" / "full-audit-2.19" / "03_omniscient-vol2-fa.epub"
    if not source.exists() or not translated.exists():
        return {"ok": False, "reason": "source or translated EPUB missing"}
    import zipfile
    from bs4 import BeautifulSoup

    book = EPUBExtractor().extract(str(source))
    sections = []
    with zipfile.ZipFile(translated) as archive:
        names = sorted(n for n in archive.namelist()
                       if n.startswith("OEBPS/chapter_") and n.endswith(".xhtml"))
        for name in names:
            soup = BeautifulSoup(archive.read(name).decode("utf-8"), "lxml")
            title = soup.title.get_text(strip=True) if soup.title else name
            if "ترجمه شده توسط تراریوم" in title:
                continue
            paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
            if not paragraphs:
                continue
            sections.append(f"--- {title} ---\n\n" + "\n\n".join(paragraphs))
    result = TranslationResult(title=book.title, author=book.author, original_lang="auto",
                               target_lang="fa", translated_text="\n\n".join(sections),
                               total_chunks=len(sections), translated_chunks=len(sections),
                               cover=book.cover, cover_mime=book.cover_mime)
    output = QA_OUT / "03_omniscient-vol2-fa-cover.epub"
    save_as_epub(result, str(output))
    shutil.copy2(output, PUBLISH / "03_omniscient-vol2-fa-cover.epub")
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    return {"ok": bool(book.cover), "chapters": len(sections), "cover_bytes": len(book.cover),
            "has_cover_file": any(n.startswith("OEBPS/images/cover") for n in names),
            "output": str(output)}


def fix_webnovel_epub() -> dict:
    """Deliver the web-novel EPUB that actually contains the chapters."""
    good = APP / "qa-out" / "site-witchcult" / "Arc 7 Chapter 1  Baptism  Witch Cult Translations.epub"
    if not good.exists():
        return {"ok": False, "reason": "generated webnovel EPUB missing"}
    target = QA_OUT / "05_witchcult-arc7-fa.epub"
    shutil.copy2(good, target)
    shutil.copy2(good, PUBLISH / "05_witchcult-arc7-fa.epub")
    import zipfile
    with zipfile.ZipFile(target) as archive:
        chapters = [n for n in archive.namelist() if n.startswith("OEBPS/chapter_")]
        sizes = [archive.getinfo(n).file_size for n in chapters]
    return {"ok": True, "bytes": target.stat().st_size, "chapters": len(chapters),
            "chapter_bytes": sizes, "output": str(target)}


def undine_proof() -> dict:
    source = FILES / "Undine_Trarium_Test_6_Pages.pdf"
    old_output = FILES / "qa" / "outputs" / "full-audit-2.19" / "01_undine-6pages-fa.pdf"
    if not source.exists():
        return {"ok": False, "reason": "sample missing"}
    new_output = QA_OUT / "01_undine-6pages-fa-new-writer.pdf"
    _, stats = translate_pdf_optimized(str(source), LayoutProofTranslator(), "fa", str(new_output),
                                       use_cache=False, use_checkpoint=False, return_stats=True,
                                       translation_workers=2)
    src = pymupdf.open(str(source))
    new = pymupdf.open(str(new_output))
    try:
        before = render(src[0])
        after_new = render(new[0])
        metrics = {"new_writer": white_defect(before, after_new)}
        if old_output.exists():
            old = pymupdf.open(str(old_output))
            try:
                metrics["old_writer"] = white_defect(before, render(old[0]))
            finally:
                old.close()
        src[0].get_pixmap(dpi=110).save(QA_OUT / "undine-p1-source.png")
        new[0].get_pixmap(dpi=110).save(QA_OUT / "undine-p1-after.png")
        if old_output.exists():
            old = pymupdf.open(str(old_output))
            try:
                old[0].get_pixmap(dpi=110).save(QA_OUT / "undine-p1-before.png")
            finally:
                old.close()
    finally:
        src.close()
        new.close()
    shutil.copy2(new_output, PUBLISH / "01_undine-6pages-fa-fixed-layout.pdf")
    for name in ("before", "after", "source"):
        image = QA_OUT / f"undine-p1-{name}.png"
        if image.exists():
            shutil.copy2(image, PUBLISH / f"ocr-{image.name}")
    metrics.update({
        "ok": True,
        "translated_blocks": stats.translated_blocks,
        "write_failures": stats.write_failures,
        "covered_blocks": stats.covered_blocks,
        "expanded_blocks": stats.expanded_blocks,
        "refined_pages": stats.refined_pages,
        "uncertain_pages": stats.uncertain_pages,
        "output": str(new_output),
    })
    return metrics


def main() -> None:
    QA_OUT.mkdir(parents=True, exist_ok=True)
    PUBLISH.mkdir(parents=True, exist_ok=True)
    report = {
        "epub_cover": fix_epub_cover(),
        "webnovel_epub": fix_webnovel_epub(),
        "undine_pdf": undine_proof(),
    }
    (QA_OUT / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
