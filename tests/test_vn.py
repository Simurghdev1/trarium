import json
import zipfile
from pathlib import Path

from trarium.vn import detect_engine, translate_game
from trarium.vn import renpy as rp

SCRIPT = '''define e = Character("Eileen", color="#c8ffc8")
default name = "Bob"

label start:
    scene bg room
    show eileen happy
    e "Hello, [name]! Welcome to {b}our{/b} story."
    "It was a quiet \\"morning\\"."
    e happy "Do you want tea?" with dissolve
    $ points = 1
    menu:
        "Yes, please.":
            jump tea
        "No thanks." if points > 0:
            return
    python:
        x = "do not touch me"
    textbutton _("Start") action Start()
    jump tea

label tea:
    e "Here you go."
    return
'''


class FakePipeline:
    """هر متن را به یک جملهٔ فارسی تبدیل می‌کند و توکن‌ها را نگه می‌دارد."""

    def __init__(self, drop_tokens=False):
        self.calls = 0
        self.drop = drop_tokens

    def translate_block(self, text):
        self.calls += 1
        parts = text.split("\n\n")
        out = []
        for p in parts:
            import re
            toks = re.findall(r"⟦\d+⟧", p)
            if self.drop:
                toks = toks[1:]
            out.append("ترجمه " + " ".join(toks) + (" پایان" if "\n" not in p else "\nخط دوم"))
        return "\n\n".join(out)


def _renpy_game(tmp: Path) -> Path:
    g = tmp / "MyGame" / "game"
    g.mkdir(parents=True)
    (g / "script.rpy").write_text(SCRIPT, encoding="utf-8")
    (g / "script.rpyc").write_bytes(b"x")
    (tmp / "MyGame" / "MyGame.exe").write_bytes(b"MZ")
    return tmp / "MyGame"


def test_detect_renpy(tmp_path):
    assert detect_engine(_renpy_game(tmp_path)) == "renpy"


def test_renpy_extract_kinds(tmp_path):
    root = _renpy_game(tmp_path)
    segs = rp.extract(root / "game")
    texts = [s.text for s in segs]
    assert "Hello, [name]! Welcome to {b}our{/b} story." in texts
    assert 'It was a quiet "morning".' in texts
    assert "Do you want tea?" in texts and "Yes, please." in texts and "No thanks." in texts
    assert "Start" in texts and "Eileen" in texts
    assert "do not touch me" not in texts and "Bob" not in texts and "#c8ffc8" not in texts


def test_renpy_full_build(tmp_path):
    root = _renpy_game(tmp_path)
    rep = translate_game(root, tmp_path / "out", FakePipeline())
    assert rep["engine"] == "renpy" and rep["translated"] == rep["segments"] > 0
    with zipfile.ZipFile(tmp_path / "out" / rep["patch_zip"]) as z:
        names = z.namelist()
        assert "game/script.rpy" in names and "game/trarium_fa.rpy" in names and "game/trarium_fa/Sahel.ttf" in names
        assert "MyGame.exe" not in names
        new = z.read("game/script.rpy").decode()
    assert "[name]" in new and "{b}" in new and "{/b}" in new
    assert 'x = "do not touch me"' in new and "jump tea" in new and "with dissolve" in new
    assert 'if points > 0:' in new and "ترجمه" in new
    with zipfile.ZipFile(tmp_path / "out" / rep["full_zip"]) as z:
        assert "MyGame.exe" in z.namelist()


def test_renpy_rejects_lost_tokens(tmp_path):
    root = _renpy_game(tmp_path)
    rep = translate_game(root, tmp_path / "out", FakePipeline(drop_tokens=True))
    assert rep["rejected"]["lost_code"] >= 1
    with zipfile.ZipFile(tmp_path / "out" / rep["patch_zip"]) as z:
        assert "Hello, [name]! Welcome to {b}our{/b} story." in z.read("game/script.rpy").decode()


def _rm_game(tmp: Path) -> Path:
    d = tmp / "RM" / "www" / "data"
    d.mkdir(parents=True)
    (tmp / "RM" / "www" / "fonts").mkdir()
    (tmp / "RM" / "www" / "fonts" / "gamefont.css").write_text("x")
    (d / "System.json").write_text(json.dumps({"gameTitle": "Hero Quest", "currencyUnit": "G", "elements": ["", "Fire"],
                                               "terms": {"basic": ["Level"], "commands": ["Fight"], "params": ["Max HP"],
                                                         "messages": {"victory": "%1 was victorious!"}}}))
    (d / "MapInfos.json").write_text("[null]")
    (d / "Actors.json").write_text(json.dumps([None, {"name": "Harold", "nickname": "", "profile": "A brave knight."}]))
    (d / "Map001.json").write_text(json.dumps({"displayName": "Village", "events": [None, {"pages": [{"list": [
        {"code": 101, "parameters": ["", 0, 0, 2, "Harold"]},
        {"code": 401, "parameters": ["Hello \\N[1]!"]},
        {"code": 401, "parameters": ["Nice weather."]},
        {"code": 102, "parameters": [["Yes", "No"], 1]},
        {"code": 402, "parameters": [0, "Yes"]},
        {"code": 0, "parameters": []}]}]}]}))
    return tmp / "RM"


def test_rpgmaker_build(tmp_path):
    root = _rm_game(tmp_path)
    assert detect_engine(root) == "rpgmaker"
    rep = translate_game(root, tmp_path / "out", FakePipeline())
    assert rep["translated"] == rep["segments"] >= 12
    with zipfile.ZipFile(tmp_path / "out" / rep["patch_zip"]) as z:
        m = json.loads(z.read("www/data/Map001.json"))
        css = z.read("www/fonts/gamefont.css").decode()
    lst = m["events"][1]["pages"][0]["list"]
    assert "\\N[1]" in lst[1]["parameters"][0]
    assert lst[2]["parameters"][0] == "خط دوم"
    assert all("ترجمه" in c for c in lst[3]["parameters"][0])
    assert "Sahel.ttf" in css
