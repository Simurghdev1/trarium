"""Windows-only test driver: run a GameMaker 8 game, send scripted key presses and
capture screenshots of the game window.  Used by CI (windows-latest) to verify a
translated build.  Stdlib + Pillow only.

Plan file (JSON list of steps):
  ["wait", seconds]
  ["key", "z"|"x"|"up"|"down"|"left"|"right"|"enter", hold_seconds?]
  ["hold", key, seconds]                  # hold a key down (walking)
  ["shot", "name"]                        # named screenshot
  ["press_every", key, interval, total]   # press key repeatedly
Periodic screenshots are also taken every --every seconds (named t####.png).
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import threading
import time
from ctypes import wintypes

from PIL import ImageGrab

user32 = ctypes.WinDLL("user32", use_last_error=True)
VK = {"z": 0x5A, "x": 0x58, "c": 0x43, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
      "enter": 0x0D, "shift": 0x10}
SC = {"z": 0x2C, "x": 0x2D, "c": 0x2E, "up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D,
      "enter": 0x1C, "shift": 0x2A}
EXT = {"up", "down", "left", "right"}

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(key: str, up: bool) -> None:
    flags = (0x0002 if up else 0) | (0x0001 if key in EXT else 0)
    inp = INPUT(type=1, u=_U(ki=KEYBDINPUT(VK[key], SC[key], flags, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class Game:
    def __init__(self, exe: str, out: str, every: float):
        self.exe = exe
        self.out = out
        self.every = every
        self.hwnd = None
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.stop = False
        self.log = open(os.path.join(out, "driver.log"), "a", encoding="utf-8")
        os.makedirs(os.path.join(out, "periodic"), exist_ok=True)

    def say(self, *a):
        line = "[%7.2f] " % (time.time() - self.t0) + " ".join(str(x) for x in a)
        print(line, flush=True)
        self.log.write(line + "\n")
        self.log.flush()

    def start(self):
        self.proc = subprocess.Popen([self.exe], cwd=os.path.dirname(os.path.abspath(self.exe)))
        self.say("started pid", self.proc.pid)
        for _ in range(120):
            time.sleep(0.5)
            self.hwnd = self._find()
            if self.hwnd:
                break
        self.say("hwnd", self.hwnd)
        for _ in range(20):
            if self.dismiss_dialogs():
                break
            time.sleep(0.5)
        time.sleep(1)
        self.hwnd = self._find() or self.hwnd
        self.focus()
        threading.Thread(target=self._periodic, daemon=True).start()

    def windows(self):
        """All visible top-level windows of the game: [(area, hwnd, title, w, h)]."""
        found = []
        pid = self.proc.pid

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, _l):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == pid and user32.IsWindowVisible(h):
                r = wintypes.RECT()
                user32.GetClientRect(h, ctypes.byref(r))
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(h, buf, 256)
                w, hh = r.right - r.left, r.bottom - r.top
                found.append((w * hh, h, buf.value, w, hh))
            return True
        user32.EnumWindows(cb, 0)
        return sorted(found, reverse=True)

    def dismiss_dialogs(self):
        """Click OK on message boxes of the game (e.g. 'No sound device detected' on CI)."""
        pid = self.proc.pid
        hits = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(h, _l):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(h, cls, 64)
            if p.value == pid and cls.value == "#32770" and user32.IsWindowVisible(h):
                hits.append(h)
            return True
        user32.EnumWindows(cb, 0)
        for h in hits:
            texts = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def ccb(c, _l):
                buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(c, buf, 512)
                cls = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(c, cls, 64)
                texts.append((cls.value, buf.value, c))
                return True
            user32.EnumChildWindows(h, ccb, 0)
            self.say("dialog", [(a, b) for a, b, _ in texts])
            buttons = [c for a, b, c in texts if a == "Button"]
            if buttons:
                user32.SendMessageW(buttons[0], 0x00F5, 0, 0)   # BM_CLICK
            else:
                user32.PostMessageW(h, 0x0010, 0, 0)            # WM_CLOSE
        return len(hits)

    def _find(self):
        ws = self.windows()
        return ws[0][1] if ws and ws[0][3] >= 300 else None

    def focus(self):
        if not self.hwnd:
            return
        fg = user32.GetForegroundWindow()
        a = user32.GetWindowThreadProcessId(fg, None)
        b = user32.GetWindowThreadProcessId(self.hwnd, None)
        user32.AttachThreadInput(a, b, True)
        user32.ShowWindow(self.hwnd, 9)
        user32.BringWindowToTop(self.hwnd)
        user32.SetForegroundWindow(self.hwnd)
        user32.AttachThreadInput(a, b, False)

    def rect(self):
        r = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(r))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        return (pt.x, pt.y, pt.x + r.right, pt.y + r.bottom)

    def grab(self, path):
        with self.lock:
            try:
                if not self.hwnd:
                    self.hwnd = self._find()
                bbox = self.rect() if self.hwnd else None
                img = ImageGrab.grab(bbox=bbox, all_screens=True)
                img.save(path)
                return True
            except Exception as e:  # noqa: BLE001
                self.say("grab failed", e)
                return False

    def _periodic(self):
        k = 0
        os.makedirs(os.path.join(self.out, "full"), exist_ok=True)
        last = None
        while not self.stop:
            t = time.time() - self.t0
            self.dismiss_dialogs()
            ws = [(w[2], w[3], w[4]) for w in self.windows()]
            if ws != last:
                self.say("windows", ws)
                last = ws
            self.hwnd = self._find() or self.hwnd
            if k % 10 == 0:
                try:
                    ImageGrab.grab(all_screens=True).save(os.path.join(self.out, "full", "f%06.1f.png" % t))
                except Exception as e:  # noqa: BLE001
                    self.say("full grab failed", e)
            k += 1
            self.grab(os.path.join(self.out, "periodic", "t%06.1f.png" % t))
            time.sleep(self.every)

    def key(self, k, hold=0.09):
        self.focus()
        _send(k, False)
        time.sleep(hold)
        _send(k, True)
        time.sleep(0.05)

    def run(self, plan):
        for step in plan:
            op = step[0]
            if self.proc.poll() is not None:
                self.say("game exited", self.proc.returncode)
                break
            if op == "wait":
                time.sleep(step[1])
            elif op == "key":
                self.say("key", step[1])
                self.key(step[1], step[2] if len(step) > 2 else 0.09)
            elif op == "hold":
                self.say("hold", step[1], step[2])
                self.key(step[1], step[2])
            elif op == "shot":
                ok = self.grab(os.path.join(self.out, step[1] + ".png"))
                self.say("shot", step[1], ok)
            elif op == "press_every":
                _, k, interval, total = step
                end = time.time() + total
                while time.time() < end and self.proc.poll() is None:
                    self.key(k)
                    time.sleep(max(0.0, interval - 0.14))
            elif op == "log":
                self.say(*step[1:])
        self.stop = True
        time.sleep(1)
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--every", type=float, default=1.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    g = Game(a.exe, a.out, a.every)
    g.start()
    g.run(json.load(open(a.plan, encoding="utf-8")))


if __name__ == "__main__":
    main()
