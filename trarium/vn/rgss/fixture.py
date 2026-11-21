"""Synthetic RPG Maker VX Ace / XP data for tests and demos (no game data)."""
from __future__ import annotations

from pathlib import Path

from trarium.vn.rgss import marshal as M


def _s(text: str, ace: bool):
    return M.RString(text.encode("utf-8"), [(M.Sym("E"), True)] if ace else None)


def _cmd(code, params, indent=0):
    return M.RObject(M.Sym("RPG::EventCommand"), [(M.Sym("@code"), code), (M.Sym("@indent"), indent),
                                                   (M.Sym("@parameters"), M.RArray(params))])


def build(root: Path, ace: bool = True) -> Path:
    ext = ".rvdata2" if ace else ".rxdata"
    data = Path(root) / "Data"
    data.mkdir(parents=True, exist_ok=True)
    if ace:
        lst = [_cmd(101, [_s("", ace), 0, 0, 2]), _cmd(401, [_s("Hello, \\N[1]! Welcome to", ace)]),
               _cmd(401, [_s("the \\C[2]village\\C[0] of Ashwood.", ace)])]
    else:
        lst = [_cmd(101, [_s("Hello, \\N[1]! Welcome to", ace)]), _cmd(401, [_s("the \\C[2]village\\C[0] of Ashwood.", ace)])]
    lst += [_cmd(102, [M.RArray([_s("Yes, please", ace), _s("No thanks", ace)]), 2]),
            _cmd(402, [0, _s("Yes, please", ace)]), _cmd(0, [])]
    page = M.RObject(M.Sym("RPG::Event::Page"), [(M.Sym("@list"), M.RArray(lst))])
    event = M.RObject(M.Sym("RPG::Event"), [(M.Sym("@id"), 1), (M.Sym("@name"), _s("EV001", ace)),
                                            (M.Sym("@pages"), M.RArray([page]))])
    events = M.RHash([(1, event)])
    mp = M.RObject(M.Sym("RPG::Map"), [(M.Sym("@width"), 20), (M.Sym("@events"), events),
                                       (M.Sym("@data"), M.RUser(b"u", M.Sym("Table"), b"\x01\0\0\0" * 5))])
    (data / f"Map001{ext}").write_bytes(M.dumps(mp))
    item = M.RObject(M.Sym("RPG::Item"), [(M.Sym("@id"), 1), (M.Sym("@name"), _s("Potion", ace)),
                                          (M.Sym("@description"), _s("Restores 50 HP to one ally.", ace))])
    (data / f"Items{ext}").write_bytes(M.dumps(M.RArray([None, item])))
    return data
