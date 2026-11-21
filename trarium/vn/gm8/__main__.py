"""CLI:  python -m trarium.vn.gm8 strings game.exe out.json
        python -m trarium.vn.gm8 build game.exe translations.json out.exe [--report r.json]"""
import argparse
import json
from pathlib import Path

from .exe import GM8Exe
from .strings import extract, is_texty


def main():
    ap = argparse.ArgumentParser(prog="trarium.vn.gm8")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("strings")
    s.add_argument("exe")
    s.add_argument("out")
    b = sub.add_parser("build")
    b.add_argument("exe")
    b.add_argument("tr")
    b.add_argument("out")
    b.add_argument("--report")
    b.add_argument("--preview")
    a = ap.parse_args()
    if a.cmd == "strings":
        g = GM8Exe(Path(a.exe).read_bytes())
        lits = extract(g)
        rows = [{"id": k, "text": v.text.decode("latin-1"), "texty": is_texty(v.text)} for k, v in lits.items()]
        Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print("literals: %d (text-like: %d)" % (len(rows), sum(r["texty"] for r in rows)))
    else:
        from .build import build
        build(a.exe, a.tr, a.out, a.report, preview_dir=a.preview)


if __name__ == "__main__":
    main()
