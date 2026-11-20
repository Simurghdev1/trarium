"""TyranoScript / TyranoBuilder: سناریوهای data/scenario/*.ks.

چه چیزهایی ترجمه می‌شود:
- خطوط متن (دیالوگ و روایت)؛ تگ‌های داخل خط مثل [p] [r] [l] [ruby ...] [emb ...] قفل می‌شوند.
- اسم گوینده در خطوط «#اسم» یا «#اسم:چهره» (هر اسم یک ترجمهٔ ثابت دارد).
- مقدار text= در [glink] [ptext] [mtext] و @glink، و jname= در [chara_new].

دست نمی‌خورد: توضیحات (;) و /* */، برچسب‌ها (*label)، بلوک‌های [iscript]…[endscript] و
[html]…[endhtml]، مقدارهای عبارت (text=&f.x) و همهٔ پارامترهای دیگر تگ‌ها. موتور Tyrano
(پوشهٔ tyrano/) کپی یا عوض نمی‌شود؛ مجوزش بازتوزیع موتور را منع می‌کند، پس خروجی فقط پچ است.

نمایش فارسی: Tyrano هر حرف را در یک <span> جدا می‌گذارد و مرورگر حروف جدا را به هم
نمی‌چسباند. برای همین متن پیام با arabic_reshaper شکل‌دهی می‌شود (بدون برعکس‌کردن) و
پنجرهٔ پیام با CSS راست‌به‌چپ می‌شود؛ تایپ‌شدن متن هم از راست شروع می‌شود.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# یک تگ؛ کروشه‌های داخل مقدارها (text="[x]" یا &tf.a[tf.i]) تگ را نمی‌شکنند
TAG = re.compile(r"""\[(?:[^\[\]"']|"[^"]*"|'[^']*'|\[[^\[\]]*\])*\]""")
PROTECT = re.compile(TAG.pattern + r"|&[A-Za-z_][\w.]*|\{[^}]*\}")
LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
ATTR_TAGS = {"glink": "text", "ptext": "text", "mtext": "text", "chara_new": "jname", "button": "hint"}
ATTR = re.compile(r'(?P<key>\b(?:text|jname|hint))\s*=\s*(?:"(?P<v1>[^"]*)"|\'(?P<v2>[^\']*)\'|(?P<v3>[^\s\]"\']+))')
CSS_NAME = "trarium_fa.css"


@dataclass
class Segment:
    file: Path
    line: int
    kind: str          # text | name | jname | attr
    text: str
    span: tuple[int, int] | None = None   # برای attr: محدودهٔ مقدار در خط


def find_root(folder: Path) -> Path | None:
    """پوشه‌ای که data/scenario دارد (برای بیلدهای nw.js ممکن است داخل resources/app باشد)."""
    folder = Path(folder)
    if (folder / "data" / "scenario").is_dir():
        return folder
    for sc in sorted(folder.rglob("scenario")):
        if sc.is_dir() and sc.parent.name == "data" and any(sc.rglob("*.ks")):
            return sc.parent.parent
    return None


def scenario_files(root: Path) -> list[Path]:
    return sorted((root / "data" / "scenario").rglob("*.ks"))


def read(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _tag_name(tag_body: str) -> str:
    return tag_body.strip().split(None, 1)[0].lower() if tag_body.strip() else ""


def extract(root: Path) -> tuple[list[Segment], dict[Path, list[str]]]:
    segs: list[Segment] = []
    docs: dict[Path, list[str]] = {}
    for f in scenario_files(root):
        lines = read(f).splitlines()
        docs[f] = lines
        block = None
        for i, line in enumerate(lines):
            s = line.strip()
            low = s.lower()
            if block:
                if (block == "comment" and "*/" in s) or low.startswith(f"[end{block}") or low.startswith(f"@end{block}"):
                    block = None
                continue
            if s.startswith("/*"):
                block = None if "*/" in s[2:] else "comment"
                continue
            if low.startswith(("[iscript", "@iscript")):
                block = "script"; continue
            if low.startswith(("[html", "@html")):
                block = "html"; continue
            if not s or s.startswith((";", "*")):
                continue
            if s.startswith("#"):
                name = s[1:].split(":", 1)[0].strip()
                if name and name != "?" and LETTER.search(name):
                    segs.append(Segment(f, i, "name", name))
                continue
            if s.startswith("@"):
                if _tag_name(s[1:]) in ATTR_TAGS:
                    _attrs(f, i, line, s[1:], segs)
                continue
            for m in TAG.finditer(line):
                if _tag_name(m.group(0)[1:-1]) in ATTR_TAGS:
                    _attrs(f, i, line, m.group(0)[1:-1], segs, offset=m.start() + 1)
            if LETTER.search(TAG.sub("", line)):
                segs.append(Segment(f, i, "text", line.strip()))
    return segs, docs


def _attrs(f, i, line, body, segs, offset=None):
    want = ATTR_TAGS[_tag_name(body)]
    base = line.index(body) if offset is None else offset
    for m in ATTR.finditer(body):
        g = next(k for k in ("v1", "v2", "v3") if m.group(k) is not None)
        val = m.group(g)
        if m.group("key") != want or not val or val.startswith("&") or not LETTER.search(val):
            continue
        kind = "jname" if want == "jname" else "attr"
        segs.append(Segment(f, i, kind, val, (base + m.start(g), base + m.end(g))))


def shape(text: str) -> str:
    """شکل‌دهی حروف (چسباندن) بدون برعکس‌کردن؛ جهت را CSS تعیین می‌کند. تگ‌ها دست نمی‌خورند."""
    import arabic_reshaper
    parts = PROTECT.split(text)
    tags = PROTECT.findall(text)
    out = [arabic_reshaper.reshape(parts[0])]
    for t, p in zip(tags, parts[1:]):
        out.append(t)
        out.append(arabic_reshaper.reshape(p))
    return "".join(out)


def apply(segs: list[Segment], trs: list[str | None], docs: dict[Path, list[str]]) -> dict:
    names: dict[str, str] = {}   # یک اسم = یک ترجمه، هم در #اسم هم در jname
    for s_, t in zip(segs, trs):
        if s_.kind in ("name", "jname") and t:
            names.setdefault(s_.text, t.replace("\n", " ").strip())
    written, changed = 0, set()
    by_line: dict[tuple[Path, int], list[tuple[Segment, str]]] = {}
    for s, t in zip(segs, trs):
        if s.kind in ("name", "jname"):
            t = names.get(s.text)
        if not t:
            continue
        by_line.setdefault((s.file, s.line), []).append((s, t))
    for (f, i), items in by_line.items():
        line = docs[f][i]
        for s, t in sorted(items, key=lambda x: -(x[0].span or (0, 0))[0]):
            t = t.replace("\n", " ").strip()
            if s.kind == "text":
                indent = line[: len(line) - len(line.lstrip())]
                line = indent + shape(t)
            elif s.kind == "name":
                rest = line.strip()[1:].split(":", 1)
                face = (":" + rest[1]) if len(rest) > 1 else ""
                line = line[: len(line) - len(line.lstrip())] + "#" + t + face   # بدون شکل‌دهی تا با jname یکی بماند
            else:
                a, b = s.span
                q = line[a - 1] if a > 0 and line[a - 1] in "\"'" else ""
                val = t.replace('"', "").replace("'", "").replace("]", "")
                line = line[:a] + (val if q else f'"{val}"') + line[b:]
            written += 1
        docs[f][i] = line
        changed.add(f)
    for f in changed:
        f.write_text("\n".join(docs[f]) + "\n", encoding="utf-8")
    return {"segments": len(segs), "written": written, "kept_original": len(segs) - written,
            "changed": sorted(changed)}


def install_font(root: Path, font: Path) -> list[Path]:
    """فونت + CSS راست‌به‌چپ، و یک خط loadcss در first.ks (فقط تگ استاندارد Tyrano، بدون جاوااسکریپت)."""
    others = root / "data" / "others"
    others.mkdir(parents=True, exist_ok=True)
    (others / "Sahel.ttf").write_bytes(font.read_bytes())
    css = others / CSS_NAME
    css.write_text(
        '@font-face { font-family: "Sahel"; src: url("Sahel.ttf"); }\n'
        ".message_inner { direction: rtl; text-align: right; font-family: Sahel !important; }\n"
        ".message_inner span { font-family: Sahel !important; }\n"
        ".glink_button, .backlog_text, .chara_name_area { font-family: Sahel !important; direction: rtl; }\n",
        encoding="utf-8")
    out = [others / "Sahel.ttf", css]
    first = root / "data" / "scenario" / "first.ks"
    if first.is_file():
        lines = read(first).splitlines()
        tag = f'[loadcss file="./data/others/{CSS_NAME}"]'
        if tag not in lines:
            at = next((k + 1 for k, l in enumerate(lines) if l.strip().lower().startswith(("[title", "@title"))), 0)
            lines.insert(at, tag)
            first.write_text("\n".join(lines) + "\n", encoding="utf-8")
            out.append(first)
    return out
