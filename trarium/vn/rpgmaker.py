"""RPG Maker MV/MZ: متن پیام‌ها، گزینه‌ها و دیتابیس داخل data/*.json.

کدهای رویداد: 401 (متن پیام)، 405 (متن اسکرول)، 102 (گزینه‌ها)، 402 (شاخهٔ
گزینه)، 101 (اسم گوینده در MZ). کدهای کنترلی مثل \\V[1]، \\N[2]، \\C[3] محافظت
می‌شوند. ساختار JSON و بقیهٔ کلیدها دست نمی‌خورد.
"""
from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

PROTECT = re.compile(r"\\[A-Za-z]+\[[^\]]*\]|\\[A-Za-z{}.|!<>^$\\]|%\d")
LETTER = re.compile(r"[A-Za-z]")
DB_FIELDS = {
    "Actors.json": ("name", "nickname", "profile"),
    "Classes.json": ("name",),
    "Skills.json": ("name", "description", "message1", "message2"),
    "Items.json": ("name", "description"),
    "Weapons.json": ("name", "description"),
    "Armors.json": ("name", "description"),
    "Enemies.json": ("name",),
    "States.json": ("name", "message1", "message2", "message3", "message4"),
}


def data_dir(root: Path) -> Path:
    for d in (root / "www" / "data", root / "data"):
        if (d / "System.json").is_file():
            return d
    raise FileNotFoundError("پوشهٔ data بازی پیدا نشد")


class Collector:
    """رشته‌ها را با یک setter جمع می‌کند تا بعد از ترجمه سر جایشان برگردند."""

    def __init__(self):
        self.texts: list[str] = []
        self.setters: list = []
        self.owners: list[Path] = []
        self.current: Path | None = None

    def add(self, text, setter):
        if isinstance(text, str) and LETTER.search(text):
            self.texts.append(text)
            self.setters.append(setter)
            self.owners.append(self.current)


def _walk_commands(cmds: list, col: Collector):
    i = 0
    while i < len(cmds):
        c = cmds[i]
        code = c.get("code")
        if code in (401, 405):
            j = i
            while j < len(cmds) and cmds[j].get("code") == code:
                j += 1
            group = cmds[i:j]
            n = len(group)

            def setter(value, group=group, n=n):
                lines = value.split("\n")
                if len(lines) != n:
                    width = max(10, max(len(g["parameters"][0]) for g in group) or 40)
                    lines = textwrap.wrap(" ".join(lines), width) or [""]
                    if len(lines) > n:   # keep window size: merge tail
                        lines = lines[: n - 1] + [" ".join(lines[n - 1:])]
                    lines += [""] * (n - len(lines))
                for g, line in zip(group, lines):
                    g["parameters"][0] = line

            col.add("\n".join(g["parameters"][0] for g in group), setter)
            i = j
            continue
        p = c.get("parameters") or []
        if code == 102 and p and isinstance(p[0], list):
            for k, choice in enumerate(p[0]):
                col.add(choice, lambda v, p=p, k=k: p[0].__setitem__(k, v))
        elif code == 402 and len(p) > 1:
            col.add(p[1], lambda v, p=p: p.__setitem__(1, v))
        elif code == 101 and len(p) > 4:
            col.add(p[4], lambda v, p=p: p.__setitem__(4, v))
        i += 1


def _pages(obj, col):
    for page in obj.get("pages") or []:
        _walk_commands(page.get("list") or [], col)


def extract(root: Path) -> tuple[dict[Path, object], Collector]:
    data = data_dir(root)
    docs: dict[Path, object] = {}
    col = Collector()
    for path in sorted(data.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        docs[path] = doc
        col.current = path
        name = path.name
        if re.match(r"Map\d+\.json", name):
            col.add(doc.get("displayName"), lambda v, d=doc: d.__setitem__("displayName", v))
            for ev in doc.get("events") or []:
                if ev:
                    _pages(ev, col)
        elif name in ("CommonEvents.json",):
            for ev in doc:
                if ev:
                    _walk_commands(ev.get("list") or [], col)
        elif name == "Troops.json":
            for tr in doc:
                if tr:
                    _pages(tr, col)
        elif name in DB_FIELDS:
            for row in doc:
                if row:
                    for f in DB_FIELDS[name]:
                        col.add(row.get(f), lambda v, r=row, f=f: r.__setitem__(f, v))
        elif name == "System.json":
            col.add(doc.get("gameTitle"), lambda v: doc.__setitem__("gameTitle", v))
            col.add(doc.get("currencyUnit"), lambda v: doc.__setitem__("currencyUnit", v))
            for key in ("elements", "skillTypes", "weaponTypes", "armorTypes", "equipTypes"):
                lst = doc.get(key) or []
                for k, s in enumerate(lst):
                    col.add(s, lambda v, lst=lst, k=k: lst.__setitem__(k, v))
            terms = doc.get("terms") or {}
            for key in ("basic", "commands", "params"):
                lst = terms.get(key) or []
                for k, s in enumerate(lst):
                    col.add(s, lambda v, lst=lst, k=k: lst.__setitem__(k, v))
            msgs = terms.get("messages") or {}
            for k, s in list(msgs.items()):
                col.add(s, lambda v, m=msgs, k=k: m.__setitem__(k, v))
    return docs, col


def save(docs: dict[Path, object], changed: set[Path]):
    for path in changed:
        path.write_text(json.dumps(docs[path], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def install_font(root: Path, font: Path) -> list[Path]:
    fonts = (root / "www" / "fonts") if (root / "www").is_dir() else (root / "fonts")
    fonts.mkdir(parents=True, exist_ok=True)
    (fonts / "Sahel.ttf").write_bytes(font.read_bytes())
    out = [fonts / "Sahel.ttf"]
    css = fonts / "gamefont.css"
    if css.is_file():   # MV
        css.write_text('@font-face {\n    font-family: GameFont;\n    src: url("Sahel.ttf");\n}\n', encoding="utf-8")
        out.append(css)
    system = data_dir(root) / "System.json"
    doc = json.loads(system.read_text(encoding="utf-8-sig"))
    adv = doc.get("advanced")
    if isinstance(adv, dict):   # MZ
        adv["mainFontFilename"] = "Sahel.ttf"
        system.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        out.append(system)
    return out
