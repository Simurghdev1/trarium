"""Minimal Ruby Marshal 4.8 reader/writer (pure data, no code execution).

Enough for RPG Maker XP/VX/VX Ace data files (.rxdata/.rvdata/.rvdata2).
Objects are read into plain Python node classes; nothing is instantiated from
class names, nothing is evaluated.  Re-serialising an unmodified tree gives the
original bytes (object links ``@`` and symbol links ``;`` are re-created in the
same order), so a translated string can be swapped in place by mutating its
:class:`RString` node.
"""
from __future__ import annotations

import struct

MAX_DEPTH = 400


class MarshalError(Exception):
    pass


class Sym(str):
    """A Ruby symbol."""


class RString:
    __slots__ = ("data", "ivars")

    def __init__(self, data: bytes, ivars=None):
        self.data, self.ivars = data, ivars   # ivars: list[(Sym, value)] or None

    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")

    def __repr__(self):
        return f"RString({self.data!r})"


class RObject:
    __slots__ = ("cls", "ivars")

    def __init__(self, cls: Sym, ivars: list):
        self.cls, self.ivars = cls, ivars

    def get(self, name: str, default=None):
        for k, v in self.ivars:
            if k == name:
                return v
        return default

    def set(self, name: str, value) -> None:
        for n, (k, _v) in enumerate(self.ivars):
            if k == name:
                self.ivars[n] = (k, value)
                return
        self.ivars.append((Sym(name), value))

    def __repr__(self):
        return f"RObject({self.cls})"


class RArray(list):
    __slots__ = ("ivars",)

    def __init__(self, *a):
        super().__init__(*a)
        self.ivars = None


class RHash:
    __slots__ = ("pairs", "default", "ivars")

    def __init__(self, pairs, default=None, has_default=False):
        self.pairs, self.default, self.ivars = pairs, (default, has_default), None


class RUser:
    """``u`` (_dump) or ``U`` (marshal_dump) or other opaque typed value."""
    __slots__ = ("kind", "cls", "data", "ivars")

    def __init__(self, kind: bytes, cls, data, ivars=None):
        self.kind, self.cls, self.data, self.ivars = kind, cls, data, ivars


class RFloat:
    __slots__ = ("raw", "ivars")

    def __init__(self, raw: bytes):
        self.raw, self.ivars = raw, None


class RBig:
    __slots__ = ("sign", "data", "ivars")

    def __init__(self, sign: bytes, data: bytes):
        self.sign, self.data, self.ivars = sign, data, None


class RExt:
    """``e`` (extended by module) wrapper."""
    __slots__ = ("mod", "obj")

    def __init__(self, mod, obj):
        self.mod, self.obj = mod, obj


class _Reader:
    def __init__(self, data: bytes):
        self.d, self.p = data, 0
        self.syms: list[Sym] = []
        self.objs: list = []

    def byte(self) -> int:
        if self.p >= len(self.d):
            raise MarshalError("unexpected end")
        b = self.d[self.p]
        self.p += 1
        return b

    def raw(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.d):
            raise MarshalError("bad length")
        v = self.d[self.p:self.p + n]
        self.p += n
        return v

    def long(self) -> int:
        c = self.byte()
        if c == 0:
            return 0
        c = c - 256 if c > 127 else c
        if 5 <= c:
            return c - 5
        if c <= -5:
            return c + 5
        if c > 0:
            x = 0
            for i in range(c):
                x |= self.byte() << (8 * i)
            return x
        n = -c
        x = -1
        for i in range(n):
            x &= ~(0xFF << (8 * i))
            x |= self.byte() << (8 * i)
        return x

    def bytes_(self) -> bytes:
        return self.raw(self.long())

    def symbol(self, depth):
        t = self.byte()
        if t == ord(":"):
            s = Sym(self.bytes_().decode("utf-8", errors="surrogateescape"))
            self.syms.append(s)
            return s
        if t == ord(";"):
            i = self.long()
            if not 0 <= i < len(self.syms):
                raise MarshalError("bad symlink")
            return self.syms[i]
        if t == ord("I"):                    # symbol with encoding ivars (Ruby 1.9+)
            s = self.symbol(depth)
            for _ in range(self.long()):
                self.symbol(depth); self.value(depth + 1)
            return s
        raise MarshalError("symbol expected")

    def entry(self, obj):
        self.objs.append(obj)
        return obj

    def value(self, depth=0):
        if depth > MAX_DEPTH:
            raise MarshalError("nesting too deep")
        t = chr(self.byte())
        if t == "0":
            return None
        if t == "T":
            return True
        if t == "F":
            return False
        if t == "i":
            return self.long()
        if t in ":;":
            self.p -= 1
            return self.symbol(depth)
        if t == "@":
            i = self.long()
            if not 0 <= i < len(self.objs):
                raise MarshalError("bad object link")
            return self.objs[i]
        if t == "I":
            obj = self.value(depth + 1)
            iv = [(self.symbol(depth), self.value(depth + 1)) for _ in range(self.long())]
            if not hasattr(obj, "ivars"):
                raise MarshalError("ivars on immediate")
            obj.ivars = iv
            return obj
        if t == '"':
            return self.entry(RString(self.bytes_()))
        if t == "[":
            a = self.entry(RArray())
            for _ in range(self.long()):
                a.append(self.value(depth + 1))
            return a
        if t in "{}":
            h = self.entry(RHash([]))
            for _ in range(self.long()):
                k = self.value(depth + 1)
                h.pairs.append((k, self.value(depth + 1)))
            if t == "}":
                h.default = (self.value(depth + 1), True)
            return h
        if t == "o":
            cls = self.symbol(depth)
            o = self.entry(RObject(cls, []))
            for _ in range(self.long()):
                o.ivars.append((self.symbol(depth), self.value(depth + 1)))
            return o
        if t == "u":
            cls = self.symbol(depth)
            return self.entry(RUser(b"u", cls, self.bytes_()))
        if t == "U":
            cls = self.symbol(depth)
            u = self.entry(RUser(b"U", cls, None))
            u.data = self.value(depth + 1)
            return u
        if t == "f":
            return self.entry(RFloat(self.bytes_()))
        if t == "l":
            sign = self.raw(1)
            n = self.long()
            return self.entry(RBig(sign, self.raw(2 * n)))
        if t == "S":
            cls = self.symbol(depth)
            o = self.entry(RObject(cls, []))
            o.ivars = [(self.symbol(depth), self.value(depth + 1)) for _ in range(self.long())]
            return RUser(b"S", cls, o)
        if t in "cm":
            return self.entry(RUser(t.encode(), None, self.bytes_()))
        if t == "/":
            r = self.entry(RUser(b"/", None, self.bytes_()))
            r.cls = self.byte()
            return r
        if t == "e":
            mod = self.symbol(depth)
            return RExt(mod, self.value(depth + 1))
        raise MarshalError(f"unsupported marshal type {t!r}")


def loads(data: bytes):
    if len(data) < 2 or data[0] != 4 or data[1] != 8:
        raise MarshalError("not Ruby Marshal 4.8")
    r = _Reader(data)
    r.p = 2
    return r.value()


class _Writer:
    def __init__(self):
        self.out = bytearray(b"\x04\x08")
        self.syms: dict[str, int] = {}
        self.objs: dict[int, int] = {}
        self.keep: list = []

    def long(self, x: int) -> None:
        o = self.out
        if x == 0:
            o.append(0)
        elif 0 < x < 123:
            o.append(x + 5)
        elif -124 < x < 0:
            o.append((x - 5) & 0xFF)
        else:
            buf = bytearray()
            for _ in range(4):
                buf.append(x & 0xFF)
                x >>= 8
                if x == 0 or x == -1:
                    break
            n = len(buf)
            o.append(n if x == 0 else (256 - n))
            o += buf

    def bytes_(self, b: bytes) -> None:
        self.long(len(b)); self.out += b

    def symbol(self, s: Sym) -> None:
        if s in self.syms:
            self.out += b";"; self.long(self.syms[s])
        else:
            self.syms[s] = len(self.syms)
            self.out += b":"; self.bytes_(s.encode("utf-8", errors="surrogateescape"))

    def link(self, obj) -> bool:
        i = self.objs.get(id(obj))
        if i is not None:
            self.out += b"@"; self.long(i)
            return True
        self.objs[id(obj)] = len(self.objs)
        self.keep.append(obj)
        return False

    def ivars(self, iv) -> None:
        self.long(len(iv))
        for k, v in iv:
            self.symbol(k); self.value(v)

    def value(self, v) -> None:
        o = self.out
        if v is None:
            o += b"0"
        elif v is True:
            o += b"T"
        elif v is False:
            o += b"F"
        elif isinstance(v, Sym):
            self.symbol(v)
        elif isinstance(v, int):
            if -(2 ** 30) <= v < 2 ** 30:
                o += b"i"; self.long(v)
            else:
                raise MarshalError("int out of fixnum range")
        elif isinstance(v, RExt):
            o += b"e"; self.symbol(v.mod); self.value(v.obj)
        elif isinstance(v, RUser) and v.kind == b"S":
            if self.link(v.data):
                return
            o += b"S"; self.symbol(v.cls); self.ivars(v.data.ivars)
        else:
            if id(v) in self.objs:
                self.link(v)
                return
            iv = getattr(v, "ivars", None)
            if iv is not None and not isinstance(v, RObject):
                o += b"I"
            self.link(v)
            if isinstance(v, RString):
                o += b'"'; self.bytes_(v.data)
            elif isinstance(v, RArray):
                o += b"["; self.long(len(v))
                for x in v:
                    self.value(x)
            elif isinstance(v, RHash):
                dflt, has = v.default
                o += b"}" if has else b"{"
                self.long(len(v.pairs))
                for k, x in v.pairs:
                    self.value(k); self.value(x)
                if has:
                    self.value(dflt)
            elif isinstance(v, RObject):
                o += b"o"; self.symbol(v.cls); self.ivars(v.ivars)
                return
            elif isinstance(v, RUser):
                if v.kind == b"u":
                    o += b"u"; self.symbol(v.cls); self.bytes_(v.data)
                elif v.kind == b"U":
                    o += b"U"; self.symbol(v.cls); self.value(v.data)
                elif v.kind in (b"c", b"m"):
                    o += v.kind; self.bytes_(v.data)
                elif v.kind == b"/":
                    o += b"/"; self.bytes_(v.data); o.append(v.cls)
            elif isinstance(v, RFloat):
                o += b"f"; self.bytes_(v.raw)
            elif isinstance(v, RBig):
                o += b"l" + v.sign; self.long(len(v.data) // 2); o += v.data
            else:
                raise MarshalError(f"cannot dump {type(v).__name__}")
            if iv is not None:
                self.ivars(iv)


def dumps(obj) -> bytes:
    w = _Writer()
    w.value(obj)
    return bytes(w.out)


def utf8_string(text: str, like: RString | None = None) -> RString:
    """New string node; copies the encoding ivars of ``like`` (VX Ace strings carry E=true)."""
    return RString(text.encode("utf-8"), list(like.ivars) if like is not None and like.ivars else None)


__all__ = ["loads", "dumps", "Sym", "RString", "RObject", "RArray", "RHash", "RUser", "MarshalError",
           "utf8_string", "struct"]
