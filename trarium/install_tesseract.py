"""نصب خودکار Tesseract OCR."""

import os
import sys
import subprocess
import shutil
import json
from pathlib import Path


TESSERACT_URL = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
INSTALLER_PATH = Path(os.environ.get("TEMP", "/tmp")) / "tesseract_setup.exe"
CONFIG_FILE = Path(__file__).parent.parent / ".tesseract_path"

_user_tesseract_path: str | None = None


def _load_saved_path() -> str | None:
    """بارگذاری مسیر ذخیره شده."""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            path = data.get("path", "")
            if path and Path(path).exists():
                return path
        except Exception:
            pass
    return None


def _save_path(path: str):
    """ذخیره مسیر Tesseract."""
    try:
        CONFIG_FILE.write_text(json.dumps({"path": path}), encoding="utf-8")
    except Exception:
        pass


def set_tesseract_path(path: str):
    """تنظیم مسیر دستی Tesseract."""
    global _user_tesseract_path
    if path and Path(path).exists():
        _user_tesseract_path = path
        _save_path(path)


def _find_tesseract() -> str | None:
    """پیدا کردن مسیر tesseract."""
    # روش ۰: مسیر ذخیره شده
    if _user_tesseract_path and Path(_user_tesseract_path).exists():
        return _user_tesseract_path

    saved = _load_saved_path()
    if saved:
        return saved

    # روش ۱: PATH
    path = shutil.which("tesseract")
    if path:
        return path

    # روش ۲: Program Files — جستجوی بازگشتی
    search_dirs = []
    for key in ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA", "APPDATA"]:
        base = os.environ.get(key, "")
        if base and Path(base).exists():
            search_dirs.append(Path(base))
        # مسیرهای ثابت ویندوز
    search_dirs.extend([
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
        Path("C:/ProgramData"),
        Path("C:/Users") / os.environ.get("USERNAME", "") / "AppData/Local/Programs",
        Path("C:/Users") / os.environ.get("USERNAME", "") / "AppData/Local",
    ])

    for base_dir in search_dirs:
        try:
            for item in base_dir.iterdir():
                if item.is_dir() and "tesseract" in item.name.lower():
                    exe = item / "tesseract.exe"
                    if exe.exists():
                        _save_path(str(exe))
                        return str(exe)
        except (PermissionError, OSError):
            continue

    return None


def is_tesseract_installed() -> bool:
    """بررسی نصب بودن Tesseract."""
    return _find_tesseract() is not None


def install_tesseract_silent(user_confirm: bool = True) -> bool:
    """دانلود و نصب سکوت Tesseract."""
    if is_tesseract_installed():
        return True

    # لینوکس
    if sys.platform.startswith("linux"):
        try:
            subprocess.run(
                ["sudo", "apt", "install", "-y", "tesseract-ocr"],
                capture_output=True, text=True, timeout=120
            )
            return shutil.which("tesseract") is not None
        except Exception:
            return False

    # مک
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["brew", "install", "tesseract"],
                capture_output=True, text=True, timeout=300
            )
            return shutil.which("tesseract") is not None
        except Exception:
            return False

    # ویندوز
    if sys.platform == "win32":
        if user_confirm:
            print(f"\n  📥 Tesseract OCR (~60MB) نیاز به دانلود داره.")
            answer = input("  آیا دانلود کنم؟ (y/n): ").strip().lower()
            if answer not in ("y", "yes", "بله", "ب"):
                print("  ⏹️ دانلود لغو شد")
                return False

        print("  📥 در حال دانلود...")
        try:
            import urllib.request
            urllib.request.urlretrieve(TESSERACT_URL, str(INSTALLER_PATH))
        except Exception as e:
            print(f"  ❌ خطا: {e}")
            return False

        print("  ⚙️ در حال نصب...")
        try:
            subprocess.run([str(INSTALLER_PATH), "/S"], capture_output=True, timeout=120)
            tess_path = _find_tesseract()
            if tess_path:
                print("  ✅ Tesseract نصب شد!")
                print("  ⚠️ لطفاً برنامه رو ری‌استارت کنید")
                return True
            return False
        finally:
            try:
                INSTALLER_PATH.unlink()
            except Exception:
                pass

    return False


def ensure_tesseract(user_confirm: bool = True):
    """Tesseract رو نصب کنه اگه نیست."""
    tess_path = _find_tesseract()
    if tess_path:
        return True

    print("\n🔍 Tesseract OCR پیدا نشد!")
    return install_tesseract_silent(user_confirm=user_confirm)
