"""Generic fallback for games no engine module recognises.

1. Find text-bearing files by *content* (BOM / UTF-8 / UTF-16 / Shift-JIS /
   cp1252), whatever their extension.
2. Pull candidate strings per format: JSON values, gettext .po/.mo, CSV cells,
   ini/properties/yaml values, Apple ``.strings``, XML/HTML/plist text, string
   literals in scripts (lua/js/py/rb/gd/...), lines of plain text / scripts.
3. One short, cheap AI call on a small sample decides which *groups*
   (file type + key) are player-visible text; groups outside the sample, or
   every group when the AI is unavailable, use a conservative heuristic.
4. Translate with placeholders/tags locked, write back in place (same encoding
   when it can hold Persian, otherwise UTF-8 with the BOM/encoding change
   reported).
5. Binary files: only gettext ``.mo`` is rebuilt (its offset tables are known).
   Other binaries are never modified; strings found in them are counted and the
   reason is reported.

Pure Python, no pickle/eval; JSON via ``json``, everything else via regex.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from trarium.vn.gms.text import is_player_text

MAX_TEXT_FILE = 16 * 2**20
MAX_BINARY_SCAN = 64 * 2**20
MAX_FILES = 20000
SAMPLE = 40
PROTECT = re.compile(r"\{\{[^{}]{0,40}\}\}|\{[^{}\s]{0,30}\}|%[-+0-9.]*[sdifuxXc@]|%\d+\$[sd]|\$\{?\w+\}?|"
                     r"\\[A-Za-z]+\[[^\]]*\]|\\[nrt]|<[^<>\n]{1,60}>|\[[A-Za-z/][^\[\]\n]{0,30}\]|&[a-z]+;")
_LETTERS = re.compile(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff\u0400-\u04ff]{2,}")
SCRIPT_EXT = {".lua", ".js", ".ts", ".py", ".rb", ".gd", ".cs", ".hx", ".moon", ".nut", ".as", ".rpy", ".ks",
              ".tjs", ".nani", ".ink", ".yarn", ".gml"}
LINE_EXT = {".txt", ".ks", ".nani", ".ink", ".yarn", ".srt", ".dialog", ".dlg", ".scn", ".msg"}
SKIP_DIRS = {"__MACOSX", ".git", "node_modules"}
BINARY_REASON = ("فایل باینری با ساختار ناشناخته است؛ تغییر طول رشته‌ها بدون دانستن جدول آدرس‌ها بازی را خراب "
                 "می‌کند، پس دست نخورد.")


@dataclass
class Unit:
    file: Path
    group: str           # "<kind>:<key>" — the AI decides per group
    text: str
    loc: object          # format-specific locator


@dataclass
class Doc:
    path: Path
    kind: str
    encoding: str
    bom: bytes
    text: str = ""
    data: object = None
    units: list = field(default_factory=list)


# ------------------------------------------------------------------ decoding
def sniff(raw: bytes) -> tuple[str, bytes] | None:
    """-> (encoding, bom) for text, None for binary."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8", b"\xef\xbb\xbf"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return ("utf-16-le" if raw[:2] == b"\xff\xfe" else "utf-16-be"), raw[:2]
    head = raw[:65536]
    if head and head.count(b"\0") > len(head) // 4:
        even, odd = head[0::2].count(0), head[1::2].count(0)
        if odd > len(head) // 3 and even < len(head) // 20:
            return "utf-16-le", b""
        if even > len(head) // 3 and odd < len(head) // 20:
            return "utf-16-be", b""
        return None
    if b"\0" in head:
        return None
    for enc in ("utf-8", "shift_jis", "cp1252"):
        try:
            txt = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        ctrl = sum(1 for c in txt[:20000] if ord(c) < 32 and c not in "\r\n\t\f")
        if ctrl <= len(txt[:20000]) // 200:
            return enc, b""
    return None


def _kind(path: Path, text: str) -> str:
    ext = path.suffix.lower()
    t = text.lstrip()[:200]
    if ext == ".json" or (t[:1] in "[{" and ext not in SCRIPT_EXT):
        return "json"
    if ext == ".po" or ext == ".pot":
        return "po"
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext in (".xml", ".html", ".htm", ".plist", ".xhtml", ".svg", ".resx", ".xliff", ".xlf", ".tmx") or t.startswith("<"):
        return "xml"
    if ext == ".strings":
        return "strings"
    if ext in (".ini", ".cfg", ".properties", ".lang", ".conf"):
        return "ini"
    if ext in (".yml", ".yaml"):
        return "yaml"
    if ext in SCRIPT_EXT - LINE_EXT:
        return "script"
    return "lines"


# ------------------------------------------------------------------ extract
_LIT = re.compile(r'"((?:[^"\\\n]|\\.){2,})"|\'((?:[^\'\\\n]|\\.){2,})\'')


def _units_regex(doc: Doc, rx: re.Pattern, groupf, kind: str):
    for m in rx.finditer(doc.text):
        gi = next(i for i in range(1, (m.lastindex or 1) + 1) if m.group(i) is not None) \
            if m.lastindex else 0
        val = m.group(gi)
        doc.units.append(Unit(doc.path, f"{kind}:{groupf(m)}", val, (m.start(gi), m.end(gi))))


def _json_walk(node, path: str, out: list):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                out.append((node, k, f"{path}.{k}" if path else str(k), v))
            else:
                _json_walk(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                out.append((node, i, path + "[]", v))
            else:
                _json_walk(v, path + "[]", out)


def _key_of(jpath: str) -> str:
    return re.sub(r"\[\]", "", jpath.split(".")[-1]) or jpath


def extract_doc(doc: Doc) -> None:
    k = doc.kind
    if k == "json":
        try:
            doc.data = json.loads(doc.text)
        except ValueError:
            doc.kind = k = "lines"
        else:
            refs: list = []
            _json_walk(doc.data, "", refs)
            for parent, key, jpath, v in refs:
                doc.units.append(Unit(doc.path, f"json:{_key_of(jpath)}", v, (parent, key)))
            return
    if k == "po":
        rx = re.compile(r'^msgid "((?:[^"\\]|\\.)*)"\s*\nmsgstr "((?:[^"\\]|\\.)*)"', re.M)
        for m in rx.finditer(doc.text):
            if m.group(1) and (not m.group(2) or m.group(2) == m.group(1)):
                doc.units.append(Unit(doc.path, "po:msgid", m.group(1), (m.start(2), m.end(2))))
        return
    if k == "csv":
        rx = re.compile(r'(?:(?<=^)|(?<=[,;\t]))(?:"((?:[^"]|"")*)"|([^,;\t"\r\n]*))', re.M)
        for m in rx.finditer(doc.text):
            gi = 1 if m.group(1) is not None else 2
            val = m.group(gi) or ""
            if val.strip():
                line_start = doc.text.rfind("\n", 0, m.start()) + 1
                col = doc.text[line_start:m.start()].count(",") + doc.text[line_start:m.start()].count("\t")
                doc.units.append(Unit(doc.path, f"csv:col{col}", val, (m.start(gi), m.end(gi))))
        return
    if k == "xml":
        _units_regex(doc, re.compile(r">([^<>]*[A-Za-z][^<>]*)<"), lambda m: _tag_before(doc.text, m.start()), "xml")
        _units_regex(doc, re.compile(r'\b(?:title|label|text|alt|value|placeholder)="([^"<>]{2,})"'),
                     lambda m: "attr", "xml")
        return
    if k == "strings":
        _units_regex(doc, re.compile(r'^\s*"(?:[^"\\]|\\.)*"\s*=\s*"((?:[^"\\]|\\.)*)"\s*;', re.M), lambda m: "value", "strings")
        return
    if k in ("ini", "yaml"):
        sep = "=" if k == "ini" else ":"
        rx = re.compile(r"^\s*([\w.\-\[\] ]{1,60}?)\s*" + re.escape(sep) + r"\s*(?!\s)([\"']?)(.*?)\2\s*$", re.M)
        for m in rx.finditer(doc.text):
            if m.group(3).strip() and not m.group(3).strip().startswith(("{", "[", "|", ">", "&", "*")):
                doc.units.append(Unit(doc.path, f"{k}:{m.group(1).strip()}", m.group(3), (m.start(3), m.end(3))))
        return
    if k == "script":
        _units_regex(doc, _LIT, lambda m: _call_before(doc.text, m.start()), "script")
        return
    pos = 0
    for line in doc.text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if body.strip():
            doc.units.append(Unit(doc.path, "lines:" + doc.path.suffix.lower(), body, (pos, pos + len(body))))
        pos += len(line)


def _tag_before(text: str, at: int) -> str:
    m = re.search(r"<([A-Za-z_:][\w:.\-]*)[^<>]*>\s*$", text[max(0, at - 200):at + 1])
    return m.group(1) if m else "text"


def _call_before(text: str, at: int) -> str:
    m = re.search(r"([A-Za-z_][\w.]*)\s*[(=:]\s*$|([A-Za-z_][\w.]*)\s*\(\s*[^()]*,\s*$", text[max(0, at - 80):at])
    return (m.group(1) or m.group(2)) if m else "literal"


# --------------------------------------------------------------------- mo
def parse_mo(raw: bytes) -> list[tuple[bytes, bytes]] | None:
    if len(raw) < 28:
        return None
    for end in "<>":
        magic, _rev, n, oo, ot = struct.unpack_from(end + "5I", raw, 0)
        if magic == 0x950412DE:
            break
    else:
        return None
    if n > 1_000_000 or oo + 8 * n > len(raw) or ot + 8 * n > len(raw):
        return None
    out = []
    for i in range(n):
        ln, off = struct.unpack_from(end + "2I", raw, oo + 8 * i)
        lt, oft = struct.unpack_from(end + "2I", raw, ot + 8 * i)
        if off + ln > len(raw) or oft + lt > len(raw):
            return None
        out.append((raw[off:off + ln], raw[oft:oft + lt]))
    return out


def build_mo(pairs: list[tuple[bytes, bytes]]) -> bytes:
    """Rebuild a little-endian .mo (no hash table; gettext falls back to binary search)."""
    pairs = sorted(pairs, key=lambda p: p[0])
    n = len(pairs)
    oo, ot = 28, 28 + 8 * n
    pos = ot + 8 * n
    ids, strs, otab, ttab = b"", b"", [], []
    for k, _v in pairs:
        otab.append((len(k), pos + len(ids))); ids += k + b"\0"
    pos += len(ids)
    for _k, v in pairs:
        ttab.append((len(v), pos + len(strs))); strs += v + b"\0"
    head = struct.pack("<7I", 0x950412DE, 0, n, oo, ot, 0, 0)
    return head + b"".join(struct.pack("<2I", *x) for x in otab) + b"".join(struct.pack("<2I", *x) for x in ttab) + ids + strs


# ------------------------------------------------------------------- scan
def _binary_strings(raw: bytes) -> int:
    return len(re.findall(rb"[\x20-\x7e]{6,}\x00", raw[:MAX_BINARY_SCAN]))


def scan(root: Path) -> tuple[list[Doc], list[dict]]:
    """-> (text/mo documents with units, binary notes)."""
    root = Path(root)
    docs, binaries, count = [], [], 0
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.parts) or p.is_symlink() or not p.is_file():
            continue
        count += 1
        if count > MAX_FILES:
            break
        size = p.stat().st_size
        if size == 0:
            continue
        rel = str(p.relative_to(root))
        with open(p, "rb") as f:
            raw = f.read(min(size, MAX_TEXT_FILE + 1))
        if p.suffix.lower() == ".mo":
            pairs = parse_mo(raw)
            if pairs is not None:
                d = Doc(p, "mo", "utf-8", b"", data=pairs)
                for i, (k, v) in enumerate(pairs):
                    if k:
                        d.units.append(Unit(p, "mo:msgid", k.decode("utf-8", "replace"), i))
                docs.append(d)
                continue
        enc = sniff(raw) if size <= MAX_TEXT_FILE else None
        if enc is None:
            n = _binary_strings(raw)
            if n:
                binaries.append({"file": rel, "strings": n, "why": BINARY_REASON if size <= MAX_TEXT_FILE
                                 else "فایل بزرگ‌تر از سقف بررسی متن است."})
            continue
        try:
            text = raw[len(enc[1]):].decode(enc[0])
        except UnicodeDecodeError:
            continue
        d = Doc(p, _kind(p, text), enc[0], enc[1], text=text)
        extract_doc(d)
        if d.units:
            docs.append(d)
    return docs, binaries


def candidates(docs: list[Doc]) -> list[Unit]:
    return [u for d in docs for u in d.units if _LETTERS.search(PROTECT.sub(" ", u.text))]


def heuristic(u: Unit) -> bool:
    t = u.text.strip()
    if u.group.startswith(("po:", "mo:", "strings:")):
        return bool(_LETTERS.search(t))
    if u.group.startswith("lines:"):
        return is_player_text(t) and not re.match(r"^\s*(\[|@|;|//|#|<|\*|function |var |local |if |end\b)", t)
    return is_player_text(t)


CLASSIFY_PROMPT = ("You are classifying strings found in a video game's files. For each numbered item, decide if it "
                   "is text a PLAYER SEES (dialogue, menu, UI label, item description) or NOT (code, identifier, file "
                   "name, key, config value, asset name). Reply ONLY with the numbers of player-visible items, "
                   "comma-separated, e.g. 1,4,7. Reply 0 if none. Do not translate.")


def ai_classify(translator, units: list[Unit]) -> dict[str, bool] | None:
    """One short call on a sample (one example per group). -> {group: visible} or None."""
    if translator is None or not hasattr(translator, "translate"):
        return None
    groups: dict[str, Unit] = {}
    for u in units:
        groups.setdefault(u.group, u)
        if len(groups) >= SAMPLE:
            break
    sample = list(groups.values())
    if not sample:
        return {}
    lines = [f"{n + 1}. [{u.file.name} | {u.group}] {u.text[:120]!r}" for n, u in enumerate(sample)]
    try:
        reply = translator.translate("\n".join(lines), "en", CLASSIFY_PROMPT)
    except TypeError:
        try:
            reply = translator.translate("\n".join(lines), "en")
        except Exception:
            return None
    except Exception:
        return None
    reply = str(reply or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٬،", "0123456789,,"))
    nums = {int(x) for x in re.findall(r"\b\d{1,3}\b", reply)}
    if not nums or max(nums) > len(sample) or not re.fullmatch(r"[\d,\s.]+", reply.strip()):
        return None
    return {u.group: (n + 1) in nums for n, u in enumerate(sample)}


def select(units: list[Unit], decision: dict[str, bool] | None) -> list[Unit]:
    out = []
    for u in units:
        if decision is not None and u.group in decision:
            if decision[u.group] and not re.fullmatch(r"[\w.\-/\\]+", u.text.strip()):
                out.append(u)
        elif heuristic(u):
            out.append(u)
    return out


# ------------------------------------------------------------------ apply
def _escape_for(doc: Doc, s: str, original: str) -> str:
    if doc.kind in ("po", "strings", "script"):
        s = s.replace("\\", "\\\\").replace('"', '\\"') if '"' in s or "\\" in s else s
    if doc.kind == "csv" and '"' in s:
        s = s.replace('"', '""')
    if doc.kind == "xml":
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if not re.search(r"&\w+;", original) else s
    return s


def apply(selected: list[Unit], translations: list[str | None], docs: list[Doc]) -> dict:
    by_doc: dict[Path, list] = {}
    for u, t in zip(selected, translations):
        if t:
            by_doc.setdefault(u.file, []).append((u, t))
    written, notes = 0, []
    for d in docs:
        todo = by_doc.get(d.path)
        if not todo:
            continue
        if d.kind == "mo":
            pairs = list(d.data)
            for u, t in todo:
                pairs[u.loc] = (pairs[u.loc][0], t.encode("utf-8"))
            pairs = [p for p in pairs]
            for i, (k, v) in enumerate(pairs):
                if k == b"":
                    pairs[i] = (k, re.sub(rb"charset=[\w\-]+", b"charset=UTF-8", v))
            d.path.write_bytes(build_mo(pairs))
            written += len(todo)
            continue
        if d.kind == "json":
            for u, t in todo:
                parent, key = u.loc
                parent[key] = t
            d.text = json.dumps(d.data, ensure_ascii=False, indent=1)
        else:
            text = d.text
            for u, t in sorted(todo, key=lambda x: -x[0].loc[0]):
                a, b = u.loc
                text = text[:a] + _escape_for(d, t.replace("\n", " ") if d.kind != "lines" else t, text[a:b]) + text[b:]
            d.text = text
        enc, bom = d.encoding, d.bom
        try:
            body = d.text.encode(enc)
        except UnicodeEncodeError:
            notes.append(f"{d.path.name}: کدگذاری {enc} حروف فارسی ندارد؛ به UTF-8 ذخیره شد.")
            enc, bom = "utf-8", (b"\xef\xbb\xbf" if d.bom else b"")
            body = d.text.encode("utf-8")
        d.path.write_bytes(bom + body)
        written += len(todo)
    return {"written": written, "encoding_notes": notes}
