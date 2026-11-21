"""RPG Maker XP/VX/VX Ace (Ruby Marshal), generic AI fallback, ISO images. Synthetic data only."""
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from trarium import vn_tab
from trarium.vn import generic as gn
from trarium.vn.detect import detect_engine
from trarium.vn.rgss import engine as rg
from trarium.vn.rgss import fixture as rgfix
from trarium.vn.rgss import marshal as M


class FakeTranslator:
    """Classification call: marks every sampled item whose text has a space and no underscore."""

    def __init__(self):
        self.classify_calls = 0

    def translate(self, text, target_lang="fa", instructions=""):
        self.classify_calls += 1
        nums = [m.group(1) for m in re.finditer(r"^(\d+)\. \[[^\]]*\] '([^']*)'", text, re.M)
                if " " in m.group(2) and "_" not in m.group(2)]
        return ",".join(nums) or "0"


class FakePipeline:
    def __init__(self):
        self.translator = FakeTranslator()

    def translate_block(self, text):
        return "\n\n".join("سلام دوست من " + " ".join(re.findall(r"⟦\d+⟧", p)) for p in text.split("\n\n"))


# ------------------------------------------------------------------ marshal
def test_marshal_roundtrip_and_types():
    s = M.RString(b"hi", [(M.Sym("E"), True)])
    tree = M.RArray([None, True, False, 0, 5, -3, 200, -200, 70000, -70000, 2**29, M.Sym("abc"), M.Sym("abc"),
                     s, s, M.RHash([(1, s)]), M.RObject(M.Sym("RPG::X"), [(M.Sym("@a"), 1)]),
                     M.RUser(b"u", M.Sym("Table"), b"\x00\x01")])
    blob = M.dumps(tree)
    back = M.loads(blob)
    assert back[3:11] == [0, 5, -3, 200, -200, 70000, -70000, 2**29]
    assert back[13] is back[14] and back[11] == "abc"           # object link and symlink restored
    assert M.dumps(back) == blob
    with pytest.raises(M.MarshalError):
        M.loads(b"\x04\x09")
    with pytest.raises(M.MarshalError):
        M.loads(b"\x04\x08@\x06")                               # dangling object link


def test_marshal_depth_guard():
    blob = b"\x04\x08" + b"[\x06" * 1000 + b"0"
    with pytest.raises(M.MarshalError):
        M.loads(blob)


@pytest.mark.parametrize("ace", [True, False])
def test_rgss_extract_apply(tmp_path, ace):
    rgfix.build(tmp_path, ace)
    for p in (tmp_path / "Data").iterdir():
        assert M.dumps(M.loads(p.read_bytes())) == p.read_bytes()
    assert detect_engine(tmp_path) == "rgss"
    items, docs = rg.extract(tmp_path)
    kinds = [i.kind for i in items]
    assert kinds.count("say") == 1 and kinds.count("choice") == 2 and kinds.count("db") == 2
    say = next(i for i in items if i.kind == "say")
    assert say.text == "Hello, \\N[1]! Welcome to the \\C[2]village\\C[0] of Ashwood."
    trs = []
    for i in items:
        trs.append({"say": "سلام \\N[1]! به دهکدهٔ \\C[2]اش‌وود\\C[0] خوش آمدی، امیدوارم اینجا بهت خوش بگذره و "
                           "بمونی پیش ما.", "choice": "بله", "db": "معجون"}[i.kind])
    rep = rg.apply(items, trs, docs)
    assert rep["written"] == 5
    mp = M.loads(next((tmp_path / "Data").glob("Map001*")).read_bytes())
    lst = mp.get("@events").pairs[0][1].get("@pages")[0].get("@list")
    texts = [c.get("@parameters")[0].text() for c in lst if c.get("@code") == 401 or (not ace and c.get("@code") == 101)]
    joined = "".join(texts)
    assert "\\N[1]" in joined and "\\C[2]" in joined and "\\C[0]" in joined
    assert any(0xFB50 <= ord(ch) <= 0xFEFF for ch in joined)          # pre-shaped presentation forms
    if ace:
        assert lst[1].get("@parameters")[0].ivars                    # UTF-8 encoding ivar kept
    assert [c.get("@code") for c in lst][-3:] == [102, 402, 0]


def test_rgss_archived_detected(tmp_path):
    (tmp_path / "Game.rgss3a").write_bytes(b"RGSSAD\0\3" + b"\0" * 16)
    assert detect_engine(tmp_path) == "rgss-archived"
    info = vn_tab.inspect_folder(tmp_path)
    assert info["engine"] == "rgss-archived" and "rgss3a" in info["note"]


# ------------------------------------------------------------------ generic
def _generic_game(root: Path) -> Path:
    g = root / "MysteryGame"
    (g / "lang").mkdir(parents=True)
    (g / "scripts").mkdir()
    (g / "lang" / "en.json").write_text(json.dumps(
        {"menu_start": "Start the game now", "sprite": "spr_hero_idle", "dialog": ["Hello traveler, welcome to {town}!"]}),
        encoding="utf-8")
    (g / "scripts" / "main.lua").write_text('require("ui_module")\nsay("Where are you going, %s?")\n', encoding="utf-8")
    (g / "lang" / "ui.po").write_text('msgid ""\nmsgstr ""\n\nmsgid "Load saved game"\nmsgstr ""\n', encoding="utf-8")
    (g / "story.txt").write_bytes("The night was cold and quiet.\n".encode("utf-16"))
    (g / "lang" / "menu.mo").write_bytes(gn.build_mo([(b"", b"Content-Type: text/plain; charset=UTF-8\n"),
                                                       (b"Quit to desktop", b"")]))
    (g / "engine.bin").write_bytes(b"\x00\x01\x02" + b"Press any key to continue\x00" + b"\xff" * 64)
    return g


def test_sniff_encodings():
    assert gn.sniff("héllo wörld".encode("utf-8")) == ("utf-8", b"")
    assert gn.sniff("こんにちは、世界".encode("shift_jis"))[0] == "shift_jis"
    assert gn.sniff("hello".encode("utf-16"))[0].startswith("utf-16")
    assert gn.sniff(b"\x00\x01\x02\x03" * 100) is None


def test_generic_scan_classify_apply(tmp_path):
    g = _generic_game(tmp_path)
    assert detect_engine(g) == "unknown"
    docs, binaries = gn.scan(g)
    assert binaries and binaries[0]["file"] == "engine.bin"
    units = gn.candidates(docs)
    tr = FakeTranslator()
    decision = gn.ai_classify(tr, units)
    assert tr.classify_calls == 1 and decision is not None
    sel = gn.select(units, decision)
    texts = {u.text for u in sel}
    assert {"Start the game now", "Hello traveler, welcome to {town}!", "Where are you going, %s?",
            "Load saved game", "The night was cold and quiet.", "Quit to desktop"} <= texts
    assert "spr_hero_idle" not in texts and "ui_module" not in texts
    trs = [("سلام {town}" if "{town}" in u.text else "کجا میری %s؟" if "%s" in u.text else "متن فارسی") for u in sel]
    rep = gn.apply(sel, trs, docs)
    assert rep["written"] == len(sel)
    data = json.loads((g / "lang" / "en.json").read_text(encoding="utf-8"))
    assert data["sprite"] == "spr_hero_idle" and data["dialog"][0] == "سلام {town}"
    assert 'say("کجا میری %s؟")' in (g / "scripts" / "main.lua").read_text(encoding="utf-8")
    assert 'msgstr "متن فارسی"' in (g / "lang" / "ui.po").read_text(encoding="utf-8")
    assert (g / "story.txt").read_bytes()[:2] in (b"\xff\xfe", b"\xfe\xff")      # BOM kept
    pairs = dict(gn.parse_mo((g / "lang" / "menu.mo").read_bytes()))
    assert pairs[b"Quit to desktop"].decode() == "متن فارسی"
    assert (g / "engine.bin").read_bytes().startswith(b"\x00\x01\x02Press any key")  # binary untouched


def test_generic_ai_unavailable_uses_heuristic(tmp_path):
    g = _generic_game(tmp_path)
    docs, _ = gn.scan(g)
    units = gn.candidates(docs)

    class Broken:
        def translate(self, *a, **k):
            raise RuntimeError("no key")
    assert gn.ai_classify(Broken(), units) is None
    sel = gn.select(units, None)
    assert any(u.text == "Start the game now" for u in sel)
    assert not any(u.text == "spr_hero_idle" for u in sel)


def test_generic_end_to_end_patch_zip(tmp_path):
    g = _generic_game(tmp_path)
    z = tmp_path / "mystery.zip"
    with zipfile.ZipFile(z, "w") as f:
        for p in g.rglob("*"):
            if p.is_file():
                f.write(p, p.relative_to(tmp_path))
    info = vn_tab.inspect_upload(z, "mystery.zip")
    assert info["engine"] == "generic" and info["lines"] >= 5 and info.get("binary_untouched")
    rep = vn_tab.run_upload(z, "mystery.zip", tmp_path / "out", FakePipeline())
    assert rep["engine"] == "generic" and rep["ai_classified"] and rep["translated"] >= 5
    assert rep["full_zip"] is None
    with zipfile.ZipFile(tmp_path / "out" / rep["patch_zip"]) as pz:
        names = pz.namelist()
    assert any(n.endswith("en.json") for n in names) and not any(n.endswith("engine.bin") for n in names)


def test_nested_archive_unpacked(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as f:
        f.writestr("text/intro.txt", "Welcome back to the old lighthouse, keeper.\n")
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as f:
        f.writestr("pack/content.zip", inner.getvalue())
    info = vn_tab.inspect_upload(outer, "outer.zip")
    assert info["engine"] == "generic" and info["lines"] == 1


def test_any_binary_file_reports_untouched(tmp_path):
    p = tmp_path / "blob.dat"
    p.write_bytes(b"\x00\x10" + b"Open the door slowly\x00" * 3 + b"\x00" * 100)
    info = vn_tab.inspect_upload(p, "blob.dat")
    assert info["engine"] == "unknown" and "باینری" in info["note"]


# ---------------------------------------------------------------------- iso
def _make_iso(path: Path, files: dict[str, bytes]) -> None:
    pycdlib = pytest.importorskip("pycdlib")
    iso = pycdlib.PyCdlib()
    iso.new(joliet=3)
    dirs = set()
    for name in files:
        parts = name.split("/")[:-1]
        for k in range(1, len(parts) + 1):
            d = "/".join(parts[:k])
            if d not in dirs:
                dirs.add(d)
                iso.add_directory("/" + d.upper(), joliet_path="/" + d)
    for name, data in files.items():
        iso.add_fp(io.BytesIO(data), len(data), "/" + name.upper().replace("-", "_") + ";1", joliet_path="/" + name)
    iso.write(str(path))
    iso.close()


def test_iso_gamemaker_and_generic(tmp_path):
    from trarium.vn.gms import fixture as gmsfix
    iso = tmp_path / "game.iso"
    _make_iso(iso, {"game/data.win": gmsfix.build(), "game/readme.txt": b"Thanks for playing our little game!\n"})
    assert vn_tab.is_iso(iso) and vn_tab.container_of(iso, "game.iso") == "iso"
    info = vn_tab.inspect_upload(iso, "game.iso")
    assert info["engine"] == "gamemaker" and info["container"] == "iso" and info["lines"] == 3
    rep = vn_tab.run_upload(iso, "game.iso", tmp_path / "out", FakePipeline())
    assert rep["translated"] == 3 and rep["full_zip"] is None
    assert iso.read_bytes()[0x8001:0x8006] == b"CD001"                       # ISO itself never rebuilt
    with zipfile.ZipFile(tmp_path / "out" / rep["patch_zip"]) as z:
        assert any(n.endswith("data.win") for n in z.namelist())


def test_iso_size_guard(tmp_path, monkeypatch):
    iso = tmp_path / "big.iso"
    _make_iso(iso, {"a.txt": b"x" * 300_000})
    monkeypatch.setenv("TRARIUM_VN_MAX_UNPACKED_MB", "0.1")
    monkeypatch.setattr(vn_tab, "_seven_zip_bin", lambda: None)
    with pytest.raises(vn_tab.VNInputError):
        vn_tab.inspect_upload(iso, "big.iso")


def test_not_iso():
    assert not vn_tab.is_iso(Path(__file__))
