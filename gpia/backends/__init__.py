"""Platform abstraction. One interface, two implementations.

The recorder never talks to X11 or Win32 directly. It calls CaptureBackend.
That is what lets you develop the logic on Linux and ship Windows without
rewriting anything.
"""
from .base import CaptureBackend, WindowInfo, BackendUnavailable
from .factory import available, create, get_backend

__all__ = [
    "CaptureBackend",
    "WindowInfo",
    "BackendUnavailable",
    "available",
    "create",
    "get_backend",
]
