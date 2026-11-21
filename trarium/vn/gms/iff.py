"""Reader/patcher for GameMaker Studio 1.x / 2.x data files (data.win, game.unx,
game.ios, game.droid).

The file is an IFF container: ``FORM`` + size, then chunks ``NAME`` + u32 size +
content.  Every reference between assets is an *absolute* file offset, so the
patcher never moves an existing byte.  It only

* appends new bytes (strings, a rebuilt font struct, a new texture-page item and an
  enlarged texture PNG) after the end of the **last** chunk (whose size, and the
  FORM size, grow accordingly), and
* overwrites single 32-bit pointer words in place (an STRG table entry, a FONT
  list entry, a TXTR entry's data pointer / size fields).

Old strings stay where they were, so anything that addresses a string directly
(asset names, variable and function names) keeps seeing the original bytes; game
code reaches strings through the STRG table by index, which is what gets
repointed.  Nothing here executes or unpickles anything from the file.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

BLOB_CHUNKS = {"TXTR", "AUDO", "STRG", "EMBI", "SHDR", "CODE"}   # not scanned for string pointers


class GMSError(Exception):
    pass


@dataclass
class Chunk:
    name: str
    offset: int        # offset of the 8-byte chunk header
    size: int

    @property
    def start(self) -> int:
        return self.offset + 8

    @property
    def end(self) -> int:
        return self.offset + 8 + self.size


def looks_like_data_win(head: bytes) -> bool:
    """``FORM`` + size + a first chunk named GEN8 (all GMS1/GMS2 runners)."""
    return len(head) >= 12 and head[:4] == b"FORM" and head[8:12] == b"GEN8"


class DataWin:
    def __init__(self, data: bytes | bytearray):
        self.d = bytearray(data)
        if not looks_like_data_win(bytes(self.d[:12])):
            raise GMSError("not a GameMaker Studio data file (FORM/GEN8 header missing)")
        form_size = self.u32(4)
        if form_size + 8 > len(self.d):
            raise GMSError("truncated data file")
        self.chunks: dict[str, Chunk] = {}
        self.order: list[str] = []
        pos, end = 8, 8 + form_size
        while pos + 8 <= end:
            name = bytes(self.d[pos:pos + 4]).decode("latin-1")
            size = self.u32(pos + 4)
            if pos + 8 + size > end or not name.isalnum():
                raise GMSError(f"bad chunk header at 0x{pos:x}")
            self.chunks[name] = Chunk(name, pos, size)
            self.order.append(name)
            pos += 8 + size
        if "STRG" not in self.chunks:
            raise GMSError("STRG chunk missing")
        self._appended = 0

    # ------------------------------------------------------------------ io
    def u32(self, p: int) -> int:
        return struct.unpack_from("<I", self.d, p)[0]

    def u16(self, p: int) -> int:
        return struct.unpack_from("<H", self.d, p)[0]

    def put_u32(self, p: int, v: int) -> None:
        struct.pack_into("<I", self.d, p, v & 0xFFFFFFFF)

    def put_u16(self, p: int, v: int) -> None:
        struct.pack_into("<H", self.d, p, v & 0xFFFF)

    def pointer_list(self, p: int) -> list[int]:
        n = self.u32(p)
        if n > 10_000_000 or p + 4 + 4 * n > len(self.d):
            raise GMSError(f"bad pointer list at 0x{p:x}")
        return list(struct.unpack_from("<%dI" % n, self.d, p + 4))

    def chunk(self, name: str) -> Chunk | None:
        return self.chunks.get(name)

    # ------------------------------------------------------------- version
    def gen8_version(self) -> tuple[int, int, int, int]:
        g = self.chunks.get("GEN8")
        if not g or g.size < 0x2C + 16:
            return (1, 0, 0, 0)
        p = g.start + 0x2C        # major, minor, release, build (u32 each)
        return tuple(self.u32(p + 4 * i) for i in range(4))  # type: ignore[return-value]

    def bytecode_version(self) -> int:
        g = self.chunks.get("GEN8")
        return self.d[g.start + 1] if g and g.size > 2 else 0

    # ------------------------------------------------------------- strings
    def string_table(self) -> list[int]:
        """Absolute offsets of the STRG entries (each: u32 length, bytes, NUL)."""
        return self.pointer_list(self.chunks["STRG"].start)

    def string_at(self, entry: int) -> str:
        n = self.u32(entry)
        if entry + 4 + n > len(self.d):
            raise GMSError("string out of range")
        return bytes(self.d[entry + 4:entry + 4 + n]).decode("utf-8", errors="replace")

    def strings(self) -> list[str]:
        return [self.string_at(p) for p in self.string_table()]

    def direct_string_refs(self) -> set[int]:
        """STRG indices whose text address is referenced directly from asset chunks
        (asset/variable/function names, room names, ...): those are identifiers."""
        table = self.string_table()
        addr = {p + 4: i for i, p in enumerate(table)}
        found: set[int] = set()
        for name in self.order:
            if name in BLOB_CHUNKS:
                continue
            c = self.chunks[name]
            n = (c.size // 4)
            if not n:
                continue
            for v in struct.unpack_from("<%dI" % n, self.d, c.start):
                i = addr.get(v)
                if i is not None:
                    found.add(i)
        return found

    # -------------------------------------------------------------- append
    def next_offset(self, align: int = 4) -> int:
        return len(self.d) + ((-len(self.d)) % align)

    def append(self, blob: bytes, align: int = 4) -> int:
        """Append bytes after the last chunk; returns their absolute offset."""
        last = self.chunks[self.order[-1]]
        pad = (-len(self.d)) % align
        if last.end != len(self.d):
            raise GMSError("data after the FORM container; not supported")
        self.d += b"\0" * pad
        at = len(self.d)
        self.d += blob
        grow = pad + len(blob)
        last.size += grow
        self.put_u32(last.offset + 4, last.size)
        self.put_u32(4, self.u32(4) + grow)
        self._appended += grow
        return at

    def set_string(self, index: int, text: str) -> None:
        raw = text.encode("utf-8")
        at = self.append(struct.pack("<I", len(raw)) + raw + b"\0")
        self.put_u32(self.chunks["STRG"].start + 4 + 4 * index, at)

    def to_bytes(self) -> bytes:
        return bytes(self.d)
