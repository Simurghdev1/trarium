"""RPG Maker XP / VX / VX Ace (Data/*.rxdata, *.rvdata, *.rvdata2).

Texts: event commands 101 (XP: first message line), 401 (message lines), 405
(scrolling text), 102 (choices), 402 (choice branch label) in maps, common events
and troops; database names/descriptions/messages.  RGSS draws text left to right
without Arabic shaping, so every Persian line is stored pre-shaped in visual
order (control codes such as ``\\C[2]`` ``\\N[1]`` ``\\V[3]`` ``\\.`` kept).

Encrypted archives (Game.rgssad / .rgss2a / .rgss3a) are detected but not
unpacked: the loose ``Data`` folder is needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from trarium.vn.gms.text import visual_line
from trarium.vn.rgss import marshal as M

EXTS = (".rxdata", ".rvdata", ".rvdata2")
ARCHIVES = ("game.rgssad", "game.rgss2a", "game.rgss3a")
PROTECT = re.compile(r"\\[A-Za-z]+\[[^\]]*\]|\\[A-Za-z{}.|!<>^$\\]")
CODE_RE = PROTECT
COLOR_RE = re.compile(r"\\[Cc]\[\d+\]\Z")
MAX_FILE = 64 * 2**20
DB_FIELDS = {
    "Actors": ("@name", "@nickname", "@description"),
    "Classes": ("@name",),
    "Skills": ("@name", "@description", "@message1", "@message2"),
    "Items": ("@name", "@description"),
    "Weapons": ("@name", "@description"),
    "Armors": ("@name", "@description"),
    "Enemies": ("@name",),
    "States": ("@name", "@message1", "@message2", "@message3", "@message4"),
}
_LETTER = re.compile(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff]")


def data_dir(root: Path) -> Path | None:
    for d in (root / "Data", root / "data"):
        if d.is_dir() and any(p.suffix.lower() in EXTS for p in d.iterdir() if p.is_file()):
            return d
    return None


def archive_of(root: Path) -> Path | None:
    for p in Path(root).iterdir():
        if p.is_file() and p.name.lower() in ARCHIVES:
            return p
    return None


@dataclass
class Item:
    text: str
    kind: str          # say | choice | db
    file: Path
    apply: object      # callable(list_of_lines_or_text)


def _str(v) -> str | None:
    return v.text() if isinstance(v, M.RString) else None


def _walk_list(lst, path: Path, items: list, xp: bool):
    if not isinstance(lst, M.RArray):
        return
    i = 0
    while i < len(lst):
        c = lst[i]
        code = c.get("@code") if isinstance(c, M.RObject) else None
        if code in (401, 405) or (xp and code == 101):
            follow = 401 if code in (101, 401) else 405
            j = i + 1
            while j < len(lst) and isinstance(lst[j], M.RObject) and lst[j].get("@code") == follow:
                j += 1
            group = lst[i:j]
            lines = [_str(g.get("@parameters")[0]) or "" for g in group]
            text = " ".join(l.strip() for l in lines if l.strip())
            if _LETTER.search(text):
                items.append(Item(text, "say", path, (lst, i, j - i, max(len(l) for l in lines))))
            i = j
            continue
        if code == 102:
            params = c.get("@parameters")
            choices = params[0] if params else None
            if isinstance(choices, M.RArray):
                for k, ch in enumerate(choices):
                    t = _str(ch)
                    if t and _LETTER.search(t):
                        items.append(Item(t, "choice", path, (choices, k)))
        i += 1


def _walk_pages(obj, path, items, xp):
    if isinstance(obj, M.RObject):
        lst = obj.get("@list")
        if lst is not None:
            _walk_list(lst, path, items, xp)
        pages = obj.get("@pages")
        if isinstance(pages, M.RArray):
            for p in pages:
                _walk_pages(p, path, items, xp)


def extract(root: Path):
    """-> (items, docs{path: tree})"""
    d = data_dir(Path(root))
    if d is None:
        raise FileNotFoundError("پوشهٔ Data بازی RPG Maker XP/VX/VX Ace پیدا نشد")
    docs, items = {}, []
    for p in sorted(d.iterdir()):
        if p.suffix.lower() not in EXTS or not p.is_file() or p.is_symlink() or p.stat().st_size > MAX_FILE:
            continue
        stem = p.stem
        xp = p.suffix.lower() == ".rxdata"
        if not (stem.startswith("Map") and stem[3:].isdigit()) and stem not in ("CommonEvents", "Troops") \
                and stem not in DB_FIELDS:
            continue
        try:
            tree = M.loads(p.read_bytes())
        except M.MarshalError:
            continue
        docs[p] = tree
        if stem.startswith("Map"):
            ev = tree.get("@events") if isinstance(tree, M.RObject) else None
            if isinstance(ev, M.RHash):
                for _k, e in ev.pairs:
                    _walk_pages(e, p, items, xp)
        elif stem in ("CommonEvents", "Troops"):
            for o in tree if isinstance(tree, M.RArray) else []:
                _walk_pages(o, p, items, xp)
        else:
            for o in tree if isinstance(tree, M.RArray) else []:
                if isinstance(o, M.RObject):
                    for f in DB_FIELDS[stem]:
                        t = _str(o.get(f))
                        if t and _LETTER.search(t):
                            items.append(Item(t, "db", p, (o, f)))
    return items, docs


def _wrap(text: str, budget: int) -> list[str]:
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if cur and len(PROTECT.sub("", cand)) > budget:
            lines.append(cur); cur = w
        else:
            cur = cand
    lines.append(cur)
    return lines


def show(line: str) -> str:
    return visual_line(line, CODE_RE, COLOR_RE, "\\C[0]")


def apply(items: list[Item], translations: list[str | None], docs: dict) -> dict:
    changed, written = set(), 0
    # message groups are applied from the end of each list so indices stay valid
    order = sorted(range(len(items)), key=lambda n: (id(items[n].apply[0]),
                                                     -items[n].apply[1] if items[n].kind == "say" else 0))
    for n in order:
        it, fa = items[n], translations[n]
        if not fa:
            continue
        fa = fa.replace("\n", " ").strip()
        if it.kind == "say":
            lst, start, count, width = it.apply
            lines = _wrap(fa, max(20, width))
            group = lst[start:start + count]
            head = group[0]
            new_cmds = []
            for k, line in enumerate(lines):
                proto = group[min(k, len(group) - 1)]
                if k < len(group):
                    cmd = proto
                else:   # extra line: clone the last line command (shares @indent / @code values)
                    cmd = M.RObject(proto.cls, [(key, val) for key, val in proto.ivars])
                    cmd.set("@parameters", M.RArray([None]))
                    if head.get("@code") == 101:
                        cmd.set("@code", 401)
                old = cmd.get("@parameters")[0]
                params = cmd.get("@parameters") if k < len(group) else cmd.get("@parameters")
                params[0] = M.utf8_string(show(line), old if isinstance(old, M.RString) else None)
                new_cmds.append(cmd)
            for k in range(len(lines), len(group)):          # fewer lines: blank the rest
                old = group[k].get("@parameters")[0]
                group[k].get("@parameters")[0] = M.utf8_string("", old if isinstance(old, M.RString) else None)
                new_cmds.append(group[k])
            lst[start:start + count] = new_cmds
        elif it.kind == "choice":
            arr, k = it.apply
            arr[k] = M.utf8_string(show(fa), arr[k])
        else:
            o, f = it.apply
            o.set(f, M.utf8_string(show(fa), o.get(f)))
        changed.add(it.file)
        written += 1
    for p in changed:
        p.write_bytes(M.dumps(docs[p]))
    return {"segments": len(items), "written": written, "kept_original": len(items) - written,
            "files": sorted(str(p.name) for p in changed)}
