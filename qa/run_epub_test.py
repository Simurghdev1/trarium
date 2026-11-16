"""End-to-end Persian translation of an EPUB novel.

The layout is not rebuilt: the original XHTML documents are translated in place,
so the cover, artwork, stylesheet and reading order stay exactly as they were.
Only the text inside <p>/<h1..h3> is replaced, the document switches to RTL with
a Persian font, and the navigation labels are translated as well.

    GEMINI_API_KEY=... python qa/run_epub_test.py \
        --input book.epub --output book_fa.epub --cache qa/outputs/epub-book/cache

Every file's translation is cached, so an interrupted run continues where it
stopped instead of paying for the same chapters twice.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import os
import pathlib
import re
import sys
import time
import unicodedata
import zipfile
from xml.etree import ElementTree

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trarium.translators.gemini import GeminiTranslator  # noqa: E402
from trarium.translators.openrouter import OpenRouterTranslator  # noqa: E402

MARK = re.compile(r"\[\[(\d+)\]\]")
BLOCK = re.compile(r"<(p|h1|h2|h3)(\s[^>]*)?>(.*?)</\1>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
FONT_SRC = ROOT / "trarium" / "fonts" / "Sahel.ttf"

NAV_LABELS = {
    "Contents": "فهرست",
    "Table of Contents": "فهرست",
    "Cover": "جلد",
    "Insert": "تصویر",
    "Title Page": "صفحهٔ عنوان",
    "Copyright": "حق نشر",
    "Yen Newsletter": "خبرنامه",
    "Ize Newsletter": "خبرنامه",
    "OceanofPDF.com": "OceanofPDF.com",
    "Navigation": "ناوبری",
    "Begin Reading": "شروع خواندن",
}
BOOK_TITLE_FA = "دیدگاه خوانندهٔ همه‌چیزدان، جلد ۲"
IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
EPISODE = {
    "Time of Judgment: (Part II)": "زمان داوری (بخش دوم)",
    "Landlord": "صاحب‌خانه",
    "Emergency Defense": "دفاع اضطراری",
    "Omniscient Sunfish’s Perspective": "دیدگاه سان‌فیش همه‌چیزدان",
    "Future War: (Part I)": "جنگ آینده (بخش اول)",
}


def text_of(fragment: str) -> str:
    """Visible text of an inline HTML fragment."""
    raw = TAG.sub(" ", fragment)
    raw = html.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def units(raw: str) -> list[tuple[int, int, str, str]]:
    """(start, end, inner_html, text) for every translatable block."""
    found = []
    for match in BLOCK.finditer(raw):
        inner = match.group(3)
        base = match.start(3)
        if "<svg" in inner.lower():
            continue
        # A paragraph can hold inline artwork (ORV uses them for censored words).
        # Translate the text runs between the images and leave each image in place.
        cuts = [(m.start(), m.end()) for m in IMG_TAG.finditer(inner)] if "<img" in inner.lower() else []
        spans, cursor = [], 0
        for start, end in cuts:
            spans.append((cursor, start))
            cursor = end
        spans.append((cursor, len(inner)))
        for start, end in spans:
            run = inner[start:end]
            text = text_of(run)
            if not re.search(r"[A-Za-z]{2}", text):
                continue
            found.append((base + start, base + end, run, text))
    return found


def build_translator(provider: str, model: str | None):
    if provider == "gemini":
        return GeminiTranslator(
            api_key=os.environ.get("GEMINI_API_KEY", ""),
            model_name=model or "gemini-flash-lite-latest",
        )
    return OpenRouterTranslator(
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        model_name=model or "nvidia/nemotron-3-ultra-550b-a55b:free",
    )


def translate_batch(translator, texts: list[str], target: str = "fa") -> list[str]:
    """Translate a batch, keeping the paragraph count stable."""
    if len(texts) == 1:
        return [translator.translate(texts[0], target)]
    numbered = "\n\n".join(f"[[{i + 1}]] {t}" for i, t in enumerate(texts))
    prompt = (
        "This is a batch of consecutive paragraphs from a novel. Translate each "
        "paragraph into the target language. Keep the [[n]] marker in front of the "
        "matching paragraph, keep the same number of paragraphs in the same order, "
        "and do not merge or split paragraphs. Output only the translated paragraphs."
    )
    out = translator.translate(f"{prompt}\n\n{numbered}", target)
    parts = MARK.split(out)
    pieces: dict[int, str] = {}
    for index in range(1, len(parts) - 1, 2):
        try:
            pieces[int(parts[index])] = parts[index + 1].strip()
        except ValueError:
            continue
    if sorted(pieces) != list(range(1, len(texts) + 1)) or any(not v for v in pieces.values()):
        # The model lost the markers: fall back to smaller batches, then to singles.
        if len(texts) > 1:
            half = len(texts) // 2
            return translate_batch(translator, texts[:half], target) + translate_batch(
                translator, texts[half:], target
            )
        raise RuntimeError("empty translation")
    return [pieces[i] for i in range(1, len(texts) + 1)]


def make_batches(texts: list[str], budget: int) -> list[list[int]]:
    batches, current, size = [], [], 0
    for index, text in enumerate(texts):
        if current and size + len(text) > budget:
            batches.append(current)
            current, size = [], 0
        current.append(index)
        size += len(text)
    if current:
        batches.append(current)
    return batches


def translate_file(translator, raw: str, budget: int, workers: int) -> list[str]:
    found = units(raw)
    texts = [item[3] for item in found]
    if not texts:
        return []
    batches = make_batches(texts, budget)
    results: list[str] = [""] * len(texts)

    def task(batch: list[int]) -> list[tuple[int, str]]:
        chunk = [texts[i] for i in batch]
        for attempt in range(4):
            try:
                got = translate_batch(translator, chunk)
                return list(zip(batch, got))
            except Exception as exc:  # noqa: BLE001 - retried, then reported
                if attempt == 3:
                    raise RuntimeError(f"batch failed after retries: {exc}") from exc
                time.sleep(3 * (2**attempt))
        return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for pairs in pool.map(task, batches):
            for index, value in pairs:
                results[index] = value
    return results


PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def wrap(paragraphs: list[str], persian_digits: bool = True) -> list[str]:
    out = []
    for p in paragraphs:
        value = re.sub(r"\s+", " ", p.strip())
        if persian_digits:
            value = value.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
        out.append(html.escape(value, quote=False))
    return out


def replace_run(run: str, new_text: str) -> str:
    """Put the translation inside a text run while keeping every inline tag.

    Paragraphs around inline artwork are split into text runs; replacing the whole
    run would drop half of a <span> pair and corrupt the XHTML, so the tags stay
    and only their text is rewritten.
    """
    escaped = html.escape(new_text.strip(), quote=False)
    parts = re.split(r"(<[^>]+>)", run)
    out, filled = [], False
    for part in parts:
        if part.startswith("<"):
            out.append(part)
        elif part.strip():
            out.append(escaped if not filled else "")
            filled = True
        else:
            out.append(part if filled else "")
    if not filled:
        out.append(escaped)
    return "".join(out)


def localize_headings(raw: str) -> str:
    """A heading that is only a chapter number becomes Persian numerals."""

    def fix(match: re.Match) -> str:
        inner = match.group(3)
        if text_of(inner).isdigit():
            inner = TAG.sub("", inner)
            inner = re.sub(r"\d+", lambda n: n.group(0).translate(PERSIAN_DIGITS), inner)
            return f'<{match.group(1)}{match.group(2) or ""}>{inner}</{match.group(1)}>'
        return match.group(0)

    return re.sub(r"<(h1|h2|h3)(\s[^>]*)?>(.*?)</\1>", fix, raw, flags=re.S | re.I)


def rtl_document(raw: str) -> str:
    """Switch a document to RTL Persian and link the bundled font."""
    raw = raw.replace("<title>Omniscient Reader’s Viewpoint, Vol. 2</title>", f"<title>{BOOK_TITLE_FA}</title>")
    head_link = '<link rel="stylesheet" href="css/fa.css" type="text/css"/>'
    if head_link not in raw:
        raw = re.sub(r"(</head>)", head_link + r"\1", raw, count=1, flags=re.I)
    # Idempotent: the html tag is rebuilt once, so running this twice cannot
    # produce a duplicate lang/dir attribute (which makes the XHTML invalid).
    match = re.search(r"<html\b[^>]*>", raw, flags=re.I)
    if match:
        tag = re.sub(r'\s(dir|lang)="[^"]*"', "", match.group(0))
        tag = tag[:-1].rstrip() + ' lang="fa" dir="rtl">'
        raw = raw[: match.start()] + tag + raw[match.end() :]
    raw = re.sub(r'(<html\b[^>]*?)\sxml:lang="[^"]*"', r'\1 xml:lang="fa"', raw, count=1, flags=re.I)
    return raw


def translate_nav(text: str) -> str:
    def chapter(match: re.Match) -> str:
        return f"فصل {match.group(1).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))}"

    text = re.sub(r"\bChapter\s+(\d+)", chapter, text)
    for source, target in EPISODE.items():
        text = text.replace(source, target)
    for source, target in NAV_LABELS.items():
        text = text.replace(source, target)
    text = re.sub(r"\bEpisode\s+(\d+)", lambda m: "قسمت " + m.group(1), text)
    return text


def decodable(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


CSS = """@font-face{font-family:Sahel;src:url(fonts/Sahel.ttf) format("truetype")}
html,body{direction:rtl;text-align:right;max-width:100%;overflow-x:hidden;box-sizing:border-box}
body,body *{max-width:100%!important;box-sizing:border-box!important}
body{margin:0!important;padding:1.2em 6%!important}
img{max-width:100%!important;height:auto}
body,p,h1,h2,h3,div,span,a{font-family:Sahel,"Iranian Sans",Tahoma,sans-serif}
p{line-height:1.95!important;text-align:justify;text-align-last:right;margin:.4em 0!important}
h1,h2,h3{text-align:center;line-height:1.6!important}
.toc-front,.toc-back{text-align:right}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--model", default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-chars", type=int, default=3600)
    parser.add_argument("--only", default=None, help="comma separated file names, for a trial run")
    args = parser.parse_args()

    source = pathlib.Path(args.input)
    out_path = pathlib.Path(args.output)
    cache = pathlib.Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    translator = build_translator(args.provider, args.model)

    with zipfile.ZipFile(source) as book:
        order = book.namelist()
        payload = {name: book.read(name) for name in order}

    only = set(args.only.split(",")) if args.only else None
    documents = [
        name
        for name in order
        if name.lower().endswith((".xhtml", ".html", ".ncx"))
    ]
    started = time.time()
    per_file = {}
    for name in documents:
        if only and pathlib.Path(name).name not in only and name not in only:
            continue
        raw = decodable(payload[name])
        if name.endswith(".ncx"):
            continue
        found = units(raw)
        if not found:
            continue
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        cached = cache / (pathlib.Path(name).name + ".json")
        if cached.exists():
            data = json.loads(cached.read_text(encoding="utf-8"))
            if data.get("hash") == digest and len(data.get("paragraphs", [])) == len(found):
                per_file[name] = data["paragraphs"]
                continue
        print(f"→ {name}: {len(found)} paragraphs", flush=True)
        got = translate_file(translator, raw, args.batch_chars, args.workers)
        cached.write_text(
            json.dumps({"hash": digest, "paragraphs": got}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        per_file[name] = got

    # Rebuild every document in place, keeping images, CSS and reading order.
    for name, paragraphs in per_file.items():
        raw = decodable(payload[name])
        found = units(raw)
        pieces, cursor = [], 0
        for (start, end, inner, _text), translated in zip(found, paragraphs):
            pieces.append(raw[cursor:start])
            pieces.append(replace_run(inner, translated) if "<" in inner else wrap([translated])[0])
            cursor = end
        pieces.append(raw[cursor:])
        rebuilt = rtl_document(localize_headings("".join(pieces)))
        payload[name] = rebuilt.encode("utf-8")

    # Front matter labels (table of contents, nav, NCX).
    for name in order:
        lower = name.lower()
        if lower.endswith((".xhtml", ".html", ".ncx")):
            raw = decodable(payload[name])
            if any(label in raw for label in NAV_LABELS) or "Episode" in raw:
                fixed = translate_nav(raw)
                if fixed != raw:
                    payload[name] = rtl_document(fixed).encode("utf-8")

    payload["OEBPS/css/fa.css"] = CSS.encode("utf-8")
    if FONT_SRC.exists():
        payload["OEBPS/fonts/Sahel.ttf"] = FONT_SRC.read_bytes()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as book:
        # The EPUB spec wants the mimetype entry first and uncompressed.
        book.writestr("mimetype", payload.get("mimetype", b"application/epub+zip"), compress_type=zipfile.ZIP_STORED)
        for name in payload:
            if name == "mimetype":
                continue
            book.writestr(name, payload[name], compress_type=zipfile.ZIP_DEFLATED)

    metrics = measure(source, out_path, per_file, time.time() - started)
    (out_path.parent / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if metrics["qc"]["ok"] else 2


def measure(source: pathlib.Path, output: pathlib.Path, per_file: dict, seconds: float) -> dict:
    leftover_blocks: list[str] = []
    with zipfile.ZipFile(output) as book:
        names = book.namelist()
        text = []
        for name in names:
            if name.lower().endswith((".xhtml", ".html")):
                raw = decodable(book.read(name))
                body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.S | re.I)
                text.append(html.unescape(TAG.sub(" ", body)))
                for match in BLOCK.finditer(body):
                    block = text_of(match.group(3))
                    latin = len(re.findall(r"[A-Za-z]{2,}", block))
                    persian_in_block = len([c for c in block if "\u0600" <= c <= "\u06ff"])
                    if latin >= 8 and persian_in_block < 10:
                        leftover_blocks.append(block[:80])
        joined = unicodedata.normalize("NFKC", " ".join(text))
        images = [n for n in names if n.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".svg"))]
    persian = len([c for c in joined if "\u0600" <= c <= "\u06ff"])
    latin_words = re.findall(r"\b[A-Za-z][A-Za-z']{2,}\b", joined)
    with zipfile.ZipFile(source) as book:
        source_images = [n for n in book.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".svg"))]
    issues = []
    missing = sorted(set(source_images) - set(images))
    if missing:
        issues.append(f"{len(missing)} image(s) missing: {missing[:3]}")
    if persian < 0.35 * (persian + sum(len(w) for w in latin_words)):
        issues.append("Persian share is low")
    if leftover_blocks:
        issues.append(f"{len(leftover_blocks)} paragraph(s) still in English: {leftover_blocks[:2]}")
    broken = []
    with zipfile.ZipFile(output) as book:
        for name in book.namelist():
            if name.lower().endswith((".xhtml", ".html")):
                try:
                    ElementTree.fromstring(book.read(name))
                except ElementTree.ParseError as exc:
                    broken.append(f"{name}: {exc}")
    if broken:
        issues.append(f"{len(broken)} malformed XHTML file(s): {broken[:2]}")
    return {
        "file": output.name,
        "files_translated": len(per_file),
        "paragraphs": sum(len(v) for v in per_file.values()),
        "persian_chars": persian,
        "latin_words": len(latin_words),
        "images_in": len(source_images),
        "images_out": len(images),
        "seconds": round(seconds, 1),
        "qc": {
            "ok": not issues,
            "issues": issues,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
