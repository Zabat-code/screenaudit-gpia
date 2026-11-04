"""Config: single source of truth. No hardcoded thresholds anywhere in the code.

Every value the recorder needs comes from here. The recorder never decides a
number on its own, so changing behaviour never requires touching code.
"""
import copy
import json
import os

from . import paths as _paths

DEFAULTS = {
    "capture": {
        "region": None,
        "fps": 10,
        "crf": 23,
        "preset": "ultrafast",
        "record_video": True,
        "draw_mouse": True,
        # Minimum age of a video segment before idle is allowed to close it.
        # Measured: X11 grab needs ~3s to deliver its first frame, and a
        # segment stopped before that finalises as a 0-byte file. Without this
        # floor every brief idle produces an empty video.
        "min_segment_seconds": 5.0,
    },
    "audio": {
        "enabled": False
    },
    "idle": {
        "pause_after_seconds": 180,
        "resume_after_seconds": 5,
    },
    "sample_interval_seconds": 2.0,
    "policy": {
        "mode": "all",
        "include": [],
        "exclude": [],
    },
    "session": {
        "employee": "",
        "service": "",
    },
    "output": {
        "root": None,
        "log_name": "gp_log.jsonl",
    },
}


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def validate(cfg):
    """Raise ValueError with a readable message. Fail loud, not silent."""
    errs = []
    if cfg.get("audio", {}).get("enabled", False):
        errs.append("audio no está permitido en esta versión")
    cap = cfg.get("capture", {})
    if int(cap.get("fps", 0)) <= 0:
        errs.append("capture.fps debe ser > 0")
    if not (0 <= int(cap.get("crf", 0)) <= 51):
        errs.append("capture.crf debe estar entre 0 y 51")
    if float(cfg.get("sample_interval_seconds", 0)) <= 0:
        errs.append("sample_interval_seconds debe ser > 0")
    idle = cfg.get("idle", {})
    pa = float(idle.get("pause_after_seconds", 0))
    re_ = float(idle.get("resume_after_seconds", 0))
    if pa <= 0:
        errs.append("idle.pause_after_seconds debe ser > 0 (informe #3 = 180)")
    if re_ < 0:
        errs.append("idle.resume_after_seconds no puede ser negativo")
    if re_ > pa:
        errs.append("idle.resume_after_seconds debe ser <= pause_after_seconds")
    mode = cfg.get("policy", {}).get("mode")
    if mode not in ("all", "include", "exclude"):
        errs.append("policy.mode debe ser: all | include | exclude")
    if errs:
        raise ValueError("config invalida:\n  - " + "\n  - ".join(errs))
    return cfg


def config_path(explicit=None):
    if explicit:
        return os.path.abspath(explicit)
    return _paths.user_config_file()


def load(path=None):
    """Load user config merged over DEFAULTS. Missing file = pure defaults."""
    p = config_path(path)
    if not os.path.exists(p):
        return copy.deepcopy(DEFAULTS)
    try:
        with open(p, encoding="utf-8") as fh:
            user = json.load(fh)
    except Exception as exc:
        raise ValueError(f"config ilegible ({p}): {exc}") from exc
    return validate(_deep_merge(DEFAULTS, user))


def save(cfg, path=None):
    p = config_path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, p)
    return p


def write_default(path=None):
    return save(copy.deepcopy(DEFAULTS), path)
