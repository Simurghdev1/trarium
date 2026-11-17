"""Prove that the pages Trarium left alone really are untouched.

Compares every page listed in a run's ``page_status.json`` (those marked
``exempt``) between the source PDF and the translated one: the rendered pixels
and the text must be identical, and the page rectangle must not move.

    python qa/verify_untouched_pages.py --source in.pdf --output out.pdf \
        --status qa/outputs/run/page_status.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--status", required=True, help="page_status.json of the run")
    parser.add_argument("--dpi", type=int, default=60)
    args = parser.parse_args()

    decisions = json.loads(pathlib.Path(args.status).read_text(encoding="utf-8"))
    pages = [d["page"] - 1 for d in decisions if d.get("exempt")]
    if not pages:
        print("هیچ صفحه‌ای دست‌نخورده نمانده است.")
        return 0

    same, different = [], []
    with pymupdf.open(args.source) as source, pymupdf.open(args.output) as output:
        for index in pages:
            if index >= source.page_count or index >= output.page_count:
                different.append((index + 1, "صفحه در خروجی نیست"))
                continue
            a, b = source[index], output[index]
            if a.rect != b.rect:
                different.append((index + 1, "اندازهٔ صفحه تغییر کرد"))
                continue
            if a.get_text() != b.get_text():
                different.append((index + 1, "متن صفحه تغییر کرد"))
                continue
            if a.get_pixmap(dpi=args.dpi).samples != b.get_pixmap(dpi=args.dpi).samples:
                different.append((index + 1, "تصویر صفحه تغییر کرد"))
                continue
            same.append(index + 1)

    print(f"صفحه‌های دست‌نخورده: {len(same)} از {len(pages)}")
    print("یکسان:", "، ".join(str(p) for p in same) or "—")
    if different:
        print("تغییرکرده:", "، ".join(f"{p} ({why})" for p, why in different))
        return 1
    print("همهٔ صفحه‌های دست‌نخورده بیت‌به‌بیت مثل فایل اصلی هستند.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
