"""تب «ویژوال ناول» در رابط Streamlit.

فقط رابط و کمک‌کار است؛ کار اصلی را trarium.vn انجام می‌دهد (detect/renpy/rpgmaker/
tyrano/apk/build). توابع بالای فایل به Streamlit وابسته نیستند و تست می‌شوند.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

ENGINE_LABELS = {
    "renpy": "Ren'Py",
    "rpgmaker": "RPG Maker MV/MZ",
    "tyrano": "TyranoScript / TyranoBuilder",
    "renpy-apk": "Ren'Py (APK اندروید)",
    "unknown": "ناشناخته",
}
VN_SHORT_PROMPT = ("این‌ها دیالوگ یک بازی ویژوال ناول هستند: فارسی محاوره‌ای، طبیعی و کوتاه بنویس، ادبی ننویس. "
                   "توکن‌های ⟦0⟧ و مانند آن را دقیقاً همان‌طور نگه دار. بین هر پاراگراف یک خط خالی بگذار.")
TMP_PREFIX = "trarium_vnui_"


def max_upload_bytes() -> int:
    return int(float(os.environ.get("TRARIUM_VN_MAX_MB", "200")) * 1024 * 1024)


def max_unpacked_bytes() -> int:
    return int(float(os.environ.get("TRARIUM_VN_MAX_UNPACKED_MB", "1024")) * 1024 * 1024)


class VNInputError(RuntimeError):
    """خطای ورودی کاربر با پیام فارسی."""


SEVEN_Z_SIG = b"7z\xbc\xaf\x27\x1c"
RAR_SIGS = (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")
# پسوندهایی که فایل‌آپلودر می‌پذیرد (tar.gz/tgz/tar.bz2/tar.xz با gz/bz2/xz پوشش داده می‌شوند).
UPLOAD_TYPES = ["zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "exe", "rpa", "apk"]
EXE_HELP = ("این فایل exe باز نشد (احتمالاً با Enigma یا مانند آن بسته‌بندی شده یا بازی GameMaker تک‌فایلی است). "
            "پوشهٔ کامل بازی را زیپ کنید و همان را بدهید، یا از «آپلود پوشه» استفاده کنید.")


def _check_inside(dest: Path) -> None:
    """بعد از استخراج: هیچ لینک نمادین یا فایلی بیرون از پوشه نماند و حجم از سقف بیشتر نشود."""
    root = dest.resolve()
    total = 0
    for p in list(dest.rglob("*")):
        if p.is_symlink():
            p.unlink(); continue
        if root not in p.resolve().parents:
            raise VNInputError(f"مسیر نامعتبر در آرشیو: {p.name}")
        if p.is_file():
            total += p.stat().st_size
    if total > max_unpacked_bytes():
        raise VNInputError(f"حجم بازشدهٔ آرشیو ({total / 2**20:.0f} مگابایت) از سقف مجاز بیشتر است.")


def _safe_extract(archive: Path, dest: Path) -> None:
    """استخراج زیپ (یا exe با زیپ چسبیده) با سقف حجم و بدون مسیرهای بیرون‌زده (zip-slip)."""
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        total = sum(i.file_size for i in infos)
        if total > max_unpacked_bytes():
            raise VNInputError(f"حجم بازشدهٔ زیپ ({total / 2**20:.0f} مگابایت) از سقف مجاز بیشتر است.")
        root = dest.resolve()
        for info in infos:
            target = (dest / info.filename).resolve()
            if target != root and root not in target.parents:
                raise VNInputError(f"مسیر نامعتبر در زیپ: {info.filename}")
        z.extractall(dest)


def _extract_tar(archive: Path, dest: Path) -> None:
    import tarfile
    with tarfile.open(archive) as t:
        members = t.getmembers()
        if sum(m.size for m in members if m.isfile()) > max_unpacked_bytes():
            raise VNInputError("حجم بازشدهٔ آرشیو tar از سقف مجاز بیشتر است.")
        try:
            t.extractall(dest, filter="data")
        except TypeError:  # پایتون قدیمی بدون filter
            root = dest.resolve()
            safe = [m for m in members if (m.isfile() or m.isdir())
                    and root in (dest / m.name).resolve().parents]
            t.extractall(dest, members=safe)


def _seven_zip_bin() -> str | None:
    for name in ("7zz", "7z", "7za"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _extract_7z_or_rar(archive: Path, dest: Path, kind: str) -> bool:
    """7z/rar (و exe خوداستخراج 7z/rar). اول کتابخانهٔ پایتونی اگر نصب است، بعد برنامهٔ 7z. موفق بود → True."""
    if kind == "7z":
        try:
            import py7zr  # اختیاری
            with py7zr.SevenZipFile(archive) as z:
                if z.archiveinfo().uncompressed > max_unpacked_bytes():
                    raise VNInputError("حجم بازشدهٔ آرشیو 7z از سقف مجاز بیشتر است.")
                z.extractall(dest)
            return True
        except ImportError:
            pass
        except VNInputError:
            raise
        except Exception:
            pass
    if kind == "rar":
        try:
            import rarfile  # اختیاری؛ به unrar/7z روی سیستم نیاز دارد
            with rarfile.RarFile(archive) as z:
                z.extractall(dest)
            return True
        except ImportError:
            pass
        except Exception:
            pass
    tool = _seven_zip_bin()
    if tool:
        import subprocess
        r = subprocess.run([tool, "x", "-y", "-snl-", f"-o{dest}", str(archive)], capture_output=True, timeout=600)
        if r.returncode == 0 and any(dest.iterdir()):
            return True
    return False


def _sfx_kind(path: Path) -> str | None:
    """exe خوداستخراج 7z/rar: امضای آرشیو بعد از بخش اجرایی پیدا می‌شود."""
    with open(path, "rb") as f:
        head = f.read(16 * 2**20)
    if SEVEN_Z_SIG in head[2:]:
        return "7z"
    if any(sig in head[2:] for sig in RAR_SIGS):
        return "rar"
    return None


def container_of(path: Path, name: str) -> str:
    """نوع فایل از روی محتوا (نه فقط پسوند)."""
    path, low = Path(path), name.lower()
    if low.endswith(".apk"):
        return "apk"
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"RPA-"):
        return "rpa"
    if head.startswith(SEVEN_Z_SIG):
        return "7z"
    if head.startswith(RAR_SIGS):
        return "rar"
    if head.startswith(b"MZ") or low.endswith(".exe"):
        return "exe"
    if zipfile.is_zipfile(path):
        return "zip"
    import tarfile
    if tarfile.is_tarfile(path):
        return "tar"
    return "unknown"


def prepare(path: Path, name: str, dest: Path) -> str:
    """هر ورودی پشتیبانی‌شده را در dest باز می‌کند و نوعش را برمی‌گرداند. فایل اصلی (مثلاً exe) دست نمی‌خورد."""
    path, dest = Path(path), Path(dest)
    if path.stat().st_size > max_upload_bytes():
        raise VNInputError(f"فایل بزرگ‌تر از سقف {max_upload_bytes() // 2**20} مگابایت است.")
    dest.mkdir(parents=True, exist_ok=True)
    kind = container_of(path, name)
    if kind == "zip":
        _safe_extract(path, dest)
    elif kind == "tar":
        _extract_tar(path, dest)
    elif kind == "rpa":
        game = dest / "game"
        game.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, game / (Path(name).stem + ".rpa"))
    elif kind in ("7z", "rar"):
        if not _extract_7z_or_rar(path, dest, kind):
            raise VNInputError(f"باز کردن {kind.upper()} روی این سرور ممکن نیست (ابزار 7-Zip/unrar نصب نیست). "
                               "لطفاً پوشهٔ بازی را با .zip فشرده کنید و دوباره بدهید.")
    elif kind == "exe":
        opened = False
        if zipfile.is_zipfile(path):          # Ren'Py / NW.js (package.nw) چسبیده به exe
            try:
                _safe_extract(path, dest); opened = True
            except zipfile.BadZipFile:
                opened = False
        if not opened:
            sfx = _sfx_kind(path)
            opened = bool(sfx) and _extract_7z_or_rar(path, dest, sfx)
        if not opened:
            raise VNInputError(EXE_HELP)
    elif kind == "apk":
        raise VNInputError("APK مسیر جدا دارد.")
    else:
        raise VNInputError("این نوع فایل پشتیبانی نمی‌شود. zip، 7z، rar، tar(.gz)، exe، rpa یا apk بدهید.")
    _check_inside(dest)
    return kind


def _apk_tools() -> tuple[Path, Path] | None:
    sdk, unrpyc = os.environ.get("TRARIUM_RENPY_SDK", ""), os.environ.get("TRARIUM_UNRPYC", "")
    if sdk and unrpyc and (Path(sdk) / "renpy.sh").is_file() and (Path(unrpyc) / "unrpyc.py").is_file():
        return Path(sdk), Path(unrpyc)
    return None


def inspect_apk(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.startswith("assets/x-game/")]
    scripts = [n for n in names if n.endswith(".rpyc") and not n.endswith("script_version.rpyc")]
    if not names:
        return {"engine": "unknown", "lines": 0, "note": "این APK بازی Ren'Py نیست (پوشهٔ assets/x-game ندارد)."}
    return {"engine": "renpy-apk", "lines": None, "script_files": len(scripts), "apk_ready": _apk_tools() is not None}


def inspect_folder(folder: Path) -> dict:
    """موتور و تعداد خط‌های قابل ترجمهٔ یک پوشهٔ بازی (بدون API). ممکن است .rpa را در همان پوشه باز کند."""
    from trarium.vn import renpy as rp
    from trarium.vn import rpgmaker as rm
    from trarium.vn import tyrano as ty
    from trarium.vn.detect import detect_engine, find_root

    root = find_root(Path(folder))
    engine = detect_engine(root)
    info: dict = {"engine": engine, "lines": 0}
    if engine == "renpy":
        game = root / "game"
        segs = []
        if rp.script_files(game):
            segs = rp.extract(game)
        else:
            # فقط برای شمارش: اسکریپت‌های داخل .rpa در پوشهٔ موقت جدا باز می‌شوند.
            peek = Path(tempfile.mkdtemp(prefix=TMP_PREFIX + "rpa_"))
            try:
                for rpa in game.glob("*.rpa"):
                    try:
                        rp.unpack_rpa(rpa, peek / "game")
                    except Exception as exc:
                        info["note"] = f"آرشیو {rpa.name} باز نشد: {exc}"
                if (peek / "game").is_dir():
                    segs = rp.extract(peek / "game")
            finally:
                shutil.rmtree(peek, ignore_errors=True)
        info["lines"] = len(segs)
        info["by_kind"] = {k: sum(1 for s in segs if s.kind == k) for k in ("say", "menu", "ui", "name")}
        if not segs and any(game.rglob("*.rpyc")) and "note" not in info:
            info["note"] = "فقط فایل‌های .rpyc پیدا شد؛ برای ترجمه فایل‌های .rpy (یا .rpa حاوی آن‌ها) لازم است."
    elif engine == "rpgmaker":
        _docs, col = rm.extract(root)
        info["lines"] = len(col.texts)
    elif engine == "tyrano":
        segs, _docs = ty.extract(ty.find_root(root))
        info["lines"] = len(segs)
        info["by_kind"] = {k: sum(1 for s in segs if s.kind == k) for k in ("text", "name", "jname", "attr")}
    return info


def inspect_upload(path: Path, name: str | None = None) -> dict:
    """موتور و تعداد خط‌ها را بدون هیچ درخواست API برمی‌گرداند (پوشهٔ موقت پاک می‌شود)."""
    path = Path(path)
    name = name or path.name
    if path.stat().st_size > max_upload_bytes():
        raise VNInputError(f"فایل بزرگ‌تر از سقف {max_upload_bytes() // 2**20} مگابایت است.")
    if container_of(path, name) == "apk":
        if not zipfile.is_zipfile(path):
            raise VNInputError("فایل APK خراب است.")
        return inspect_apk(path)
    work = Path(tempfile.mkdtemp(prefix=TMP_PREFIX + "inspect_"))
    try:
        kind = prepare(path, name, work)
        info = inspect_folder(work)
        info["container"] = kind
        return info
    finally:
        shutil.rmtree(work, ignore_errors=True)


def folder_to_zip(files, out: Path) -> int:
    """آپلود پوشه (فهرست فایل با مسیر نسبی در name) را به یک زیپ تبدیل می‌کند."""
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            rel = str(f.name).replace("\\", "/").lstrip("/")
            if not rel or ".." in rel.split("/"):
                continue
            data = f.getvalue()
            total += len(data)
            if total > max_upload_bytes():
                raise VNInputError(f"حجم پوشه بیشتر از سقف {max_upload_bytes() // 2**20} مگابایت است.")
            z.writestr(rel, data)
    return total


class ProgressPipeline:
    """هر بار که بلوکی ترجمه شد، پیشرفت را گزارش می‌کند (بدون تغییر در ترجمه)."""

    def __init__(self, pipeline, total: int, on_progress=None):
        self._pipeline = pipeline
        self.total = max(1, int(total or 1))
        self.done = 0
        self.on_progress = on_progress

    def translate_block(self, text: str) -> str:
        out = self._pipeline.translate_block(text)
        self.done += len([p for p in re.split(r"\n\s*\n", text) if p.strip()]) or 1
        if self.on_progress:
            self.on_progress(min(self.done / self.total, 0.99), self.done)
        return out

    def __getattr__(self, item):
        return getattr(self._pipeline, item)


def _cleanup_build_tmp(report: dict) -> None:
    """translate_game پوشهٔ کاری‌اش (trarium_vn_*) را در temp نگه می‌دارد؛ بعد از ساخت پاکش کن."""
    root = report.get("game_root")
    if not root:
        return
    tmp = Path(tempfile.gettempdir()).resolve()
    p = Path(root).resolve()
    for parent in [p, *p.parents]:
        if parent.parent == tmp and parent.name.startswith("trarium_vn_"):
            shutil.rmtree(parent, ignore_errors=True)
            return


def run_upload(path: Path, name: str, outdir: Path, pipeline, limit: int | None = None) -> dict:
    """ورودی (zip/7z/rar/tar/exe/rpa) را با سقف حجم باز می‌کند و با trarium.vn.translate_game ترجمه می‌کند.

    برای exe فقط پچ ساخته می‌شود؛ خود exe هرگز دوباره بسته‌بندی یا عوض نمی‌شود.
    """
    from trarium.vn import translate_game
    work = Path(tempfile.mkdtemp(prefix=TMP_PREFIX + "src_"))
    try:
        src = work / (_safe_stem(Path(name).stem) or "game")
        kind = prepare(Path(path), name, src)
        report = translate_game(src, outdir, pipeline, limit=limit or None)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _cleanup_build_tmp(report)
    report["container"] = kind
    if kind == "exe" and report.get("full_zip"):
        (Path(outdir) / report["full_zip"]).unlink(missing_ok=True)
        report["full_zip"] = None
        report["note"] = ((report.get("note") or "") + " فایل exe دست نخورد؛ محتوای پچ را در پوشهٔ "
                          "استخراج‌شدهٔ بازی (یا package.nw) جایگزین کنید.").strip()
    return report


def run_zip(path: Path, outdir: Path, pipeline, limit: int | None = None) -> dict:
    return run_upload(path, Path(path).name, outdir, pipeline, limit)


def run_apk(path: Path, outdir: Path, pipeline) -> dict:
    """مسیر APK (همان مراحل ci_run/vn_apk_run.py). به Ren'Py SDK و unrpyc روی سرور نیاز دارد."""
    from trarium.vn import apk as ak
    from trarium.vn import renpy as rp
    from trarium.vn.build import _protected_translate
    tools = _apk_tools()
    if tools is None:
        raise VNInputError("برای APK باید TRARIUM_RENPY_SDK و TRARIUM_UNRPYC روی سرور تنظیم شده باشند.")
    sdk, unrpyc = tools
    outdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=TMP_PREFIX + "apk_"))
    try:
        proj = work / "project"; game = proj / "game"
        ak.unpack(path, game)
        rpys = ak.decompile(game, unrpyc)
        font_name = ak.font_of(game) or "CarroisGothic-Regular.ttf"
        segs = rp.extract(game)
        trs, why = _protected_translate(pipeline, [s.text.replace("\n", " ") for s in segs], rp.PROTECT)
        sizes = {"say": (24, 560), "menu": (36, None), "screen_text": (20, 480), "ui": (28, None), "name": (24, None)}
        shaped = []
        for s, t in zip(segs, trs):
            size, width = sizes.get(s.kind, (24, None))
            shaped.append(None if t is None else rp.preshape(t.replace("\n", " "), ak.FONT, size, width))
        stats = rp.apply(segs, shaped)
        (game / font_name).write_bytes(ak.FONT.read_bytes())
        ak.right_align(game, 560)
        for p in game.glob("*.rpyc"):
            if p.name != "script_version.rpyc":
                p.unlink()
        log = ak.compile_game(sdk, proj)
        repl = {}
        for name in rpys:
            c = game / (name + "c")
            if not c.exists():
                raise RuntimeError("کامپایل اسکریپت‌ها ناموفق بود: " + log[-500:])
            repl[ak._xpath(name + "c")] = c.read_bytes()
        repl[ak._xpath(font_name)] = ak.FONT.read_bytes()
        out_apk = outdir / (Path(path).stem + "-fa.apk")
        sign = ak.repack(Path(path), repl, out_apk, work)
        if not out_apk.exists():
            unsigned = out_apk.with_suffix(".unsigned.apk")
            out_apk = unsigned if unsigned.exists() else out_apk
        return {"engine": "renpy-apk", "segments": stats["segments"], "translated": stats["written"],
                "kept_original": stats["kept_original"], "rejected": why, "sign": sign,
                "apk": out_apk.name if out_apk.exists() else None}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def sweep_stale(max_age_hours: float = 6.0) -> int:
    """پوشه‌های آپلودِ جلسه‌های رهاشده را پاک می‌کند."""
    import time
    removed = 0
    for p in Path(tempfile.gettempdir()).glob(TMP_PREFIX + "*"):
        try:
            if p.is_dir() and time.time() - p.stat().st_mtime > max_age_hours * 3600:
                shutil.rmtree(p, ignore_errors=True); removed += 1
        except OSError:
            pass
    return removed


# --------------------------------------------------------------------- Streamlit
def _reset_upload_state(st) -> None:
    old = st.session_state.pop("vn_upload_dir", None)
    if old:
        shutil.rmtree(old, ignore_errors=True)
    for key in ("vn_upload_hash", "vn_upload_path", "vn_info", "vn_result", "vn_error"):
        st.session_state.pop(key, None)


def _rtl(st, html: str, muted: bool = False, box: bool = False) -> None:
    style = "direction:rtl;text-align:right;"
    if muted:
        style += "color:#a6a6a6;font-size:.92rem;margin-bottom:.6rem;"
    if box:
        style += ("padding:14px 18px;border-radius:12px;background:#1a1d2e;border:1px solid #2c3350;"
                  "color:#cfd8ff;margin-bottom:1rem;")
    st.markdown(f'<div style="{style}">{html}</div>', unsafe_allow_html=True)


def render_vn_tab(ctx: dict) -> None:
    import hashlib

    import streamlit as st

    from trarium.processors.pipeline import TranslationPipeline

    st.markdown("### 🎮 ترجمهٔ ویژوال ناول (آزمایشی)")
    _rtl(st, "بازی را به‌صورت آرشیو (<b>zip، 7z، rar، tar.gz</b>)، فایل <b>exe</b> ویندوزی (اگر بازی داخلش "
             "چسبیده باشد)، آرشیو <b>.rpa</b> بازی Ren'Py یا <b>.apk</b> بازی Ren'Py بدهید؛ یا کل پوشهٔ بازی را از "
             "«آپلود پوشه» بفرستید. موتور خودکار تشخیص داده می‌شود (Ren'Py، RPG Maker MV/MZ، TyranoScript). فقط "
             "متن‌ها و فونت عوض می‌شوند و فایل اجرایی بازی دست نمی‌خورد.", muted=True)
    _rtl(st, "⚖️ فقط بازی‌هایی را ترجمه کنید که اجازهٔ ترجمه‌شان را دارید."
             "<ul style='margin:.4rem 0 0'>"
             "<li><b>TyranoScript</b>: مجوز موتور بازتوزیع را منع می‌کند؛ خروجی فقط پچ است.</li>"
             "<li><b>RPG Maker</b>: متن‌ها فارسی می‌شوند، ولی برای چسبیدن حروف و راست‌به‌چپ در بازی پلاگین RTL لازم است.</li>"
             "<li><b>exe</b>: فقط پچ ساخته می‌شود؛ اگر exe باز نشد، پوشهٔ بازی را زیپ کنید.</li>"
             "<li>خروجی فعلاً فقط فارسی است.</li></ul>", box=True)

    cap = max_upload_bytes() // 2**20
    uploaded = st.file_uploader(f"فایل بازی (حداکثر {cap} مگابایت)", type=UPLOAD_TYPES, key="uploader_vn")
    folder_files = None
    with st.expander("📁 آپلود پوشهٔ بازی (به‌جای آرشیو)"):
        try:
            folder_files = st.file_uploader("پوشهٔ بازی را انتخاب کنید", accept_multiple_files="directory",
                                            key="uploader_vn_dir")
        except Exception:  # Streamlit قدیمی: آپلود پوشه ندارد
            st.caption("نسخهٔ Streamlit این سرور آپلود پوشه را پشتیبانی نمی‌کند؛ پوشه را زیپ کنید.")
    if uploaded is None and not folder_files:
        if st.session_state.get("vn_upload_hash"):
            _reset_upload_state(st)
        return

    if uploaded is not None:
        if uploaded.size > max_upload_bytes():
            st.error(f"فایل بزرگ‌تر از سقف {cap} مگابایت است.")
            return
        up_name = uploaded.name
        digest = hashlib.sha256(uploaded.getvalue()).hexdigest()
    else:
        first = str(folder_files[0].name).replace("\\", "/").split("/")[0] or "game"
        up_name = first + ".zip"
        h = hashlib.sha256()
        for f in folder_files:
            h.update(str(f.name).encode()); h.update(str(f.size).encode()); h.update(str(getattr(f, "file_id", "")).encode())
        digest = h.hexdigest()
    if st.session_state.get("vn_upload_hash") != digest:
        _reset_upload_state(st)
        sweep_stale()
        updir = Path(tempfile.mkdtemp(prefix=TMP_PREFIX + "up_"))
        lowered = up_name.lower()
        suffix = next((x for x in (".tar.gz", ".tar.bz2", ".tar.xz") if lowered.endswith(x)), Path(lowered).suffix) or ".bin"
        path = updir / ("upload" + suffix)
        st.session_state.update({"vn_upload_dir": str(updir), "vn_upload_path": str(path),
                                 "vn_upload_hash": digest, "vn_upload_name": up_name})
        try:
            if uploaded is not None:
                path.write_bytes(uploaded.getvalue())
            else:
                folder_to_zip(folder_files, path)
            with st.spinner("در حال بررسی بازی..."):
                st.session_state["vn_info"] = inspect_upload(path, up_name)
        except VNInputError as exc:
            st.session_state["vn_error"] = str(exc)
        except Exception as exc:
            st.session_state["vn_error"] = f"فایل خوانده نشد: {exc}"

    if st.session_state.get("vn_error"):
        st.error("❌ " + st.session_state["vn_error"])
        return
    info = st.session_state.get("vn_info") or {}
    engine = info.get("engine", "unknown")
    a, b = st.columns(2)
    a.metric("موتور تشخیص‌داده‌شده", ENGINE_LABELS.get(engine, engine))
    if engine == "renpy-apk":
        b.metric("فایل‌های اسکریپت", info.get("script_files", 0))
    else:
        b.metric("خط‌های قابل ترجمه", f"{info.get('lines', 0):,}")
    if info.get("by_kind"):
        names = {"say": "دیالوگ", "menu": "گزینه", "ui": "رابط", "name": "نام", "text": "متن", "jname": "نام نمایشی", "attr": "دکمه/برچسب"}
        _rtl(st, " · ".join(f"{names.get(k, k)}: {v}" for k, v in info["by_kind"].items() if v), muted=True)
    if info.get("note"):
        st.warning(info["note"])
    if engine == "unknown":
        st.error("موتور این بازی پشتیبانی نمی‌شود. فعلاً Ren'Py، RPG Maker MV/MZ، TyranoScript و APK بازی Ren'Py.")
        return
    if engine == "renpy-apk" and not info.get("apk_ready"):
        st.warning("ترجمهٔ APK روی این سرور فعال نیست (به Ren'Py SDK و unrpyc نیاز دارد: TRARIUM_RENPY_SDK و TRARIUM_UNRPYC).")
        return
    if engine != "renpy-apk" and not info.get("lines"):
        return

    limit = 0
    if engine != "renpy-apk":
        limit = st.number_input("تعداد خط برای ترجمهٔ آزمایشی (۰ = همه)", 0, 1_000_000, 0, key="vn_limit")
    if st.button("🚀 ترجمهٔ بازی", type="primary", key="vn_translate"):
        if not ctx.get("api_key"):
            st.error("API Key لازم است.")
        else:
            st.session_state.pop("vn_result", None)
            outdir = Path(st.session_state["vn_upload_dir"]) / "out"
            shutil.rmtree(outdir, ignore_errors=True)
            total = (limit or info.get("lines") or 0)
            bar = st.progress(0.0, text="شروع ترجمه...")

            def cb(frac, done):
                bar.progress(frac, text=f"ترجمه: {min(done, total) if total else done:,}" + (f" از {total:,}" if total else ""))

            try:
                translator = ctx["resolve_translator"](project=st.session_state.get('vn_upload_name', 'vn'))
                short = VN_SHORT_PROMPT + ((" " + ctx["short_prompt"]) if ctx.get("short_prompt") else "")
                pipe = TranslationPipeline(translator, "fa", int(ctx.get("chunk_size") or 14000),
                                           float(ctx.get("delay") or 0.0), int(ctx.get("workers") or 2),
                                           short_prompt=short)
                prog = ProgressPipeline(pipe, total, cb)
                path = Path(st.session_state["vn_upload_path"])
                with st.spinner("ترجمه در حال انجام است..."):
                    if engine == "renpy-apk":
                        report = run_apk(path, outdir, prog)
                    else:
                        report = run_upload(path, st.session_state.get("vn_upload_name", path.name), outdir,
                                            prog, limit=int(limit) or None)
                bar.progress(1.0, text="✅ تمام شد")
                st.session_state["vn_result"] = {"report": report, "outdir": str(outdir),
                                                 "stem": Path(st.session_state.get("vn_upload_name", "game")).stem}
            except Exception as exc:
                bar.empty()
                st.error(f"❌ {exc}")

    res = st.session_state.get("vn_result")
    if not res:
        return
    rep, outdir = res["report"], Path(res["outdir"])
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("ترجمه‌شده", f"{rep.get('translated', 0):,}/{rep.get('segments', 0):,}")
    c2.metric("بدون تغییر", rep.get("kept_original", 0))
    rej = rep.get("rejected") or {}
    c3.metric("ردشده (کد گم/بدون فارسی)", sum(rej.values()) if rej else 0)
    if rep.get("note"):
        st.caption(rep["note"])
    stem = _safe_stem(res["stem"])
    if rep.get("apk"):
        p = outdir / rep["apk"]
        if not (rep.get("sign") or {}).get("signed"):
            st.warning("ابزار امضای اندروید روی سرور نیست؛ APK امضا نشده است.")
        st.download_button("⬇️ دانلود APK فارسی", p.read_bytes(), f"{stem}-fa.apk",
                           "application/vnd.android.package-archive", type="primary", key="vn_dl_apk")
        st.caption("APK با کلید تازه امضا شده؛ قبل از نصب، نسخهٔ اصلی را پاک کنید.")
        return
    if rep.get("patch_zip") and (outdir / rep["patch_zip"]).exists():
        st.download_button("⬇️ دانلود پچ فارسی (فقط فایل‌های عوض‌شده)", (outdir / rep["patch_zip"]).read_bytes(),
                           f"{stem}_fa_patch.zip", "application/zip", type="primary", key="vn_dl_patch")
    if rep.get("full_zip") and (outdir / rep["full_zip"]).exists():
        st.download_button("⬇️ دانلود کل بازیِ فارسی‌شده", (outdir / rep["full_zip"]).read_bytes(),
                           f"{stem}_fa_full.zip", "application/zip", key="vn_dl_full")
    if rep.get("samples"):
        with st.expander("نمونهٔ ترجمه‌ها"):
            for s in rep["samples"]:
                st.markdown(f"- `{s.get('en') or s.get('src')}` → {s.get('fa') or '—'}")


def _safe_stem(name: str) -> str:
    return "".join(c for c in (name or "") if c.isalnum() or c in " _-").strip() or "game"
