"""JSON contract for capture output.

Compatibility rule: every field the legacy engine reads is emitted with the
same name and meaning. Legacy consumers (evaluar.py, sentinel_primary.py) read
only: t, app, exe, title, pid, hwnd. Those are frozen; the informe fields are
additive.

t      float  seconds since session start (relative, as legacy)
ts     str    ISO-8601 wall clock with timezone (informe #8)
idle_s float  seconds since last mouse/keyboard input (informe #3)
clicks int    cumulative click count at this sample (informe #7)
service str   service code, e.g. "606" (informe #15)
employee str  employee code (informe #9)
state   str   active|paused_idle|filtered
"""
import json
import time
from datetime import datetime, timezone

SCHEMA_VERSION = 2

LEGACY_FIELDS = ("t", "app", "exe", "title", "pid", "hwnd")
INFORME_FIELDS = ("ts", "idle_s", "clicks", "service", "employee", "state")


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def session_start_iso():
    return now_iso()


def make_sample(*, elapsed, window, idle_s, clicks, service, employee, state,
                paused_by=None):
    """Build one gp_log.jsonl line. window is None when nothing is foreground.

    `paused_by` distingue una pausa por idle de una pausa por administrador
    (informe #17). El motor heredado no lee `state` ni `paused_by`: recalcula
    desde los deltas de `t`, asi que agregar campos aca es seguro.
    """
    w = window or {}
    return {
        "v": SCHEMA_VERSION,
        "t": round(float(elapsed), 2),
        "ts": now_iso(),
        "hwnd": int(w.get("hwnd") or 0),
        "pid": int(w.get("pid") or 0),
        "title": w.get("title") or "",
        "exe": w.get("exe") or "",
        "app": w.get("app") or "",
        "idle_s": round(float(idle_s), 2),
        "clicks": int(clicks),
        "service": service or "",
        "employee": employee or "",
        "state": state,
        "paused_by": paused_by,
    }


def check_legacy_compat(sample):
    """Assert the frozen legacy fields are present. Returns list of problems."""
    return [f for f in LEGACY_FIELDS if f not in sample]


def dumps(sample):
    return json.dumps(sample, ensure_ascii=False)


class JsonlWriter:
    def __init__(self, path, append=False):
        self.path = str(path)
        self.fh = open(self.path, "a" if append else "w", encoding="utf-8")

    def write(self, sample):
        self.fh.write(dumps(sample) + "\n")
        self.fh.flush()

    def close(self):
        try:
            self.fh.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
