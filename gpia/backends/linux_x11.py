"""X11 backend (Linux - used to prove the logic before moving to Windows).

Requires a DISPLAY and python-xlib. The package imports fine without them:
the X11 import is lazy, so `import gpia` never fails on a headless box.
Only instantiating this backend needs the libs.
"""
import os
import threading

from .base import CaptureBackend, WindowInfo, BackendUnavailable

_display = None
_xlib = None


def _load():
    """Lazy import so the package stays importable without a display."""
    global _display, _xlib
    if _xlib is not None:
        return _xlib
    if not os.environ.get("DISPLAY"):
        raise BackendUnavailable("DISPLAY no esta definido: no hay sesion grafica")
    try:
        from Xlib import display as _dpy
        from Xlib import X, Xatom, protocol
        import Xlib.ext.xtest  # noqa: F401
    except ImportError as exc:
        raise BackendUnavailable(
            "falta python-xlib: pip install python-xlib"
        ) from exc
    try:
        d = _dpy.Display()
    except Exception as exc:
        raise BackendUnavailable(f"no se pudo abrir DISPLAY: {exc}") from exc
    globals()["_display"] = d
    _xlib = {
        "display": d, "X": X, "Xatom": Xatom,
        "protocol": protocol,
        "root": d.screen().root,
    }
    return _xlib


class X11Backend(CaptureBackend):
    name = "x11"

    def __init__(self):
        self.x = _load()
        self._click_thread = None
        self._click_stop = threading.Event()
        self._display = self.x["display"]
        self._root = self.x["root"]
        self._idle_cache = {"t": 0.0, "v": 0.0}
        self.clicks_available = True
        self._click_error = None

    # ---------- window ----------
    def foreground_window(self):
        X, Xatom = self.x["X"], self.x["Xatom"]
        atom = self._root.get_full_property(
            self._display.intern_atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW)
        if not atom or not atom.value:
            return None
        win_id = atom.value[0]
        try:
            win = self._root.create_resource_object("window", win_id)
        except Exception:
            return None

        title = ""
        wm_class = ""
        pid = 0
        for prop, kind in (("_NET_WM_NAME", "utf8"), ("WM_NAME", "latin1")):
            if not title:
                try:
                    a = win.get_full_property(
                        self._display.intern_atom(prop), X.AnyPropertyType)
                    if a and a.value:
                        title = a.value.decode(kind, "replace")
                except Exception:
                    pass
        try:
            a = win.get_full_property(
                self._display.intern_atom("WM_CLASS"), self.x["X"].AnyPropertyType)
            if a and a.value:
                wm_class = a.value.decode("latin1", "replace")
        except Exception:
            pass
        try:
            a = win.get_full_property(
                self._display.intern_atom("_NET_WM_PID"), self.x["X"].AnyPropertyType)
            if a and a.value:
                pid = int(a.value[0])
        except Exception:
            pass

        # WM_CLASS is "instance\0class". Prefer the class (real app name).
        exe = wm_class.split("\0")
        exe = (exe[1] if len(exe) > 1 and exe[1] else (exe[0] if exe[0] else ""))
        exe = (exe or "").lower().strip()
        app = exe[:-4] if exe.endswith(".exe") else exe
        return WindowInfo(hwnd=win_id, pid=pid, title=title, exe=exe, app=app)

    # ---------- idle ----------
    def idle_seconds(self):
        """MIT-SCREEN-SAVER idle time. Falls back to pointer-position polling."""
        X = self.x["X"]
        try:
            if not hasattr(self, "_screensaver"):
                self._screensaver = self._display.screen().root
                from Xlib.ext import screensaver
                self._ssa = screensaver
            info = self._ssa.query_info(self._display)
            # query_info returns (info, t) where t is idle ms
            if isinstance(info, tuple):
                ms = info[1]
            else:
                ms = getattr(info, "idle", 0)
            return float(ms) / 1000.0
        except Exception:
            return self._idle_by_pointer()

    def _idle_by_pointer(self):
        """No screensaver ext: track when the pointer last moved."""
        import time
        try:
            px, py, _ = self._root.query_pointer()
            now = time.monotonic()
            if (px, py) != (self._idle_cache.get("px"), self._idle_cache.get("py")):
                self._idle_cache = {"px": px, "py": py, "t": now}
            return max(0.0, now - self._idle_cache.get("t", now))
        except Exception:
            return 0.0

    # ---------- geometry ----------
    def screen_geometry(self):
        try:
            s = self._display.screen()
            return (s.width_in_pixels, s.height_in_pixels)
        except Exception:
            return None

    # ---------- clicks ----------
    def start_click_hook(self, on_click):
        """Root-level ButtonPressMask.

        Limitation known: if another client (normally the window manager)
        already holds ButtonPressMask on the root window, the X server answers
        BadAccess and no click is captured. We do NOT pretend otherwise: we set
        clicks_available=False and the session summary reports it.

        On a busy desktop the correct upgrade is the X RECORD extension, which
        does not compete for the event mask. The Windows backend has no such
        limitation (WH_MOUSE_LL is per-process and always works), so this is a
        Linux-development-only gap.
        """
        X = self.x["X"]
        self.clicks_available = True
        self._click_error = None
        captured = {}

        def on_error(err, request):
            captured["err"] = str(err)
            return 0

        try:
            self._display.set_error_handler(on_error)
            self._root.change_attributes(event_mask=X.ButtonPressMask)
            self._display.sync()   # X is async: the error only lands here
        except Exception as exc:
            self.clicks_available = False
            self._click_error = str(exc)
            return False
        finally:
            try:
                self._display.set_error_handler(None)
            except Exception:
                pass
        if captured.get("err"):
            self.clicks_available = False
            self._click_error = captured["err"]
            return False

        def loop():
            while not self._click_stop.is_set():
                if not self._display.pending_events():
                    self._display.sync()
                    if not self._display.pending_events():
                        self._click_stop.wait(0.05)
                        continue
                try:
                    ev = self._display.next_event()
                except Exception:
                    break
                if ev.type == X.ButtonPress:
                    try:
                        on_click(int(ev.root_x), int(ev.root_y), int(ev.detail))
                    except Exception:
                        pass

        self._click_thread = threading.Thread(target=loop, daemon=True)
        self._click_thread.start()
        return True

    def stop_click_hook(self):
        self._click_stop.set()
        if self._click_thread:
            self._click_thread.join(timeout=1.0)
            self._click_thread = None

    def describe(self):
        d = super().describe()
        d["display"] = os.environ.get("DISPLAY", "")
        d["geometry"] = self.screen_geometry()
        d["clicks_available"] = self.clicks_available
        if self._click_error:
            d["click_error"] = self._click_error
        return d
