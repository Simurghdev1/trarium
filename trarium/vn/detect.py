"""تشخیص موتور بازی از روی ساختار پوشه."""
from __future__ import annotations

from pathlib import Path


def find_root(folder: Path) -> Path:
    """اگر زیپ یک پوشهٔ تکی داشت، ریشهٔ واقعی بازی را برمی‌گرداند."""
    folder = Path(folder)
    for _ in range(3):
        entries = [p for p in folder.iterdir() if not p.name.startswith(("__MACOSX", "."))]
        direct = (folder / "game").is_dir() or (folder / "www" / "data").is_dir() or \
            (folder / "data" / "System.json").is_file() or (folder / "data" / "scenario").is_dir()
        if len(entries) == 1 and entries[0].is_dir() and not direct:
            folder = entries[0]
        else:
            break
    return folder


def detect_engine(folder: Path) -> str:
    folder = Path(folder)
    game = folder / "game"
    if game.is_dir() and (any(game.rglob("*.rpy")) or any(game.rglob("*.rpyc")) or any(game.glob("*.rpa"))):
        return "renpy"
    for data in (folder / "www" / "data", folder / "data"):
        if (data / "System.json").is_file() and (data / "MapInfos.json").is_file():
            return "rpgmaker"
    from trarium.vn.tyrano import find_root as tyrano_root
    if tyrano_root(folder) is not None:
        return "tyrano"
    return "unknown"
