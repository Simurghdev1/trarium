"""Keep the pictures of a rebuilt PDF visible in every viewer.

Scanned books usually store their page images as JPEG 2000 (``/JPXDecode``).
Adobe Reader and Chrome's own PDF engine decode that fine, so a page looks
complete there — but a whole class of viewers (pdf.js, which is what GitHub,
Firefox and many phone readers and "preview" panes use, plus several mobile
PDF apps) cannot decode JPEG 2000 at all.  In those viewers the page texture
and the engravings vanish and only the text Trarium wrote, with the pale boxes
behind it, is left: it looks exactly as if the pictures had been deleted.

The pixels are not missing, only the codec is unsupported, so the cure is to
re-encode those images into formats every reader understands:

* a photo-like image becomes a plain JPEG (same size, same place, same mask);
* a small image, an image with a colour-key mask, or an alpha channel becomes
  lossless Flate data, because JPEG would smear a mask's edges;
* a grey soft mask becomes lossless Flate grey data.

Nothing moves: each image is rewritten *in place* in its own object, so the
page geometry, the drawing order and the existing masks stay untouched.
"""
from __future__ import annotations

import io
import zlib

import pymupdf

# Below this many pixels an image is cheaper to store lossless than to explain
# a JPEG artefact to someone staring at an engraving.
LOSSLESS_PIXELS = 400_000


def _jpx_filters(doc: pymupdf.Document, xref: int) -> bool:
    kind, value = doc.xref_get_key(xref, "Filter")
    if kind == "null":
        return False
    return "JPXDecode" in str(value)


def _has_key_mask(doc: pymupdf.Document, xref: int) -> bool:
    kind, value = doc.xref_get_key(xref, "Mask")
    return kind != "null" and value not in ("null", "")


def _image_xrefs(doc: pymupdf.Document) -> list[int]:
    """Every image object in the file, soft masks included.

    A soft mask is a separate image object; leaving it as JPEG 2000 would keep
    the hole in the transparency even after the picture itself was fixed.
    """
    seen: dict[int, None] = {}
    for page in doc:
        for image in page.get_images(full=True):
            seen[image[0]] = None
    # Soft masks are not listed by get_images(), so walk the dicts for them.
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, "Subtype")[1] != "/Image":
                continue
        except Exception:
            continue
        seen.setdefault(xref, None)
        kind, value = doc.xref_get_key(xref, "SMask")
        if kind == "xref":
            target = int(str(value).split()[0])
            seen.setdefault(target, None)
    return sorted(seen)


def _encode_jpeg(pix: pymupdf.Pixmap, quality: int) -> bytes:
    from PIL import Image

    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _write_image(doc: pymupdf.Document, xref: int, data: bytes, pymupdf_filter: str,
                 colorspace: str, width: int, height: int) -> None:
    # compress=0: the bytes are already encoded, and letting MuPDF deflate
    # them again would wrap a JPEG in Flate and label it as one.
    doc.update_stream(xref, data, new=True, compress=0)
    doc.xref_set_key(xref, "Filter", pymupdf_filter)
    doc.xref_set_key(xref, "DecodeParms", "null")
    doc.xref_set_key(xref, "ColorSpace", colorspace)
    doc.xref_set_key(xref, "BitsPerComponent", "8")
    doc.xref_set_key(xref, "Width", str(width))
    doc.xref_set_key(xref, "Height", str(height))
    doc.xref_set_key(xref, "Length", str(len(data)))


def _convert(doc: pymupdf.Document, xref: int, quality: int) -> bool:
    if doc.xref_get_key(xref, "ImageMask")[1] == "true":
        return False  # a one-bit stencil: re-encoding it would break it
    try:
        pix = pymupdf.Pixmap(doc, xref)
    except Exception:
        return False

    grey = pix.colorspace is None or pix.colorspace.n == 1
    if grey:
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        data = zlib.compress(pix.samples, 6)
        _write_image(doc, xref, data, "/FlateDecode", "/DeviceGray", pix.width, pix.height)
        return True

    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    lossless = _has_key_mask(doc, xref) or pix.width * pix.height <= LOSSLESS_PIXELS
    if lossless:
        _write_image(doc, xref, zlib.compress(pix.samples, 6), "/FlateDecode",
                     "/DeviceRGB", pix.width, pix.height)
    else:
        _write_image(doc, xref, _encode_jpeg(pix, quality), "/DCTDecode",
                     "/DeviceRGB", pix.width, pix.height)
    return True


def normalize_jpx_images(doc: pymupdf.Document, quality: int = 82) -> int:
    """Re-encode every JPEG 2000 image so no viewer has to skip it.

    Returns how many images were converted.  Safe to run more than once; a file
    without JPEG 2000 images is left byte-for-byte alone.
    """
    converted = 0
    for xref in _image_xrefs(doc):
        if not _jpx_filters(doc, xref):
            continue
        if _convert(doc, xref, quality):
            converted += 1
    return converted
