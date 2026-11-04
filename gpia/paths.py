"""Per-user paths. Informe #1: nothing is written outside the user's own
profile. Works the same on Linux (test) and Windows (target); no edits needed.
"""
import os
import sys

APP = "ScreenAudit"


def home():
    return os.path.expanduser("~")


def is_windows():
    return sys.platform.startswith("win")


def user_config_dir():
    if is_windows():
        base = os.environ.get("APPDATA") or os.path.join(home(), "AppData", "Roaming")
        return os.path.join(base, APP)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home(), ".config")
    return os.path.join(base, "ScreenAudit")


def user_data_dir():
    if is_windows():
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.join(home(), "AppData", "Local"))
        return os.path.join(base, APP)
    base = (os.environ.get("XDG_DATA_HOME")
            or os.path.join(home(), ".local", "share"))
    return os.path.join(base, "ScreenAudit")


def user_config_file():
    return os.path.join(user_config_dir(), "config.json")


def sessions_dir():
    return os.path.join(user_data_dir(), "sessions")


_UNSAFE = '<>:"/\\|?*\0'


def slugify(value, fallback="general"):
    """Filesystem-safe folder name. Informe #15: one folder per service.

    A service name is free text in config and may contain a slash, a colon or
    a non-ASCII character, any of which breaks the path on Windows. So the
    name is normalised here rather than trusted.
    """
    if value is None:
        return fallback
    s = str(value).strip()
    for ch in _UNSAFE:
        s = s.replace(ch, "_")
    s = "".join(ch for ch in s if ord(ch) >= 32).strip(". ")
    s = s.replace(" ", "_")
    if not s:
        return fallback
    return s[:64]


def service_dir(service):
    """sessions/<service>/ -- physical separation per informe #15."""
    return os.path.join(sessions_dir(), slugify(service))


def ensure(path):
    os.makedirs(path, exist_ok=True)
    return path
