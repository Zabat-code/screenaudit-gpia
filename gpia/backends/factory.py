"""Backend selection. Explicit first (config/env), auto-detect second.

GPIA_BACKEND=win32|x11|null  overrides. Useful for CI and for the senior's
Windows box without editing code.
"""
import os

from .base import BackendUnavailable


def available():
    """Names of backends that can actually run here, best first."""
    import sys
    found = []
    if sys.platform.startswith("win"):
        found.append("win32")
    elif sys.platform.startswith("linux"):
        found.append("x11")
    return found


def create(name):
    if name in (None, "", "auto"):
        names = available()
        if not names:
            raise BackendUnavailable(
                "sin backend para esta plataforma: solo win32 y x11")
        name = names[0]
    if name == "win32":
        from .windows import Win32Backend
        return Win32Backend()
    if name == "x11":
        from .linux_x11 import X11Backend
        return X11Backend()
    if name == "null":
        from .null import NullBackend
        return NullBackend()
    raise BackendUnavailable(f"backend desconocido: {name}")


def get_backend(name=None):
    name = name or os.environ.get("GPIA_BACKEND") or "auto"
    return create(name)
