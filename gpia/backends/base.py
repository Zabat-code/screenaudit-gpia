"""Interface every platform must satisfy.

Keep this file small. Anything added here must be implemented in both
linux_x11.py and windows.py, so the abstraction stays honest.
"""
from dataclasses import dataclass


class BackendUnavailable(RuntimeError):
    """Raised when the backend cannot run here (no display, missing lib)."""


@dataclass
class WindowInfo:
    hwnd: int = 0
    pid: int = 0
    title: str = ""
    exe: str = ""
    app: str = ""


class CaptureBackend:
    """Everything the recorder needs from the operating system."""

    name = "abstract"

    def foreground_window(self):
        """Return WindowInfo, or None when nothing is foreground."""
        raise NotImplementedError

    def idle_seconds(self):
        """Seconds since last mouse/keyboard input. Informe #3."""
        raise NotImplementedError

    def screen_geometry(self):
        """Return (width, height) of the virtual desktop, or None."""
        raise NotImplementedError

    def start_click_hook(self, on_click):
        """Begin reporting clicks. on_click(x, y, button) per event. #7"""
        raise NotImplementedError

    def stop_click_hook(self):
        raise NotImplementedError

    def is_available(self):
        try:
            self.foreground_window()
            return True
        except Exception:
            return False

    def describe(self):
        return {"backend": self.name}
