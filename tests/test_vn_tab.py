"""تب ویژوال ناول: کمک‌کارهای بدون Streamlit، روی یک بازی Ren'Py ساختگی و بدون API."""
import re
import tempfile
import zipfile
from pathlib import Path

import pytest

from trarium import vn_tab

SCRIPT = '''define e = Character("Eileen")

label start:
    e "Hello, [name]! Welcome."
    "It was a quiet morning."
    menu:
        "Yes, please.":
            return
        "No thanks.":
            return
'''


class FakePipeline:
    def __init__(self):
        self.calls = 0

    def translate_block(self, text):
        self.calls += 1
        return "\n\n".join("ترجمه " + " ".join(re.findall(r"⟦\d+⟧", p)) for p in text.split("\n\n"))


def _renpy_zip(tmp: Path) -> Path:
    z = tmp / "Tiny.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Tiny/game/script.rpy", SCRIPT)
        f.writestr("Tiny/Tiny.exe", b"MZ")
    return z


def test_inspect_renpy_zip(tmp_path):
    before = set(Path(tempfile.gettempdir()).glob(vn_tab.TMP_PREFIX + "inspect_*"))
    info = vn_tab.inspect_upload(_renpy_zip(tmp_path))
    assert info["engine"] == "renpy"
    assert info["lines"] == 5          # نام + ۲ دیالوگ + ۲ گزینه
    assert info["by_kind"]["menu"] == 2
    after = set(Path(tempfile.gettempdir()).glob(vn_tab.TMP_PREFIX + "inspect_*"))
    assert after == before             # پوشهٔ موقت پاک شد


def test_any_file_type_accepted_without_engine(tmp_path):
    # همهٔ نوع فایل‌ها پذیرفته می‌شوند؛ فایلی که آرشیو نیست به مسیر عمومی می‌رود و چیزی ترجمه‌پذیر ندارد
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"\x00\x01binary\x00junk")
    info = vn_tab.inspect_upload(bad)
    assert info["engine"] == "unknown" and info["container"] == "file"
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as f:
        f.writestr("readme.txt", "hi")
    info = vn_tab.inspect_upload(other)
    assert info["engine"] in ("unknown", "generic") and not info.get("lines")


def test_size_limit_and_zip_slip(tmp_path, monkeypatch):
    z = _renpy_zip(tmp_path)
    monkeypatch.setenv("TRARIUM_VN_MAX_MB", "0.0001")
    with pytest.raises(vn_tab.VNInputError):
        vn_tab.inspect_upload(z)
    monkeypatch.delenv("TRARIUM_VN_MAX_MB")
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as f:
        f.writestr("../escape.rpy", "x")
    with pytest.raises(vn_tab.VNInputError):
        vn_tab.inspect_upload(evil)


def test_apk_without_tools(tmp_path, monkeypatch):
    monkeypatch.delenv("TRARIUM_RENPY_SDK", raising=False)
    apk = tmp_path / "g.apk"
    with zipfile.ZipFile(apk, "w") as f:
        f.writestr("assets/x-game/x-script.rpyc", b"x")
        f.writestr("assets/x-game/x-script_version.rpyc", b"x")
    info = vn_tab.inspect_upload(apk)
    assert info["engine"] == "renpy-apk" and info["script_files"] == 1 and info["apk_ready"] is False


def test_run_zip_with_progress_and_cleanup(tmp_path):
    seen = []
    prog = vn_tab.ProgressPipeline(FakePipeline(), 5, lambda frac, done: seen.append(frac))
    rep = vn_tab.run_zip(_renpy_zip(tmp_path), tmp_path / "out", prog)
    assert rep["engine"] == "renpy" and rep["translated"] == 5
    assert (tmp_path / "out" / rep["patch_zip"]).exists()
    assert seen and all(0 < f < 1 for f in seen)
    assert not Path(rep["game_root"]).exists()   # پوشهٔ کاری translate_game پاک شد


# ------------------------------------------------------------- more input formats
def _zip_bytes() -> bytes:
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as f:
        f.writestr("game/script.rpy", SCRIPT)
    return buf.getvalue()


def test_zip_appended_exe_gives_patch_only(tmp_path):
    exe = tmp_path / "Tiny.exe"
    exe.write_bytes(b"MZ" + b"\0" * 4000 + _zip_bytes())   # استاب ویندوز + زیپ چسبیده
    original = exe.read_bytes()
    info = vn_tab.inspect_upload(exe)
    assert info["container"] == "exe" and info["engine"] == "renpy" and info["lines"] == 5
    rep = vn_tab.run_upload(exe, "Tiny.exe", tmp_path / "out", FakePipeline())
    assert rep["container"] == "exe" and rep["translated"] == 5
    assert rep["full_zip"] is None and (tmp_path / "out" / rep["patch_zip"]).exists()
    assert exe.read_bytes() == original                        # exe دست نخورد


def test_unopenable_exe_explains(tmp_path):
    exe = tmp_path / "Packed.exe"
    exe.write_bytes(b"MZ" + b"\x90" * 5000)
    with pytest.raises(vn_tab.VNInputError) as e:
        vn_tab.inspect_upload(exe)
    assert "زیپ" in str(e.value)


def test_tar_gz(tmp_path):
    import tarfile
    src = tmp_path / "Tiny" / "game"
    src.mkdir(parents=True)
    (src / "script.rpy").write_text(SCRIPT, encoding="utf-8")
    tgz = tmp_path / "Tiny.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(tmp_path / "Tiny", arcname="Tiny")
    info = vn_tab.inspect_upload(tgz)
    assert info["container"] == "tar" and info["engine"] == "renpy" and info["lines"] == 5


def _rpa(path: Path, files: dict, index_obj=None):
    import pickle, zlib
    key = 0x42
    body, index, off = b"", {}, 34
    for name, data in files.items():
        index[name.encode()] = [(off ^ key, len(data) ^ key, b"")]
        body += data; off += len(data)
    idx = zlib.compress(pickle.dumps(index_obj if index_obj is not None else index, protocol=2))
    header = f"RPA-3.0 {off:016x} {key:08x}\n".encode()
    assert len(header) == 34
    path.write_bytes(header + body + idx)


def test_rpa_upload(tmp_path):
    rpa = tmp_path / "archive.rpa"
    _rpa(rpa, {"script.rpy": SCRIPT.encode()})
    info = vn_tab.inspect_upload(rpa)
    assert info["container"] == "rpa" and info["engine"] == "renpy" and info["lines"] == 5


def test_rpa_with_code_in_index_is_refused(tmp_path):
    rpa = tmp_path / "evil.rpa"
    _rpa(rpa, {"script.rpy": b"x"}, index_obj={b"script.rpy": [(1, 1, b"")], b"x": Path("/tmp")})
    info = vn_tab.inspect_upload(rpa)
    assert info["lines"] == 0 and "RPA" in info.get("note", "")


def test_7z_without_tools_is_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(vn_tab, "_seven_zip_bin", lambda: None)
    f = tmp_path / "g.7z"
    f.write_bytes(vn_tab.SEVEN_Z_SIG + b"\0" * 64)
    with pytest.raises(vn_tab.VNInputError) as e:
        vn_tab.inspect_upload(f)
    assert "zip" in str(e.value)


def test_folder_upload_to_zip(tmp_path):
    class F:
        def __init__(self, name, data):
            self.name, self._d = name, data
        def getvalue(self):
            return self._d
    out = tmp_path / "f.zip"
    vn_tab.folder_to_zip([F("Tiny/game/script.rpy", SCRIPT.encode()), F("../evil.txt", b"x")], out)
    assert zipfile.ZipFile(out).namelist() == ["Tiny/game/script.rpy"]
    assert vn_tab.inspect_upload(out)["lines"] == 5
