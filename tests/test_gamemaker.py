"""GameMaker Studio 1/2 (data.win) + GameMaker 8 detection + 2 GB upload limits.
Synthetic fixtures only; no game data."""
import io
import re
import struct
import zipfile
from pathlib import Path

import pytest

from trarium import vn_tab
from trarium.vn.detect import detect_engine
from trarium.vn.gms import fixture
from trarium.vn.gms import text as tx
from trarium.vn.gms.build import apply, extract, find_data_file, inspect, translate_file
from trarium.vn.gms.font import parse_fonts, read_texture
from trarium.vn.gms.iff import DataWin, GMSError

ROOT = Path(__file__).resolve().parents[1]


class FakePipeline:
    def translate_block(self, text):
        return "\n\n".join("سلام دوست من " + " ".join(re.findall(r"⟦\d+⟧", p)) for p in text.split("\n\n"))


FA = ["* سلام!&* من فلاوی هستم.&* فلاوی، گل!/", "* حس می‌کنی قراره \\Rروز\\W بدی داشته باشی./%%",
      "برای شروع Z را بزن"]


@pytest.mark.parametrize("gms2", [False, True])
def test_parse_and_filter(gms2):
    dw = DataWin(fixture.build(gms2=gms2))
    assert dw.order == ["GEN8", "SPRT", "FONT", "TPAG", "STRG", "TXTR", "AUDO"]
    assert dw.strings()[4].startswith("* Howdy!")
    segs, stats = extract(dw)
    texts = [s.text for s in segs]
    assert texts == fixture.DEFAULT_STRINGS[4:7]
    assert "spr_player" not in texts and "settings.ini" not in texts and "General" not in texts
    assert stats["identifiers"] >= 3 and stats["skipped_single_words"] == 2
    f = parse_fonts(dw)[0]
    assert f.name == "fnt_main" and f.kerning == gms2 and ord("A") in f.glyphs


@pytest.mark.parametrize("gms2", [False, True])
def test_patch_roundtrip_appends_only(gms2):
    original = fixture.build(gms2=gms2)
    dw = DataWin(original)
    segs, _ = extract(dw)
    rep = apply(dw, segs, FA)
    assert rep["written"] == 3 and rep["fonts"]["patched"][0]["added"] == rep["glyph_forms"] > 20
    out = dw.to_bytes()
    assert len(out) > len(original)
    # only pointer words / sizes changed inside the original range; the bytes of every old string are untouched
    diff = [i for i in range(len(original)) if original[i] != out[i]]
    old = DataWin(original)
    for p in old.string_table():
        n = old.u32(p)
        assert out[p:p + 4 + n] == original[p:p + 4 + n]
    assert len(diff) < 80
    dw2 = DataWin(out)
    s = dw2.strings()
    assert s[0:4] == fixture.DEFAULT_STRINGS[0:4] and s[7:] == fixture.DEFAULT_STRINGS[7:]
    assert s[4].endswith("/") and s[4].count("&") == 2 and "\uFEE1" in s[4]   # shaped (final meem of سلام)
    assert s[5].endswith("/%%") and "\\R" in s[5] and "\\W" in s[5]
    f = parse_fonts(dw2)[0]
    for ch in tx.glyphs_needed([s[4], s[5], s[6]]):
        assert ord(ch) in f.glyphs
    # glyph pixels exist in the enlarged texture, old glyph 'A' still where it was
    tex = read_texture(dw2, 0)
    tp = struct.unpack_from("<10H", dw2.d, f.tpag)
    x, y, w, h = f.glyphs[0xFEE1][:4]
    assert tex.getchannel("A").crop((tp[0] + x, tp[1] + y, tp[0] + x + w, tp[1] + y + h)).getbbox()
    assert tex.width & (tex.width - 1) == 0 and tex.height & (tex.height - 1) == 0
    assert f.glyphs[ord("A")][:4] == parse_fonts(old)[0].glyphs[ord("A")][:4]
    assert sorted(f.glyphs) == [struct.unpack_from("<H", dw2.d, p)[0] for p in f.glyph_ptrs]


def test_visual_order_and_codes():
    s = tx.prepare("* سلام دنیا!&* خوبی؟/", "* Hello world!&* How are you?/")
    assert s.endswith("/") and s.count("&") == 1
    first = s.split("&")[0]
    assert first.endswith("*") and first.startswith("!")          # RTL: '*' drawn rightmost
    # leading face code stays first, LTR run keeps its order, pause code kept
    v = tx.visual_line("\\E2* من LV 5 دارم^1")
    assert v.startswith("\\E2") and "LV 5" in v and "^1" in v
    # coloured word keeps its colour, colour reset after it
    c = tx.visual_line("یک \\Rقرمز\\W دو")
    assert re.search(r"\\R[^\\]+\\W", c)
    # wrap: long Persian line gets extra line breaks with the source's separator
    long = tx.prepare("کلمه " * 20, "* short line#* x")
    assert long.count("#") >= 2


def test_protect_regex_locks_gamemaker_codes():
    src = "\\E1* Hi %s, {0}^1&next/%% \\[C]"
    toks = [m.group(0) for m in tx.PROTECT.finditer(src)]
    for want in ("\\E1", "%s", "{0}", "^1", "&", "/%%", "\\[C]"):
        assert want in toks


def test_bad_files_rejected(tmp_path):
    with pytest.raises(GMSError):
        DataWin(b"FORM\x10\0\0\0GEN8" + b"\0" * 4)
    with pytest.raises(GMSError):
        DataWin(b"PK\x03\x04" + b"\0" * 20)
    bad = bytearray(fixture.build())
    struct.pack_into("<I", bad, 4, len(bad) * 2)             # FORM size beyond the file
    with pytest.raises(GMSError):
        DataWin(bytes(bad))


def test_translate_file_and_inspect(tmp_path):
    p = tmp_path / "data.win"
    p.write_bytes(fixture.build())
    info = inspect(p)
    assert info["lines"] == 3 and info["fonts"] == 1
    rep = translate_file(p, lambda texts: (FA[:len(texts)], {}))
    assert rep["written"] == 3
    assert "\uFEE1" in DataWin(p.read_bytes()).strings()[4]


def _game_folder(tmp: Path, name="data.win") -> Path:
    g = tmp / "MyGame"
    g.mkdir()
    (g / name).write_bytes(fixture.build())
    (g / "options.ini").write_text("[Windows]\n")
    return g


def test_detect_folder_zip_and_raw(tmp_path):
    g = _game_folder(tmp_path)
    assert detect_engine(g) == "gamemaker"
    assert find_data_file(tmp_path).name == "data.win"
    z = tmp_path / "game.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.write(g / "data.win", "MyGame/data.win")
        f.write(g / "options.ini", "MyGame/options.ini")
    info = vn_tab.inspect_upload(z, "game.zip")
    assert info["engine"] == "gamemaker" and info["lines"] == 3
    raw = vn_tab.inspect_upload(g / "data.win", "data.win")
    assert raw["engine"] == "gamemaker" and raw["container"] == "gmsdata"
    droid = tmp_path / "game.droid"
    droid.write_bytes(fixture.build(gms2=True))
    assert vn_tab.inspect_upload(droid, "game.droid")["engine"] == "gamemaker"


def test_run_upload_builds_patch_and_keeps_upload(tmp_path):
    src = tmp_path / "data.win"
    original = fixture.build()
    src.write_bytes(original)
    rep = vn_tab.run_upload(src, "data.win", tmp_path / "out", FakePipeline())
    assert rep["engine"] == "gamemaker" and rep["translated"] == 3 and rep["full_zip"] is None
    assert src.read_bytes() == original                      # hard-linked upload not modified
    with zipfile.ZipFile(tmp_path / "out" / rep["patch_zip"]) as z:
        patched = z.read("data.win")
    assert patched != original
    assert any(ord(c) >= 0xFB50 for c in DataWin(patched).strings()[4])


def test_apk_with_game_droid(tmp_path):
    apk = tmp_path / "g.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("AndroidManifest.xml", b"\0")
        z.writestr("assets/game.droid", fixture.build(gms2=True))
    info = vn_tab.inspect_upload(apk, "g.apk")
    assert info["engine"] == "gamemaker-apk" and info["lines"] == 3
    rep = vn_tab.run_apk_gms(apk, tmp_path / "out", FakePipeline())
    assert rep["translated"] == 3
    with zipfile.ZipFile(tmp_path / "out" / rep["patch_zip"]) as z:
        assert "assets/game.droid" in z.namelist()


def test_gm8_detection_is_content_based(tmp_path):
    from trarium.vn.gm8.auto import find_gm8_exe, is_gm8_exe
    from trarium.vn.gm8.exe import GM80_SIG, GM80_SIG_POS
    fake = tmp_path / "Game.exe"
    fake.write_bytes(b"MZ" + b"\0" * 100)
    assert not is_gm8_exe(fake)
    blob = bytearray(GM80_SIG_POS + 16)
    blob[:2] = b"MZ"
    blob[GM80_SIG_POS:GM80_SIG_POS + 8] = GM80_SIG
    fake.write_bytes(bytes(blob))
    assert is_gm8_exe(fake) and find_gm8_exe(tmp_path) == fake
    assert detect_engine(tmp_path) == "gm8"


def test_upload_limits_default_2gb(monkeypatch):
    monkeypatch.delenv("TRARIUM_VN_MAX_MB", raising=False)
    monkeypatch.delenv("TRARIUM_VN_MAX_UNPACKED_MB", raising=False)
    assert vn_tab.max_upload_bytes() == 2048 * 2**20
    assert vn_tab.max_unpacked_bytes() == 6144 * 2**20
    monkeypatch.setenv("TRARIUM_VN_MAX_UNPACKED_MB", "100")
    assert vn_tab.max_unpacked_bytes() == 100 * 2**20
    assert re.search(r"^maxUploadSize\s*=\s*2048$", (ROOT / ".streamlit" / "config.toml").read_text(), re.M)
    docker = (ROOT / "Dockerfile").read_text()
    assert "STREAMLIT_SERVER_MAX_UPLOAD_SIZE=2048" in docker and "TRARIUM_VN_MAX_MB=2048" in docker
    for ext in ("win", "unx", "ios", "droid", "apk", "exe"):
        assert ext in vn_tab.UPLOAD_TYPES


def test_save_upload_streams_and_enforces_cap(tmp_path, monkeypatch):
    class Up(io.BytesIO):
        pass
    up = Up(b"x" * (3 * 2**20))
    n = vn_tab.save_upload(up, tmp_path / "a.bin")
    assert n == 3 * 2**20 and (tmp_path / "a.bin").stat().st_size == n
    monkeypatch.setenv("TRARIUM_VN_MAX_MB", "1")
    with pytest.raises(vn_tab.VNInputError):
        vn_tab.save_upload(Up(b"x" * (9 * 2**20)), tmp_path / "b.bin")


def test_symlink_data_file_ignored(tmp_path):
    real = tmp_path / "outside.win"
    real.write_bytes(fixture.build())
    g = tmp_path / "g"
    g.mkdir()
    (g / "data.win").symlink_to(real)
    assert find_data_file(g) is None
