"""Null backend: no OS access. Two uses.

1. `python -m gpia selftest` on a machine with no display (CI, this box).
2. The junior's safety net: the whole recorder loop can be exercised with
   synthetic windows/clicks, so the logic is provable before touching X11
   or Win32. The recorder code is identical in both cases.
"""
import itertools
import time

from .base import CaptureBackend, WindowInfo

SYNTHETIC_WINDOWS = [
    ("code.exe", "main.py - Visual Studio Code"),
    ("chrome.exe", "github.com - pull request"),
    ("chrome.exe", "netflix.com - watch"),
    ("slack.exe", "#general - equipo"),
    ("excel.exe", "presupuesto_606.xlsx - Excel"),
    ("explorer.exe", "Documentos"),
]


class NullBackend(CaptureBackend):
    name = "null"

    def __init__(self, windows=None, idle_seq=None, click_every=3.0):
        self._windows = windows or SYNTHETIC_WINDOWS
        self._cycle = itertools.cycle(self._windows)
        self._idle_seq = list(idle_seq) if idle_seq else [0.0, 0.0, 200.0, 0.0]
        self._idx = 0
        self._idle = 0.0
        self._t0 = time.monotonic()
        self._clicks = 0
        self._click_every = click_every
        self._hook = None

    def foreground_window(self):
        exe, title = next(self._cycle)
        app = exe[:-4] if exe.endswith(".exe") else exe
        return WindowInfo(hwnd=self._clicks + 1, pid=1000 + self._idx,
                          title=title, exe=exe, app=app)

    def idle_seconds(self):
        v = self._idle_seq[self._idx % len(self._idle_seq)]
        self._idx += 1
        return float(v)

    def screen_geometry(self):
        return (1920, 1080)

    def start_click_hook(self, on_click):
        import threading
        self._hook = on_click

        def loop():
            while True:
                time.sleep(self._click_every)
                try:
                    on_click(100, 200, 1)
                except Exception:
                    return
        threading.Thread(target=loop, daemon=True).start()
        return True

    def stop_click_hook(self):
        self._hook = None

    def describe(self):
        return {"backend": self.name, "synthetic": True}
