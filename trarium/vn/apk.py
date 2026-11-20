"""APK اندروید بازی‌های Ren'Py: فقط متن و فونت عوض می‌شود.

مسیر: assets/x-game → اسکریپت‌های .rpyc → unrpyc → .rpy → ترجمه →
کامپایل با همان نسخهٔ SDK خود بازی → جایگزینی .rpyc ها و فونت در APK → امضا.

امنیت: هیچ کد، کتابخانه، مجوز (permission) یا AndroidManifest اضافه یا عوض
نمی‌شود. فقط فایل‌های اسکریپت ترجمه‌شده، فایل فونت (با همان اسم قبلی) و
تنظیم چینش متن عوض می‌شوند. امضای اصلی سازنده قابل حفظ نیست، پس APK با یک
کلید تازه امضا می‌شود (نسخهٔ اصلی باید قبل از نصب پاک شود).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

from trarium.vn import renpy as rp

FONT = Path(__file__).resolve().parents[1] / "fonts" / "Sahel.ttf"
ALLOWED_CHANGES = re.compile(r"^assets/x-game/(x-[^/]+\.rpyc|x-[^/]+\.ttf)$")


def _strip(path: str) -> str:
    return "/".join(p[2:] if p.startswith("x-") else p for p in path.split("/"))


def _xpath(rel: str) -> str:
    return "assets/x-game/" + "/".join("x-" + p for p in rel.split("/"))


def unpack(apk: Path, game: Path) -> dict:
    game.mkdir(parents=True, exist_ok=True)
    info = {"rpyc": [], "fonts": [], "version": None}
    with zipfile.ZipFile(apk) as z:
        for n in z.namelist():
            if not n.startswith("assets/x-game/"):
                continue
            rel = _strip(n[len("assets/x-game/"):])
            out = game / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(n))
            if rel.endswith(".rpyc"):
                info["rpyc"].append(rel)
            elif rel.endswith((".ttf", ".otf")):
                info["fonts"].append(rel)
    sv = game / "script_version.rpyc"
    if sv.exists():
        import zlib
        raw = sv.read_bytes()
        try:
            data = zlib.decompress(raw)
        except Exception:
            data = raw
        m = re.search(rb"script_version = \((\d+), (\d+), (\d+)\)", data)
        if m:
            info["version"] = ".".join(x.decode() for x in m.groups())
    return info


def decompile(game: Path, unrpyc: Path) -> list[str]:
    files = [str(p) for p in game.glob("*.rpyc") if p.name != "script_version.rpyc"]
    subprocess.run(["python3", str(unrpyc / "unrpyc.py"), *files], check=True, capture_output=True)
    return sorted(p.name for p in game.glob("*.rpy"))


def font_of(game: Path) -> str | None:
    for p in game.glob("*.rpy"):
        m = re.search(r'style\.default\.font\s*=\s*"([^"]+)"', p.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return None


def right_align(game: Path, width: int):
    """فقط چینش: دیالوگ فارسی از راست شروع شود (بدون هیچ کد اجرایی جدید)."""
    p = game / "options.rpy"
    s = p.read_text(encoding="utf-8")
    line = "    style.default.font ="
    if line in s and "say_dialogue.text_align" not in s:
        i = s.index(line)
        s = s[:i] + f"    style.say_dialogue.text_align = 1.0\n    style.say_dialogue.min_width = {width}\n" + s[i:]
        p.write_text(s, encoding="utf-8")
    # متن پیش‌شکل‌داده‌شده فارسی در حالت تایپ‌شدن از چپ ظاهر می‌شود؛ متن یک‌جا نمایش داده شود
    s = p.read_text(encoding="utf-8")
    s2 = re.sub(r"config\.default_text_cps\s*=\s*\d+", "config.default_text_cps = 0", s)
    if s2 != s:
        p.write_text(s2, encoding="utf-8")


def compile_game(sdk: Path, project: Path) -> str:
    r = subprocess.run([str(sdk / "renpy.sh"), str(project), "compile"], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "SDL_AUDIODRIVER": "dummy", "SDL_VIDEODRIVER": "dummy",
                            "RENPY_PLATFORM": "linux-x86_64", "HOME": str(project)})
    return (r.stdout + r.stderr)[-3000:]


def repack(apk: Path, replacements: dict[str, bytes], out: Path, workdir: Path) -> dict:
    bad = [k for k in replacements if not ALLOWED_CHANGES.match(k)]
    if bad:
        raise RuntimeError(f"تغییر غیرمجاز در APK: {bad}")
    unsigned = workdir / "unsigned.apk"
    with zipfile.ZipFile(apk) as src, zipfile.ZipFile(unsigned, "w") as dst:
        for item in src.infolist():
            if item.filename.startswith("META-INF/"):
                continue
            data = replacements.get(item.filename, src.read(item.filename))
            dst.writestr(item, data, compress_type=item.compress_type)
    aligned = workdir / "aligned.apk"
    tools = sorted(Path("/usr/local/lib/android/sdk/build-tools").glob("*/zipalign")) if Path("/usr/local/lib/android/sdk/build-tools").exists() else []
    zipalign = str(tools[-1]) if tools else shutil.which("zipalign")
    apksigner = str(tools[-1].parent / "apksigner") if tools else shutil.which("apksigner")
    if not (zipalign and apksigner):
        shutil.copy(unsigned, out.with_suffix(".unsigned.apk"))
        return {"signed": False}
    subprocess.run([zipalign, "-f", "-p", "4", str(unsigned), str(aligned)], check=True)
    ks = workdir / "trarium.keystore"
    if not ks.exists():
        subprocess.run(["keytool", "-genkeypair", "-keystore", str(ks), "-storepass", "trarium", "-keypass", "trarium",
                        "-alias", "trarium", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                        "-dname", "CN=Trarium Persian Patch"], check=True, capture_output=True)
    subprocess.run([apksigner, "sign", "--ks", str(ks), "--ks-pass", "pass:trarium", "--out", str(out), str(aligned)], check=True)
    v = subprocess.run([apksigner, "verify", "--print-certs", str(out)], capture_output=True, text=True)
    return {"signed": True, "verify_ok": v.returncode == 0}


def audit(original: Path, patched: Path) -> dict:
    """ثابت می‌کند جز اسکریپت‌ها و فونت چیزی عوض نشده."""
    def digests(p):
        with zipfile.ZipFile(p) as z:
            return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if not n.startswith("META-INF/")}
    a, b = digests(original), digests(patched)
    changed = sorted(n for n in a if b.get(n) != a[n])
    return {"files": len(a), "changed": changed, "added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
            "manifest_same": a.get("AndroidManifest.xml") == b.get("AndroidManifest.xml"),
            "dex_same": a.get("classes.dex") == b.get("classes.dex"),
            "libs_same": all(a[n] == b.get(n) for n in a if n.startswith("lib/")),
            "only_allowed": all(ALLOWED_CHANGES.match(n) for n in changed)}
