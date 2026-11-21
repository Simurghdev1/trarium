"""تشخیص موتور بازی از روی ساختار پوشه."""
from __future__ import annotations

from pathlib import Path


def find_root(folder: Path) -> Path:
    """اگر زیپ یک پوشهٔ تکی داشت، ریشهٔ واقعی بازی را برمی‌گرداند."""
    folder = Path(folder)
    for _ in range(3):
        entries = [p for p in folder.iterdir() if not p.name.startswith(("__MACOSX", "."))]
        direct = (folder / "game").is_dir() or (folder / "www" / "data").is_dir() or \
            (folder / "data" / "System.json").is_file() or (folder / "data" / "scenario").is_dir() or \
            any((folder / n).is_file() for n in ("data.win", "game.unx", "game.ios", "game.droid")) or \
            (folder / "Data").is_dir()
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
    from trarium.vn.rgss.engine import archive_of, data_dir as rgss_data
    if rgss_data(folder) is not None:
        return "rgss"
    if archive_of(folder) is not None:
        return "rgss-archived"
    from trarium.vn.gms.build import find_data_file
    if find_data_file(folder) is not None:
        return "gamemaker"
    from trarium.vn.gm8.auto import find_gm8_exe
    if find_gm8_exe(folder) is not None:
        return "gm8"
    return "unknown"
