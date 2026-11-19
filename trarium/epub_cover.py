"""The cover image of an EPUB, so a translated book keeps its own cover.

Readers find a cover in several ways (EPUB 3 ``properties="cover-image"``,
EPUB 2 ``<meta name="cover">``, a ``<guide>`` cover page, or simply a first
page that is one picture). All of them are checked, in that order.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

_IMG_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}


@dataclass
class Cover:
    data: bytes
    media_type: str
    ext: str


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): v for k, v in re.findall(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']', tag)}


def _image_in_page(zf: zipfile.ZipFile, page_path: str) -> str | None:
    try:
        html = zf.read(page_path).decode("utf-8", "ignore")
    except KeyError:
        return None
    m = re.search(r'<(?:img|image)\b[^>]*?(?:src|xlink:href|href)\s*=\s*["\']([^"\']+)["\']', html, re.I)
    if not m:
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(page_path), m.group(1).split("#")[0]))


def extract_cover(path: str | Path) -> Cover | None:
    try:
        zf = zipfile.ZipFile(str(path))
    except Exception:
        return None
    with zf:
        try:
            container = zf.read("META-INF/container.xml").decode("utf-8", "ignore")
            opf_path = re.search(r'full-path\s*=\s*["\']([^"\']+)', container).group(1)
            opf = zf.read(opf_path).decode("utf-8", "ignore")
        except Exception:
            return None
        base = posixpath.dirname(opf_path)
        items = {}
        for tag in re.findall(r"<item\b[^>]*>", opf, re.I):
            a = _attrs(tag)
            if "id" in a and "href" in a:
                a["full"] = posixpath.normpath(posixpath.join(base, a["href"]))
                items[a["id"]] = a

        target = None
        for a in items.values():
            if "cover-image" in a.get("properties", "").split():
                target = a["full"]
                break
        if not target:
            for tag in re.findall(r"<meta\b[^>]*>", opf, re.I):
                a = _attrs(tag)
                if a.get("name", "").lower() == "cover" and a.get("content") in items:
                    target = items[a["content"]]["full"]
                    break
        if not target:
            m = re.search(r'<reference\b[^>]*type\s*=\s*["\']cover["\'][^>]*>', opf, re.I)
            if m:
                href = _attrs(m.group(0)).get("href", "").split("#")[0]
                if href:
                    target = _image_in_page(zf, posixpath.normpath(posixpath.join(base, href)))
        if not target:
            first = re.search(r'<itemref\b[^>]*idref\s*=\s*["\']([^"\']+)', opf, re.I)
            if first and first.group(1) in items:
                page = items[first.group(1)]["full"]
                try:
                    text = re.sub(r"<[^>]+>", " ", zf.read(page).decode("utf-8", "ignore"))
                except KeyError:
                    text = "x" * 999
                if len(text.split()) < 40:  # a picture page, not a chapter
                    target = _image_in_page(zf, page)
        if not target:
            return None
        ext = posixpath.splitext(target)[1].lower()
        if ext not in _IMG_TYPES:
            return None
        try:
            data = zf.read(target)
        except KeyError:
            return None
        return Cover(data=data, media_type=_IMG_TYPES[ext], ext=".jpg" if ext == ".jpeg" else ext)


def cover_from_source(path: str | Path | None) -> Cover | None:
    """The cover of the source book, when it is an EPUB that has one."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        if p.suffix.lower() == ".epub" or zipfile.is_zipfile(p):
            return extract_cover(p)
    except Exception:
        return None
    return None
