"""Ren'Py: بیرون کشیدن دیالوگ‌ها، گزینه‌ها و رشته‌های _() و نوشتن فارسی در همان جا.

قانون‌ها:
- فقط محتوای داخل کوتیشن عوض می‌شود؛ تورفتگی، اسم شخصیت، with/if و بقیهٔ کد
  دست نمی‌خورد، پس بازی همان‌طور اجرا می‌شود.
- تگ‌های {b}، متغیرهای [name]، %(x)s و \\n محافظت می‌شوند. اگر ترجمه یکی
  از آن‌ها را گم کرد یا فارسی نداشت، متن اصلی می‌ماند.
- فونت ساحل و راست‌به‌چپ با یک فایل جدا (trarium_fa.rpy) اضافه می‌شود.
"""
from __future__ import annotations

import pickle
import re
import zlib
from dataclasses import dataclass
from pathlib import Path

KEYWORDS = {
    "define", "default", "image", "show", "scene", "hide", "play", "stop", "queue",
    "voice", "jump", "call", "label", "screen", "style", "init", "python", "if", "elif",
    "else", "while", "return", "with", "transform", "translate", "old", "new", "text",
    "textbutton", "imagebutton", "hotspot", "add", "use", "action", "key", "pause",
    "window", "nvl", "camera", "at", "for", "pass", "font", "outlines", "layout",
    "config", "gui", "build", "achievement", "tooltip", "has", "frame", "vbox", "hbox",
    "label_text", "renpy", "audio", "sound", "music", "rpy", "extend_noop", "dissolve",
}
_STR = r'"(?P<body>(?:[^"\\]|\\.)*)"'
# say:   [who [attrs...]] "text" [with x] [(...)]     menu choice: "text" [if cond]:
SAY = re.compile(
    r'^(?P<pre>\s*(?:(?P<who>[A-Za-z_]\w*)(?:\s+[A-Za-z_@][\w-]*)*\s+)?)' + _STR +
    r'(?P<post>\s*(?:\(.*\))?\s*(?:with\s+[\w.()]+)?\s*(?:id\s+\w+)?\s*(?:nointeract)?\s*(?:if\s+.+)?:?\s*(?:#.*)?)$'
)
SCREEN_TEXT = re.compile(r'^(?P<pre>\s*text\s+)' + _STR + r'(?P<post>.*)$')
UNDERSCORE = re.compile(r'(?P<pre>_{1,2}\(\s*)' + _STR + r'(?P<post>\s*\))')
CHARACTER = re.compile(r'(?P<pre>Character\(\s*)' + _STR)
PROTECT = re.compile(r"\{[^{}]*\}|\[[^\[\]]*\]|%\([^)]*\)[sdif]|%[sdif]|\\n|\\\"|\\'")
FA = re.compile(r"[\u0600-\u06FF]")
LETTER = re.compile(r"[A-Za-z]")


@dataclass
class Segment:
    file: Path
    line: int
    start: int
    end: int
    text: str          # what the player reads (escapes undone)
    kind: str          # say | menu | ui | name


def _unescape(body: str) -> str:
    return body.replace('\\"', '"')


def _escape(text: str) -> str:
    text = text.replace('\\"', '\x00')
    text = text.replace('"', '\\"').replace("\x00", '\\"')
    return text


def protect(text: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        token = f"⟦{len(mapping)}⟧"
        mapping[token] = m.group(0)
        return token

    return PROTECT.sub(repl, text), mapping


def restore(text: str, mapping: dict[str, str]) -> str | None:
    for token, original in mapping.items():
        if token not in text:
            return None
        text = text.replace(token, original, 1)
    return text


def script_files(game: Path) -> list[Path]:
    out = []
    for path in sorted(game.rglob("*.rpy")):
        rel = path.relative_to(game).parts
        if rel and rel[0] in {"tl", "renpy", "trarium_fa"}:
            continue
        if path.name == "trarium_fa.rpy":
            continue
        out.append(path)
    return out


SPEAKERS_BUILTIN = {"extend", "narrator", "centered", "vcentered", "nvl_narrator"}
DEFINE_CHAR = re.compile(r"^\s*define\s+([A-Za-z_]\w*)\s*=\s*(?:Character|DynamicCharacter|NVLCharacter)\(")


def speakers(game: Path) -> set[str]:
    """اسم متغیرهای شخصیت؛ فقط خطی که با یکی از این‌ها شروع شود دیالوگ است."""
    names = set(SPEAKERS_BUILTIN)
    for path in script_files(game):
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            m = DEFINE_CHAR.match(line)
            if m:
                names.add(m.group(1))
    return names


def extract(game: Path) -> list[Segment]:
    segments: list[Segment] = []
    who_ok = speakers(game)
    for path in script_files(game):
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        in_python = None
        for no, line in enumerate(lines):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if in_python is not None:
                if stripped and indent <= in_python:
                    in_python = None
                else:
                    continue
            if re.match(r"^(init\s+[-\d\s]*)?python\b.*:\s*$", stripped) or stripped.startswith("python early"):
                in_python = indent
                continue
            if not stripped or stripped.startswith(("#", "$")) or '"""' in stripped:
                continue
            m = SAY.match(line)
            first = stripped.split()[0] if stripped.split() else ""
            if m and first not in KEYWORDS and ((m.group("who") in who_ok) or stripped.startswith('"')):
                body = m.group("body")
                if LETTER.search(body):
                    kind = "menu" if m.group("post").rstrip().endswith(":") else "say"
                    segments.append(Segment(path, no, m.start("body"), m.end("body"), _unescape(body), kind))
                continue
            m3 = SCREEN_TEXT.match(line)
            if m3 and len(m3.group("body").split()) >= 2 and LETTER.search(m3.group("body")):
                segments.append(Segment(path, no, m3.start("body"), m3.end("body"), _unescape(m3.group("body")), "screen_text"))
                continue
            for rx, kind in ((UNDERSCORE, "ui"), (CHARACTER, "name")):
                for m2 in rx.finditer(line):
                    if LETTER.search(m2.group("body")):
                        segments.append(Segment(path, no, m2.start("body"), m2.end("body"), _unescape(m2.group("body")), kind))
    return segments


def apply(segments: list[Segment], translations: list[str | None]) -> dict:
    """ترجمه‌ها را در فایل‌ها می‌نویسد. خروجی: آمار."""
    by_file: dict[Path, list[tuple[Segment, str]]] = {}
    stats = {"segments": len(segments), "written": 0, "kept_original": 0}
    for seg, tr in zip(segments, translations):
        if tr is None:
            stats["kept_original"] += 1
            continue
        by_file.setdefault(seg.file, []).append((seg, tr))
    for path, items in by_file.items():
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        # right-to-left inside a line so earlier offsets stay valid
        for seg, tr in sorted(items, key=lambda x: (x[0].line, x[0].start), reverse=True):
            line = lines[seg.line]
            lines[seg.line] = line[: seg.start] + _escape(tr) + line[seg.end:]
            stats["written"] += 1
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stats["files_changed"] = sorted(str(p) for p in by_file)
    return stats


FONT_SETUP = '''# Trarium: Persian font + right-to-left. Safe on Ren'Py 7 and 8.
init -1 python:
    for _name in ("text_font", "name_text_font", "interface_text_font"):
        if hasattr(gui, _name):
            setattr(gui, _name, "trarium_fa/Sahel.ttf")
    if hasattr(gui, "dialogue_text_xalign"):
        gui.dialogue_text_xalign = 1.0

init 999 python:
    config.rtl = True
    for _s in ("default", "say_dialogue", "say_label", "button_text", "choice_button_text",
               "input", "interface", "gui_text", "label_text", "pref_label_text", "history_text"):
        try:
            _st = getattr(style, _s)
            _st.font = "trarium_fa/Sahel.ttf"
            if renpy.version_tuple >= (8, 1):
                _st.shaper = "harfbuzz"
        except Exception:
            pass
    # Persian reads from the right: dialogue and history lines hug the right edge.
    for _s in ("say_dialogue", "history_text", "nvl_dialogue"):
        try:
            getattr(style, _s).text_align = 1.0
        except Exception:
            pass
    try:
        config.font_replacement_map.clear()
    except Exception:
        pass
'''


def install_font(game: Path, font: Path) -> list[Path]:
    target = game / "trarium_fa"
    target.mkdir(exist_ok=True)
    (target / "Sahel.ttf").write_bytes(font.read_bytes())
    setup = game / "trarium_fa.rpy"
    setup.write_text(FONT_SETUP, encoding="utf-8")
    return [target / "Sahel.ttf", setup]


# ---------------------------------------------------------------- RPA archives
class _IndexUnpickler(pickle.Unpickler):
    """فهرست RPA فقط dict/list/tuple/int/bytes است؛ هیچ کلاس یا تابعی بار نمی‌شود (جلوگیری از اجرای کد)."""

    def find_class(self, module, name):
        if (module, name) == ("_codecs", "encode"):  # bytes با پروتکل ۲ در پایتون ۳ (Ren'Py 8)
            import _codecs
            return _codecs.encode
        if module in ("builtins", "__builtin__") and name in ("bytes", "bytearray", "str", "unicode"):
            return {"bytes": bytes, "bytearray": bytearray}.get(name, str)
        raise pickle.UnpicklingError(f"آرشیو RPA مشکوک است (شیء {module}.{name}).")


def unpack_rpa(archive: Path, dest: Path, only=(".rpy",)) -> list[Path]:
    """RPA-3.0/2.0: فقط اسکریپت‌های .rpy را بیرون می‌آورد."""
    raw = archive.read_bytes()
    header = raw[:64].split(b"\n", 1)[0].decode("latin-1").split()
    if header[0] == "RPA-3.0":
        offset, key = int(header[1], 16), int(header[2], 16)
    elif header[0] == "RPA-2.0":
        offset, key = int(header[1], 16), 0
    else:
        raise ValueError(f"فرمت آرشیو پشتیبانی نمی‌شود: {header[0]}")
    import io
    index = _IndexUnpickler(io.BytesIO(zlib.decompress(raw[offset:])), encoding="bytes").load()
    root = Path(dest).resolve()
    written = []
    for name, chunks in index.items():
        name = name.decode("utf-8") if isinstance(name, bytes) else name
        if not name.endswith(only):
            continue
        first = chunks[0]
        start, length = first[0] ^ key, first[1] ^ key
        prefix = first[2] if len(first) > 2 else b""
        if isinstance(prefix, str):
            prefix = prefix.encode("latin-1")
        data = prefix + raw[start: start + length - len(prefix)]
        out = dest / name
        if root not in out.resolve().parents:
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            out.write_bytes(data)
            written.append(out)
    return written


# ------------------------------------------------- old engines (Ren'Py < 7)
TAG = re.compile(r"\{[^{}]*\}")


def preshape(text: str, font: Path, size: int, width: int | None) -> str:
    """برای موتورهای قدیمی بدون شکل‌دهی عربی: حروف چسبیده + ترتیب نمایشی، خط‌به‌خط.

    تگ‌های {i} برداشته می‌شوند چون با برعکس شدن ترتیب، باز و بسته‌شان جابه‌جا می‌شود.
    خط‌شکنی خودمان انجام می‌شود تا خط اول جمله بالا بماند.
    """
    import arabic_reshaper
    from bidi.algorithm import get_display
    from PIL import ImageFont

    plain = TAG.sub("", text).replace("\\n", " ").strip()
    lines = [plain]
    if width:
        f = ImageFont.truetype(str(font), size)
        lines, cur = [], ""
        for word in plain.split():
            trial = (cur + " " + word).strip()
            if cur and f.getlength(arabic_reshaper.reshape(trial)) > width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
    return "\\n".join(get_display(arabic_reshaper.reshape(l)) for l in lines)
