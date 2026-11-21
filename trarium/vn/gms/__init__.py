"""GameMaker Studio 1.x / 2.x (data.win, game.unx, game.ios, game.droid): text
extraction, STRG patching by append-and-repoint, Persian glyphs in bitmap fonts."""
from trarium.vn.gms.iff import DataWin, GMSError, looks_like_data_win

__all__ = ["DataWin", "GMSError", "looks_like_data_win"]
