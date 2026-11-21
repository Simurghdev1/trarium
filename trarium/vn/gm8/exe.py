"""GameMaker 8.0 executable reader/writer (experimental, isolated).

Only the standard (non-antidec, non-UPX) GM8.0 layout is supported.  The game
data is parsed into sections; the asset sections we touch (scripts, fonts,
timelines, objects) are split into their zlib chunks so individual assets can be
edited and the exe re-packed with the engine's own byte-swap encoding (the same
seed table is reused, so the runner decodes it exactly as before).

Format reference: OpenGMK gm8exe (MIT/GPL?) -- re-implemented from the public
format description; no code is copied.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

GM80_MAGIC_POS = 0x144AC0
GM80_SIG_POS = 0x000A49BE
GM80_SIG = bytes([0x8B, 0x45, 0xF4, 0xE8, 0x2A, 0xBD, 0xFD, 0xFF])
EMPTY_CHUNK = bytes([0x78, 0x9C, 0x63, 0x60, 0x60, 0x60, 0x00, 0x00, 0x00, 0x04, 0x00, 0x01])


class GM8Error(Exception):
    pass


# --------------------------------------------------------------------------- io
class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.p = pos

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def raw(self, n: int) -> bytes:
        v = self.d[self.p:self.p + n]
        if len(v) != n:
            raise GM8Error("unexpected end of data")
        self.p += n
        return v

    def pstr(self) -> bytes:
        return self.raw(self.u32())


def w_u32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def w_i32(v: int) -> bytes:
    return struct.pack("<i", v)


def w_pstr(b: bytes) -> bytes:
    return w_u32(len(b)) + b


# ----------------------------------------------------------- gm80 byte coding
def _tables(swap: bytes):
    rev = bytearray(256)
    for i in range(256):
        rev[swap[i]] = i
    return rev


def gm80_decrypt(buf: bytearray, pos: int, length: int, swap: bytes) -> None:
    rev = _tables(swap)
    for i in range(pos + length, pos + 1, -1):
        buf[i - 1] = (rev[buf[i - 1]] - (buf[i - 2] + (i - (pos + 1)))) & 0xFF
    for i in range(pos + length - 1, pos - 1, -1):
        b = max(i - swap[(i - pos) & 0xFF], pos)
        buf[i], buf[b] = buf[b], buf[i]


def gm80_encrypt(buf: bytearray, pos: int, length: int, swap: bytes) -> None:
    for i in range(pos, pos + length):
        b = max(i - swap[(i - pos) & 0xFF], pos)
        buf[i], buf[b] = buf[b], buf[i]
    for i in range(pos + 2, pos + length + 1):
        buf[i - 1] = swap[(buf[i - 1] + buf[i - 2] + (i - (pos + 1))) & 0xFF]


# --------------------------------------------------------------- asset models
@dataclass
class Action:
    head: bytes          # version..execution_type (8 x u32)
    fn_name: bytes
    fn_code: bytes
    param_count: int
    param_types: bytes   # u32 count(8) + 8 x u32
    applies_to: int
    is_relative: int
    args: list           # 8 x bytes
    invert: int
    kind: int = 0        # action_kind (7 == code)

    @classmethod
    def read(cls, r: Reader) -> "Action":
        head = r.raw(32)
        kind = struct.unpack_from("<I", head, 12)[0]
        fn_name = r.pstr()
        fn_code = r.pstr()
        pc = r.u32()
        pt = r.raw(4 + 32)
        at = r.i32()
        rel = r.u32()
        if r.u32() != 8:
            raise GM8Error("bad action arg count")
        args = [r.pstr() for _ in range(8)]
        inv = r.u32()
        return cls(head, fn_name, fn_code, pc, pt, at, rel, args, inv, kind)

    def write(self) -> bytes:
        out = [self.head, w_pstr(self.fn_name), w_pstr(self.fn_code), w_u32(self.param_count),
               self.param_types, w_i32(self.applies_to), w_u32(self.is_relative), w_u32(8)]
        out += [w_pstr(a) for a in self.args]
        out.append(w_u32(self.invert))
        return b"".join(out)


@dataclass
class Obj:
    name: bytes
    head: bytes            # version..mask (8 fields = 32 bytes)
    events: list           # 12 lists of (sub, ver, [Action])

    @classmethod
    def read(cls, r: Reader) -> "Obj":
        name = r.pstr()
        head = r.raw(32)
        n = r.u32()
        if n != 11:
            raise GM8Error("object event list count != 11")
        events = []
        for _ in range(12):
            lst = []
            while True:
                sub = r.i32()
                if sub < 0:
                    break
                ver = r.u32()
                cnt = r.u32()
                lst.append((sub, ver, [Action.read(r) for _ in range(cnt)]))
            events.append(lst)
        return cls(name, head, events)

    def write(self) -> bytes:
        out = [w_pstr(self.name), self.head, w_u32(11)]
        for lst in self.events:
            for sub, ver, acts in lst:
                out += [w_i32(sub), w_u32(ver), w_u32(len(acts))] + [a.write() for a in acts]
            out.append(w_i32(-1))
        return b"".join(out)

    def actions(self):
        for et, lst in enumerate(self.events):
            for sub, _ver, acts in lst:
                for ai, a in enumerate(acts):
                    yield (et, sub, ai), a


@dataclass
class Script:
    name: bytes
    ver: int
    source: bytes

    @classmethod
    def read(cls, r: Reader) -> "Script":
        return cls(r.pstr(), r.u32(), r.pstr())

    def write(self) -> bytes:
        return w_pstr(self.name) + w_u32(self.ver) + w_pstr(self.source)


@dataclass
class Timeline:
    name: bytes
    ver: int
    moments: list  # (index, ver, [Action])

    @classmethod
    def read(cls, r: Reader) -> "Timeline":
        name = r.pstr()
        ver = r.u32()
        moms = []
        for _ in range(r.u32()):
            idx = r.u32()
            mv = r.u32()
            moms.append((idx, mv, [Action.read(r) for _ in range(r.u32())]))
        return cls(name, ver, moms)

    def write(self) -> bytes:
        out = [w_pstr(self.name), w_u32(self.ver), w_u32(len(self.moments))]
        for idx, mv, acts in self.moments:
            out += [w_u32(idx), w_u32(mv), w_u32(len(acts))] + [a.write() for a in acts]
        return b"".join(out)

    def actions(self):
        for idx, _mv, acts in self.moments:
            for ai, a in enumerate(acts):
                yield (idx, ai), a


@dataclass
class Font:
    name: bytes
    ver: int
    sys_name: bytes
    size: int
    bold: int
    italic: int
    range_start: int
    range_end: int
    dmap: list            # 0x600 u32 (x, y, w, h, offset, advance) x 256
    map_w: int
    map_h: int
    pixels: bytes         # alpha map, map_w * map_h

    @classmethod
    def read(cls, r: Reader) -> "Font":
        name = r.pstr()
        ver = r.u32()
        sysn = r.pstr()
        size, bold, ital, rs, re_ = (r.u32() for _ in range(5))
        dmap = list(struct.unpack_from("<1536I", r.d, r.p))
        r.p += 1536 * 4
        mw, mh = r.u32(), r.u32()
        px = r.pstr()
        return cls(name, ver, sysn, size, bold, ital, rs, re_, dmap, mw, mh, px)

    def write(self) -> bytes:
        return b"".join([w_pstr(self.name), w_u32(self.ver), w_pstr(self.sys_name), w_u32(self.size),
                         w_u32(self.bold), w_u32(self.italic), w_u32(self.range_start), w_u32(self.range_end),
                         struct.pack("<1536I", *self.dmap), w_u32(self.map_w), w_u32(self.map_h),
                         w_pstr(self.pixels)])

    def glyph(self, c: int):
        return tuple(self.dmap[c * 6:c * 6 + 6])


MODELS = {"scripts": Script, "fonts": Font, "timelines": Timeline, "objects": Obj}


@dataclass
class AssetSection:
    kind: str
    version: int
    chunks: list          # raw zlib chunks
    _cache: dict = field(default_factory=dict)

    def __len__(self):
        return len(self.chunks)

    def get(self, i: int):
        """Return parsed asset i (or None if deleted).  Parsed objects are cached; call
        ``put`` to store changes."""
        if i in self._cache:
            return self._cache[i]
        c = self.chunks[i]
        if c == EMPTY_CHUNK:
            return None
        data = zlib.decompress(c)
        r = Reader(data)
        if r.u32() == 0:
            return None
        m = MODELS[self.kind].read(r)
        self._cache[i] = m
        return m

    def put(self, i: int, asset) -> None:
        self._cache[i] = asset
        self.chunks[i] = zlib.compress(w_u32(1) + asset.write(), 9)

    def items(self):
        for i in range(len(self.chunks)):
            a = self.get(i)
            if a is not None:
                yield i, a

    def write(self) -> bytes:
        out = [w_u32(self.version), w_u32(len(self.chunks))]
        for c in self.chunks:
            out += [w_u32(len(c)), c]
        return b"".join(out)


def _read_chunks(r: Reader) -> list:
    return [r.pstr() for _ in range(r.u32())]


class GM8Exe:
    """Parsed GM8.0 game.  ``sections`` is an ordered list of either raw bytes or
    AssetSection instances covering the whole decrypted region."""

    def __init__(self, data: bytes):
        self.orig = data
        if data[:2] != b"MZ":
            raise GM8Error("not a Windows exe")
        if data[GM80_SIG_POS:GM80_SIG_POS + 8] != GM80_SIG:
            raise GM8Error("not a standard GameMaker 8.0 executable (8.1/antidec/UPX not supported)")
        magic = struct.unpack_from("<I", data, GM80_SIG_POS + 9)[0]
        pos = struct.unpack_from("<I", data, GM80_MAGIC_POS)[0]
        while struct.unpack_from("<I", data, pos)[0] != magic:
            pos += 10000
            if pos + 4 > len(data):
                raise GM8Error("GM8 header not found")
        r = Reader(data, pos + 16)
        r.raw(r.u32())                      # settings (zlib)
        r.raw(r.u32())                      # dll name
        r.raw(r.u32())                      # embedded d3dx dll
        g1, g2 = r.u32() * 4, r.u32() * 4
        r.p += g1
        self.swap = r.raw(256)
        r.p += g2
        self.len_pos = r.p
        length = r.u32()
        self.data_pos = r.p
        buf = bytearray(data[self.data_pos:self.data_pos + length])
        if len(buf) != length:
            raise GM8Error("truncated game data")
        # decrypt on a padded copy so the algorithm's pos-relative indices hold
        gm80_decrypt(buf, 0, length, self.swap)
        self.tail = data[self.data_pos + length:]
        self._parse(bytes(buf))

    def _parse(self, d: bytes) -> None:
        r = Reader(d)
        secs = []
        start = r.p
        n_garbage = r.u32()
        r.p += n_garbage * 4                # garbage dwords
        r.raw(8 + 16)                       # pro flag, game id, guid
        if r.u32() != 700:
            raise GM8Error("bad extensions header")
        n_ext = r.u32()
        for _ in range(n_ext):
            self._skip_extension(r)
        secs.append(d[start:r.p])
        for kind in ("triggers", "constants", "sounds", "sprites", "backgrounds", "paths",
                     "scripts", "fonts", "timelines", "objects", "rooms"):
            if kind == "constants":
                s = r.p
                r.u32()
                for _ in range(r.u32()):
                    r.pstr(); r.pstr()
                secs.append(d[s:r.p])
                continue
            s = r.p
            ver = r.u32()
            chunks = _read_chunks(r)
            if kind in MODELS:
                secs.append(AssetSection(kind, ver, chunks))
            else:
                secs.append(d[s:r.p])
        secs.append(d[r.p:])                # instance ids, included files, help, libs, room order
        self.sections = secs

    @staticmethod
    def _skip_extension(r: Reader) -> None:
        r.u32(); r.pstr(); r.pstr()          # ver, name, folder
        for _ in range(r.u32()):            # files
            r.u32(); r.pstr(); r.u32(); r.pstr(); r.pstr()   # ver, name, kind, init, final
            for _ in range(r.u32()):        # functions
                r.u32(); r.pstr(); r.pstr(); r.u32(); r.u32(); r.raw(4 * 17 + 4)
                r.u32()                     # return type
            for _ in range(r.u32()):        # constants
                r.u32(); r.pstr(); r.pstr()
        r.pstr()                            # file contents (encrypted blob)

    def section(self, kind: str) -> AssetSection:
        for s in self.sections:
            if isinstance(s, AssetSection) and s.kind == kind:
                return s
        raise KeyError(kind)

    def build(self) -> bytes:
        body = bytearray(b"".join(s.write() if isinstance(s, AssetSection) else s for s in self.sections))
        gm80_encrypt(body, 0, len(body), self.swap)
        return self.orig[:self.len_pos] + w_u32(len(body)) + bytes(body) + self.tail
