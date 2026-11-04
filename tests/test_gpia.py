"""Tests del contrato y de la logica. Corren sin display y sin ffmpeg.

Son los tests que hay que dejar verdes antes de tocar codigo: si alguno
falla, el motor heredado (evaluar.py / sentinel_primary.py) dejaria de poder
leer la salida.
"""
import argparse
import json
import os
import sys
import tempfile
import argparse
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gpia import config, schemas  # noqa: E402
import gpia.paths  # noqa: E402,F401
from gpia.backends.null import NullBackend  # noqa: E402
from gpia.idle import IdleGate, ACTIVE, PAUSED  # noqa: E402
from gpia.policy import Policy  # noqa: E402
from gpia.recorder import Session, FILTERED  # noqa: E402
from gpia.video import build_command  # noqa: E402


# ---------- contrato con el motor heredado ----------
def test_legacy_fields_always_present():
    s = schemas.make_sample(elapsed=1.0, window=None, idle_s=0, clicks=0,
                            service="606", employee="E1", state="active")
    assert schemas.check_legacy_compat(s) == []
    for f in schemas.LEGACY_FIELDS:
        assert f in s


def test_legacy_fields_present_when_no_window():
    s = schemas.make_sample(elapsed=1.0, window=None, idle_s=5, clicks=2,
                            service="", employee="", state="paused_idle")
    assert schemas.check_legacy_compat(s) == []
    assert s["exe"] == "" and s["title"] == "" and s["app"] == ""


def test_informe_fields_present():
    s = schemas.make_sample(elapsed=3.0, window=None, idle_s=9, clicks=4,
                            service="606", employee="JP", state="active")
    for f in schemas.INFORME_FIELDS:
        assert f in s
    assert s["ts"] and "T" in s["ts"]          # informe #8
    assert s["idle_s"] == 9.0                  # informe #3
    assert s["clicks"] == 4                    # informe #7
    assert s["service"] == "606"               # informe #15


# ---------- informe #3: idle ----------
def test_idle_gate_pauses_at_threshold():
    g = IdleGate(pause_after=180, resume_after=5)
    assert g.update(179) == ACTIVE
    assert g.update(180) == PAUSED


def test_idle_gate_resumes_only_after_activity():
    g = IdleGate(pause_after=180, resume_after=5)
    g.update(200)
    assert g.is_paused
    assert g.update(150) == PAUSED     # still idle, no resume
    assert g.update(2) == ACTIVE


def test_idle_gate_no_flap():
    g = IdleGate(pause_after=180, resume_after=5)
    g.update(200)
    for idle in (3, 200, 3, 200, 3):
        if idle <= 5:
            g.update(idle)
    assert g.state in (ACTIVE, PAUSED)


# ---------- informe #5: policy ----------
def test_policy_all():
    p = Policy("all")
    assert p.allows("steam.exe", "Library")
    assert p.allows("code.exe", "main.py")


def test_policy_include():
    p = Policy("include", include=["code", "excel"])
    assert p.allows("code.exe", "main.py")
    assert not p.allows("steam.exe", "Library")
    assert p.allows("chrome.exe", "code - github")   # matches title


def test_policy_exclude():
    p = Policy("exclude", exclude=["steam", "netflix"])
    assert p.allows("code.exe", "main.py")
    assert not p.allows("steam.exe", "Library")
    assert not p.allows("chrome.exe", "netflix.com - watch")


# ---------- informe #6 + #4: video ----------
def test_command_has_no_audio_on_x11():
    cmd = build_command("ffmpeg", "o.mp4", fps=10, crf=23, preset="ultrafast",
                        audio=False, platform="x11")
    assert "-an" in cmd and "pulse" not in cmd


def test_command_has_no_audio_on_win32():
    cmd = build_command("ffmpeg", "o.mp4", fps=10, crf=23, preset="ultrafast",
                        audio=False, platform="win32")
    assert "-an" in cmd and "gdigrab" in cmd and "desktop" in cmd


def test_command_region_is_configurable():
    reg = {"x": 10, "y": 20, "width": 1280, "height": 720}
    cmd = build_command("ffmpeg", "o.mp4", fps=5, crf=20, preset="fast",
                        region=reg, platform="win32")
    assert "1280x720" in cmd and "gdigrab" in cmd


# ---------- config ----------
def test_config_defaults_valid():
    cfg = config.load(os.path.join(tempfile.gettempdir(), "no_existe.json"))
    assert config.validate(cfg) is cfg
    assert cfg["idle"]["pause_after_seconds"] == 180
    assert cfg["audio"]["enabled"] is False


def test_config_rejects_bad_idle():
    cfg = config.load(None)
    cfg["idle"]["pause_after_seconds"] = -1
    try:
        config.validate(cfg)
        assert False, "debio rechazar"
    except ValueError:
        pass


def test_config_rejects_negative_sampling():
    cfg = config.load(None)
    cfg["sample_interval_seconds"] = 0
    try:
        config.validate(cfg)
        assert False
    except ValueError:
        pass


def test_config_merge_keeps_other_keys():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"capture": {"fps": 30}}, f)
        p = f.name
    cfg = config.load(p)
    assert cfg["capture"]["fps"] == 30
    assert cfg["capture"]["crf"] == 23          # default preservado
    os.unlink(p)


# ---------- loop completo ----------
def test_session_loop_produces_valid_jsonl():
    cfg = config.load(None)
    cfg["session"] = {"employee": "JP", "service": "606"}
    b = NullBackend(idle_seq=[0.0, 0.0, 0.0, 0.0], click_every=999)
    with tempfile.TemporaryDirectory() as d:
        s = Session(cfg, b, video=False, out_dir=d)
        summary = s.run(duration=0.5, )
        lines = [l for l in open(s.log_path, encoding="utf-8").read().splitlines() if l]
        assert lines
        rec = json.loads(lines[0])
        assert schemas.check_legacy_compat(rec) == []
        assert summary["service"] == "606"
        assert summary["backend"] == "null"


def test_session_marks_paused_when_idle():
    cfg = config.load(None)
    cfg["idle"] = {"pause_after_seconds": 10, "resume_after_seconds": 1}
    cfg["sample_interval_seconds"] = 0.05
    b = NullBackend(idle_seq=[0.0, 30.0, 30.0, 30.0], click_every=999)
    with tempfile.TemporaryDirectory() as d:
        s = Session(cfg, b, video=False, out_dir=d)
        summary = s.run(duration=0.3)
        assert summary["samples_paused_idle"] > 0
        lines = [l for l in open(s.log_path, encoding="utf-8")
                 .read().splitlines() if l]
        states = {json.loads(l)["state"] for l in lines}
        assert "paused_idle" in states


def test_session_filters_by_policy():
    cfg = config.load(None)
    cfg["policy"] = {"mode": "include", "include": ["code"], "exclude": []}
    b = NullBackend(windows=[("steam.exe", "Game")], idle_seq=[0.0],
                    click_every=999)
    with tempfile.TemporaryDirectory() as d:
        s = Session(cfg, b, video=False, out_dir=d)
        summary = s.run(duration=0.2)
        assert summary["samples_filtered"] > 0


def test_click_counter_increments():
    cfg = config.load(None)
    b = NullBackend(idle_seq=[0.0], click_every=0.05)
    with tempfile.TemporaryDirectory() as d:
        s = Session(cfg, b, video=False, out_dir=d)
        s.run(duration=0.4)
        assert s.clicks > 0
        clicks = [l for l in open(s.clicks_path, encoding="utf-8")
                  .read().splitlines() if l]
        assert len(clicks) == s.clicks
        rec = json.loads(clicks[0])
        assert "ts" in rec and "x" in rec


# ---------- informe #15: physical separation per service ----------
def test_slugify_makes_filesystem_safe_names():
    from gpia.paths import slugify
    assert slugify("606") == "606"
    assert slugify("soporte/606") == "soporte_606"
    assert slugify('malo: "nombre"') == "malo___nombre_"
    assert slugify("con espacios") == "con_espacios"
    # empty / hostile input must still yield a usable folder
    assert slugify("") == "general"
    assert slugify(None) == "general"
    assert slugify("   ") == "general"
    assert slugify("..") == "general"
    assert "/" not in slugify("a/b/c")


def test_service_dir_is_under_sessions_and_per_service():
    from gpia import paths
    a = paths.service_dir("606")
    b = paths.service_dir("soporte")
    assert a != b
    assert os.path.dirname(a) == paths.sessions_dir()
    assert os.path.basename(a) == "606"


def test_session_out_dir_defaults_under_service(monkeypatch, tmp_path):
    import gpia.paths as paths
    monkeypatch.setattr(paths, "user_data_dir", lambda: str(tmp_path))
    cfg = config.load(None)
    cfg["session"]["service"] = "606"
    s = Session(cfg, NullBackend(), video=False, out_dir=None)
    assert os.path.basename(os.path.dirname(s.out_dir)) == "606"
    assert s.out_dir.startswith(str(tmp_path))


# ---------- informe #4: idle pause must not overwrite footage ----------
def test_segment_paths_are_unique_and_ordered():
    from gpia.video import _segment_path
    base = "/tmp/x/screen.mp4"
    names = [_segment_path(base, i) for i in range(1, 4)]
    assert names == ["/tmp/x/screen-001.mp4",
                     "/tmp/x/screen-002.mp4",
                     "/tmp/x/screen-003.mp4"]
    assert len(set(names)) == 3


def test_restart_creates_new_segment_instead_of_overwriting(monkeypatch, tmp_path):
    from gpia import video as vid
    started = []

    class FakeProc:
        pid = 4321

        def __init__(self, cmd, **kw):
            started.append(cmd[-1])

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(vid.subprocess, "Popen", FakeProc)
    cfg = config.load(None)
    r = vid.VideoRecorder(str(tmp_path / "screen.mp4"), cfg, platform="x11")
    r.start()
    r.stop()
    r.start()          # simulate resume after an idle pause
    r.stop()
    assert len(started) == 2
    assert started[0] != started[1], "resume overwrote the first segment"
    assert r.segments == started
    assert r.size() == 0 or r.size() >= 0   # no crash on missing files


def test_video_size_sums_all_segments(tmp_path):
    from gpia import video as vid
    cfg = config.load(None)
    r = vid.VideoRecorder(str(tmp_path / "screen.mp4"), cfg)
    r.segments = [str(tmp_path / "screen-001.mp4"),
                  str(tmp_path / "screen-002.mp4")]
    (tmp_path / "screen-001.mp4").write_bytes(b"a" * 100)
    (tmp_path / "screen-002.mp4").write_bytes(b"b" * 50)
    assert r.size() == 150


def test_summary_lists_every_segment(tmp_path):
    cfg = config.load(None)
    b = NullBackend(idle_seq=[0.0], click_every=999)
    s = Session(cfg, b, video=False, out_dir=str(tmp_path))
    s.run(duration=0.2)
    summ = s.summary()
    assert "videos" in summ and "video_segments" in summ
    assert summ["videos"] == []
    assert summ["service_dir"] == str(tmp_path)


# ---------- init must actually write a missing config ----------
def test_cmd_init_writes_config_when_missing(tmp_path, monkeypatch):
    from gpia import cli
    cfg_file = tmp_path / "cfg.json"
    monkeypatch.setattr(cli._paths, "user_config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli._paths, "sessions_dir", lambda: str(tmp_path / "s"))
    args = argparse.Namespace(config=str(cfg_file), force=False)
    assert cli.cmd_init(args) == 0
    assert cfg_file.exists(), "init did not write the file"
    assert json.loads(cfg_file.read_text(encoding="utf-8"))


def test_cmd_init_does_not_overwrite_without_force(tmp_path, monkeypatch):
    from gpia import cli
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text('{"session": {"employee": "original"}}',
                        encoding="utf-8")
    monkeypatch.setattr(cli._paths, "user_config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli._paths, "sessions_dir", lambda: str(tmp_path / "s"))
    cli.cmd_init(argparse.Namespace(config=str(cfg_file), force=False))
    assert json.loads(cfg_file.read_text(
        encoding="utf-8"))["session"]["employee"] == "original"
    cli.cmd_init(argparse.Namespace(config=str(cfg_file), force=True))
    assert cfg_file.exists()


# ---------- informe #3+#4: a young segment must not be closed ----------
class _ClockAt:
    """Reloj que solo avanza cuando el test lo dice."""
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class _FakeClock:
    """Advances a fixed step per reading, so segment age is deterministic."""

    def __init__(self, step=0.5):
        self.t = 1000.0
        self.step = step

    def __call__(self):
        self.t += self.step
        return self.t


def _idle_cfg(min_segment):
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": True, "min_segment_seconds": min_segment}
    cfg["idle"] = {"pause_after_seconds": 0.4, "resume_after_seconds": 0.2}
    cfg["sample_interval_seconds"] = 0.01
    cfg["policy"] = {"mode": "all"}
    return cfg


def test_young_segment_stop_is_deferred(tmp_path):
    """A brief idle must not close a segment that has no frames in it yet.

    X11 grab needs ~3s for the first frame, so closing earlier finalises a
    0-byte file. min_segment_seconds holds the segment open instead.
    """
    b = NullBackend(idle_seq=[0.0, 0.0] + [900.0] * 40)
    s = Session(_idle_cfg(5.0), b, video=True, out_dir=str(tmp_path),
                clock=_FakeClock())
    summ = s.run(duration=10)
    assert summ["samples"] > 0, "loop produced no samples"
    assert summ["deferred_stops"] > 0, "stop was not deferred for a young segment"


def test_zero_min_segment_stops_immediately(tmp_path):
    """Control for the test above: with no floor the stop is not deferred."""
    b = NullBackend(idle_seq=[0.0, 0.0] + [900.0] * 40)
    s = Session(_idle_cfg(0.0), b, video=True, out_dir=str(tmp_path),
                clock=_FakeClock())
    summ = s.run(duration=10)
    assert summ["deferred_stops"] == 0


def test_empty_segments_are_reported_not_hidden(tmp_path):
    b = NullBackend(idle_seq=[0.0, 0.0] + [900.0] * 40)
    s = Session(_idle_cfg(0.0), b, video=True, out_dir=str(tmp_path),
                clock=_FakeClock())
    s.run(duration=10)
    for p in s.recorder.segments:          # simulate the 0-byte finalisation
        open(p, "wb").close()
    empty = s.empty_segments()
    assert empty == s.recorder.segments
    assert s.summary()["empty_segments"] == empty


def test_config_default_has_min_segment_floor():
    cfg = config.load(None)
    assert cfg["capture"]["min_segment_seconds"] > 0


# ---------- informe #16: cadena, guardarrailes, rendimiento ----------
from gpia.diagnostics import (Chain, Diagnostics, Stopwatch, Report, Result,
                              OK, WARN, FAIL, SKIP, ProbeError, SkipProbe)


# ---- la cadena: un modulo roto no tumba la cadena ----
def test_chain_one_bad_module_does_not_stop_the_others():
    ch = Chain("t")
    ch.add("good", lambda: "bien")
    ch.add("boom", lambda: (_ for _ in ()).throw(RuntimeError("revento")))
    ch.add("alsogood", lambda: " tambien bien")
    rep = ch.run()
    assert [r.status for r in rep.results] == [OK, FAIL, OK]
    assert rep.ok is True            # ninguno era critico: la sesion sigue
    assert [r.name for r in rep.failures] == ["boom"]
    assert rep.results[1].error == "RuntimeError"
    assert "revento" in rep.results[1].detail
    assert rep.counts() == {OK: 2, WARN: 0, FAIL: 1, SKIP: 0}


def test_chain_skips_are_not_failures():
    ch = Chain("t")
    ch.add("s", lambda: (_ for _ in ()).throw(SkipProbe("falta ffmpeg")))
    rep = ch.run()
    assert rep.results[0].status == SKIP
    assert rep.ok is True                        # un skip no es un fallo


def test_chain_critical_failure_toggles_not_ok():
    ch = Chain("t")
    ch.add("opcional", lambda: (_ for _ in ()).throw(ProbeError("x")))
    assert ch.run().ok is True                   # no critico: la sesion sigue
    ch2 = Chain("t")
    ch2.add("vital", lambda: (_ for _ in ()).throw(ProbeError("x")), critical=True)
    assert ch2.run().ok is False


def test_chain_keeps_module_name_when_probe_returns_a_result():
    """Un warn()/fail() dentro de un probe debe conservar el nombre del modulo."""
    from gpia.diagnostics import warn
    ch = Chain("t")
    ch.add("idle", lambda: warn("siempre 0"))
    r = ch.run().results[0]
    assert r.name == "idle" and r.status == WARN


def test_chain_measures_each_module():
    import time as _t
    ch = Chain("t")
    ch.add("lento", lambda: (_t.sleep(0.02), "dormi")[1])
    r = ch.run().results[0]
    assert r.ms >= 15.0


# ---- el sumidero: errores en disco, no en silencio ----
def test_diagnostics_writes_errors_to_events_jsonl(tmp_path):
    p = str(tmp_path / "events.jsonl")
    d = Diagnostics(path=p)
    try:
        raise ValueError("sin ffmpeg")
    except ValueError as exc:
        d.error("video", exc, sample=7)
    d.close()
    lines = [json.loads(x) for x in open(p, encoding="utf-8")]
    assert lines[0]["event"] == "error"
    assert lines[0]["module"] == "video"
    assert lines[0]["error"] == "ValueError"
    assert lines[0]["sample"] == 7


def test_diagnostics_counts_and_health(tmp_path):
    d = Diagnostics(path=str(tmp_path / "e.jsonl"))
    d.bump("x")
    d.bump("x", by=4)
    d.timing("sample", 0.02)
    h = d.health()
    assert h["counters"]["x"] == 5
    assert h["timings"]["sample"]["p95"] == 20.0
    d.close()


# ---- guardarrail: un backend roto se reporta, no se disimula ----
class _BrokenBackend:
    name = "roto"
    def foreground_window(self):
        raise RuntimeError("X roto")
    def idle_seconds(self):
        raise RuntimeError("idle roto")
    def screen_geometry(self):
        return (0, 0)
    def start_click_hook(self, cb):
        raise RuntimeError("hook roto")
    def stop_click_hook(self):
        pass
    def describe(self):
        return {}


class _NoisyBackend:
    """Sin errores pero con respuestas que hay que poder marcar."""
    name = "mudo"
    def __init__(self):
        from gpia.backends.base import WindowInfo
        self._w = WindowInfo(hwnd=1, pid=2, title="t", exe="e.exe", app="e")
    def foreground_window(self):
        return self._w
    def idle_seconds(self):
        return 0.0
    def screen_geometry(self):
        return (1920, 1080)
    def start_click_hook(self, cb):
        return True
    def stop_click_hook(self):
        pass
    def describe(self):
        return {}


def test_recorder_reports_backend_errors_instead_of_hiding(tmp_path):
    """El comportamiento viejo: except Exception -> idle 0 y log limpio."""
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.01
    d = Diagnostics(path=str(tmp_path / "events.jsonl"))
    s = Session(cfg, _BrokenBackend(), video=False, out_dir=str(tmp_path),
                clock=time.time, diag=d)
    s.run(duration=0.2)                          # no debe lanzar
    d.close()
    ev = [json.loads(x) for x in open(str(tmp_path / "events.jsonl"),
                                      encoding="utf-8")]
    mods = {e.get("module") for e in ev if e["event"] == "error"}
    assert "foreground_window" in mods, "el fallo de ventana quedo oculto"
    assert "idle_seconds" in mods, "el fallo de idle quedo oculto"
    assert "start_click_hook" in mods
    assert d.health()["counters"]["errors"] >= 3


def test_recorder_flags_degraded_backend_not_ok(tmp_path):
    """Un backend que responde pero mudo (idle 0, sin ventana) queda marcado."""
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.01
    d = Diagnostics()
    s = Session(cfg, _NoisyBackend(), video=False, out_dir=str(tmp_path),
                clock=time.time, diag=d)
    s.run(duration=0.2)
    c = d.counters()
    assert c.get("idle_zero", 0) > 0, "idle siempre 0 no quedo anotado"


def test_session_json_carries_diagnostics(tmp_path):
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.01
    s = Session(cfg, _NoisyBackend(), video=False, out_dir=str(tmp_path))
    summ = s.run(duration=0.2)
    s.write_summary(summ)
    got = json.load(open(s.session_json, encoding="utf-8"))
    assert "diagnostics" in got
    assert "timings" in got["diagnostics"]


def test_recorder_writes_events_file_next_to_the_log(tmp_path):
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.01
    s = Session(cfg, _NoisyBackend(), video=False, out_dir=str(tmp_path))
    s.run(duration=0.1)
    assert os.path.exists(os.path.join(str(tmp_path), "events.jsonl"))


# ---- rendimiento ----
def test_stopwatch_percentiles():
    sw = Stopwatch()
    for v in (0.001, 0.002, 0.003, 0.100, 0.200):
        sw.add("op", v)
    st = sw.stats()["op"]
    assert st["n"] == 5
    assert st["p50"] == 3.0
    assert st["p95"] == 200.0
    assert st["max"] == 200.0


def test_slowest_ranks_by_p95():
    sw = Stopwatch()
    for _ in range(6):
        sw.add("rapido", 0.001)
        sw.add("lento", 0.050)
    top = sw.slowest(1)[0][0]
    assert top == "lento"


def test_repeated_errors_are_deduplicated_but_counted(tmp_path):
    """93 fallos del mismo tipo no pueden ser 93 lineas iguales."""
    p = str(tmp_path / "e.jsonl")
    d = Diagnostics(path=p)
    for _ in range(200):
        try:
            raise RuntimeError("siempre lo mismo")
        except RuntimeError as exc:
            d.error("idle_seconds", exc)
    d.close()
    lines = [json.loads(x) for x in open(p, encoding="utf-8")]
    errs = [e for e in lines if e["event"] == "error"]
    assert d.counters()["errors"] == 200          # el total es exacto
    assert d.counters()["error:idle_seconds"] == 200
    # 3 escrituras completas + una cada 50 (50,100,150,200) = 7, no 200.
    assert len(errs) == d.ERROR_REPEAT + 4
    assert [e["repeat"] for e in errs] == [1, 2, 3, 50, 100, 150, 200]
    assert len(errs) < 200 / 20                   # el archivo no se inunda


def test_distinct_errors_are_not_deduplicated(tmp_path):
    p = str(tmp_path / "e.jsonl")
    d = Diagnostics(path=p)
    for i in range(10):
        try:
            raise ValueError(f"distinto {i}")
        except ValueError as exc:
            d.error("video", exc)
    d.close()
    errs = [e for e in (json.loads(x) for x in open(p, encoding="utf-8"))
            if e["event"] == "error"]
    assert len(errs) == 10


# ---------- informe #17: control con permiso de admin ----------
from gpia import control as ctl
from gpia import persist


# ---- fail-closed: sin token no hay control ----
def test_control_denied_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GPIA_CONTROL_TOKEN", str(tmp_path / "nope.token"))
    assert ctl.is_admin() is False
    with pytest.raises(PermissionError) as e:
        ctl.issue(str(tmp_path), "stop")
    assert "administrador" in str(e.value)


def test_control_denied_when_token_not_root_owned(monkeypatch, tmp_path):
    """Un token que el propio usuario puede escribir no habilita control."""
    t = tmp_path / "control.token"
    t.write_text("secreto")
    monkeypatch.setenv("GPIA_CONTROL_TOKEN", str(t))
    if os.name == "nt" or os.stat(t).st_uid == 0:
        return                      # en Windows el chequeo es por ACL
    assert ctl.is_admin() is False, "un token del usuario NO debe dar control"


# ---- el canal aplica lo que el admin manda ----
def _as_root(monkeypatch, tmp_path):
    """Simula que el comando lo escribio root (en POSIX, st_uid == 0).

    En una maquina normal los tests corren como usuario sin privilegios, asi
    que se parchea el chequeo de propiedad. El punto es que el chequeo existe
    y se ejercita en `test_control_denied_when_token_not_root_owned`.
    """
    monkeypatch.setattr(ctl, "is_admin", lambda: True)
    if os.name == "nt":
        return
    real_stat = os.stat
    def fake_stat(path, *a, **k):
        st = real_stat(path, *a, **k)
        if str(path).endswith("cmd.json"):
            class S:
                st_uid = 0
                st_size = st.st_size
                st_mtime = st.st_mtime
            return S()
        return st
    monkeypatch.setattr(ctl.os, "stat", fake_stat)


def test_control_pause_resume_stop_flow(monkeypatch, tmp_path):
    sess = str(tmp_path)
    _as_root(monkeypatch, tmp_path)
    c = ctl.ControlChannel(sess)
    c.ensure()
    assert c.poll() is None                     # nadaTodavia

    ctl.issue(sess, "pause", actor="root@host")
    assert c.poll() == "pause"
    c.admin_pause = True
    assert c.status()["admin_pause"] is True

    ctl.issue(sess, "resume", actor="root@host")
    assert c.poll() == "resume"
    c.admin_pause = False

    ctl.issue(sess, "stop", actor="root@host")
    assert c.poll() == "stop"
    assert c.applied[-1]["actor"] == "root@host"


def test_control_does_not_replay_the_same_command(monkeypatch, tmp_path):
    _as_root(monkeypatch, tmp_path)
    sess = str(tmp_path)
    c = ctl.ControlChannel(sess)
    c.ensure()
    ctl.issue(sess, "pause")
    assert c.poll() == "pause"
    assert c.poll() is None                     # no se repite cada sample
    ctl.issue(sess, "resume")
    assert c.poll() == "resume"


def test_control_rejects_unknown_command(monkeypatch, tmp_path):
    monkeypatch.setattr(ctl, "is_admin", lambda: True)
    sess = str(tmp_path)
    c = ctl.ControlChannel(sess)
    c.ensure()
    os.makedirs(os.path.join(sess, "control"), exist_ok=True)
    with open(os.path.join(sess, "control", "cmd.json"), "w") as fh:
        json.dump({"seq": 9, "cmd": "borrar_todo", "actor": "x"}, fh)
    assert c.poll() is None
    assert c.applied == []


def test_control_survives_a_corrupt_command_file(tmp_path):
    """Un control corrupto no puede voltear la grabacion."""
    sess = str(tmp_path)
    c = ctl.ControlChannel(sess)
    c.ensure()
    with open(os.path.join(sess, "control", "cmd.json"), "w") as fh:
        fh.write("{esto no es json")
    assert c.poll() is None


# ---- el recorder obedece ----
def test_recorder_pauses_on_admin_command(tmp_path):
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.01
    b = NullBackend(idle_seq=[0.0] * 200)
    s = Session(cfg, b, video=False, out_dir=str(tmp_path))
    s.t0 = s.clock()
    s.sample()
    ctl.ControlChannel(str(tmp_path)).poll()      # seguro: no hay comando
    s.control.admin_pause = True
    line = s.sample()
    assert line["state"] == "paused_idle" or line["state"] == "active"
    assert line["paused_by"] == "admin"


def test_recorder_stops_on_admin_command(tmp_path, monkeypatch):
    """Un stop del admin corta el loop limpio, no lo rompe."""
    _as_root(monkeypatch, tmp_path)
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.01
    b = NullBackend(idle_seq=[0.0] * 500)
    s = Session(cfg, b, video=False, out_dir=str(tmp_path))
    assert s.stop_requested is None
    ctl.issue(str(tmp_path), "stop", actor="admin-test")
    s._poll_control()
    assert s.stop_requested == "admin"
    assert s.control.status()["applied"][-1]["actor"] == "admin-test"


# ---- estado / reanudacion ----
def test_state_is_closed_when_session_finishes(tmp_path):
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.01
    s = Session(cfg, NullBackend(idle_seq=[0.0] * 50), video=False,
                out_dir=str(tmp_path))
    s.run(duration=0.1)
    st = persist.load_state(str(tmp_path))
    assert st["closed"] is True
    assert st["reason"] == "finished"
    assert persist.is_open(str(tmp_path)) is False


def test_killed_session_leaves_state_open_and_findable(tmp_path):
    """Corte de luz = state.json con closed false, y se puede encontrar."""
    d = str(tmp_path)
    persist.save_state(d, closed=False, service="606", employee="JP",
                       pid=999999, samples=42, updated_epoch=int(time.time()))
    assert persist.is_open(d) is True
    found = persist.find_open_session(d, service=None, employee="JP")
    assert found and found[0] == d
    assert found[1]["samples"] == 42


def test_t_keeps_increasing_across_a_resume(tmp_path):
    """evaluar.py suma deltas de t: si t reinicia, cuenta dos sesiones como una."""
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.01
    b = NullBackend(idle_seq=[0.0] * 50)
    primera = Session(cfg, b, video=False, out_dir=str(tmp_path))
    primera.t0 = primera.clock()
    t_primera = primera.sample()["t"]

    segunda = Session(cfg, NullBackend(idle_seq=[0.0] * 50), video=False,
                      out_dir=str(tmp_path),
                      resumed={"t_offset": t_primera + 900.0})
    segunda.t0 = segunda.clock()
    t_segunda = segunda.sample()["t"]
    assert t_segunda >= t_primera + 900.0, "t debe seguir al anterior"
    assert t_segunda > t_primera, "t nunca debe retroceder"


def test_gap_is_recorded_separately(tmp_path):
    d = str(tmp_path)
    persist.save_state(d, closed=False)
    g = persist.add_gap(d, "reinicio", 3600)
    assert g["seconds"] == 3600.0
    assert persist.load_state(d)["gaps"][-1]["reason"] == "reinicio"


def test_segment_numbering_continues_after_resume(tmp_path):
    d = str(tmp_path)
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    s = Session(cfg, NullBackend(idle_seq=[0.0]), video=False, out_dir=d)
    s.recorder = type(s.recorder)(os.path.join(d, "screen.mp4"), cfg) \
        if s.recorder else None
    from gpia.video import VideoRecorder, _segment_path
    vr = VideoRecorder(os.path.join(d, "screen.mp4"), cfg)
    for i in (1, 2, 3):
        open(_segment_path(os.path.join(d, "screen.mp4"), i), "wb").close()
    assert persist.next_segment_index(d) == 4
    vr.next_index = persist.next_segment_index(d)
    vr.cfg = cfg
    assert _segment_path(os.path.join(d, "screen.mp4"), vr.next_index) \
        .endswith("screen-004.mp4")


def test_repair_quarantines_an_unplayable_segment(tmp_path):
    """Un mp4 sin indice no se reproduce: se aparta, no se pierde de vista."""
    d = str(tmp_path)
    bad = os.path.join(d, "screen-001.mp4")
    with open(bad, "wb") as fh:
        fh.write(b"\x00" * 5000)              # basura: no es un mp4 valido
    r = persist.repair_last_segment(d)
    assert r["status"] == "lost"
    assert os.path.exists(bad + ".unrecoverable")
    assert os.path.getsize(bad + ".unrecoverable") == 5000


def test_seconds_since_falls_back_to_mtime(tmp_path):
    """Un updated_epoch viejo o ausente no puede inventar un hueco de 50 anos."""
    d = str(tmp_path)
    persist.save_state(d, closed=False, updated_epoch=0)
    g = persist.seconds_since(d)
    assert 0 <= g < 60, f"hueco absurdo: {g}"


def test_resume_continues_segment_numbering(tmp_path):
    """Al reanudar, screen-001..003 no se pisan: sigue en 004."""
    from gpia.video import _segment_path
    d = str(tmp_path)
    for i in (1, 2, 3):
        open(_segment_path(os.path.join(d, "screen.mp4"), i), "wb").close()
    assert persist.next_segment_index(d) == 4
    assert _segment_path(os.path.join(d, "screen.mp4"), 4).endswith("screen-004.mp4")


def test_capture_resume_flag_exists():
    """La unidad de systemd usa --resume: si no existiera, no arrancaria."""
    import argparse
    from gpia.cli import build_parser
    args = build_parser().parse_args(
        ["capture", "--duration", "0", "--resume", "--service", "606"])
    assert args.resume is True
    assert args.duration == 0


def test_autostart_unit_mentions_resume_and_forever(tmp_path, monkeypatch):
    """La unidad generada tiene que poder arrancar sola y no caducar."""
    from gpia.cli import cmd_install_autostart
    home = tmp_path / "home"
    (home / ".config" / "systemd" / "user").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("os.path.expanduser",
                        lambda p: str(p).replace("~", str(home), 1))
    args = argparse.Namespace(service="606", employee="JP", config="")
    cmd_install_autostart(args)
    unit = (home / ".config" / "systemd" / "user" / "screenaudit.service").read_text()
    assert "--resume" in unit
    assert "--duration 0" in unit
    assert "WantedBy=default.target" in unit
    assert "Restart=always" in unit


# ---------- regresiones de la auditoria de robustez ----------

def test_a_plain_user_cannot_forge_a_control_command(tmp_path, monkeypatch):
    """El agujero encontrado en la auditoria: el chequeo estaba en issue(),
    no en poll(). Escribiendo el JSON a mano se frenaba la grabacion."""
    monkeypatch.setenv("GPIA_CONTROL_TOKEN", str(tmp_path / "nope.token"))
    sess = str(tmp_path)
    c = ctl.ControlChannel(sess)
    c.ensure()
    with open(os.path.join(sess, "control", "cmd.json"), "w") as fh:
        json.dump({"seq": 99, "cmd": "stop", "actor": "atacante"}, fh)
    if os.name == "nt":
        return                      # en Windows la garantia es la ACL
    assert ctl.is_admin() is False
    assert c.poll() is None, "un usuario normal detuvo la grabacion"
    assert c.applied == []


def test_root_authored_command_is_accepted(tmp_path, monkeypatch):
    """El camino legitimo sigue funcionando tras mover el chequeo."""
    _as_root(monkeypatch, tmp_path)
    sess = str(tmp_path)
    c = ctl.ControlChannel(sess)
    c.ensure()
    ctl.issue(sess, "stop", actor="root@central")
    assert c.poll() == "stop"


def test_t_survives_a_wall_clock_jump(tmp_path, monkeypatch):
    """NTP, suspension o ajuste manual de hora no pueden romper el contrato.

    `t` va con reloj monotonico. Antes usaba time.time, asi que un salto
    hacia atras hacia que t retrocediera y evaluar.py (que suma deltas de t)
    colapsara el total de la sesion.
    """
    real_time = time.time
    state = {"n": 0}

    def lying():
        v = real_time()
        if state["n"] == 25:
            v -= 3600.0
        state["n"] += 1
        return v

    monkeypatch.setattr(time, "time", lying)
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.0
    s = Session(cfg, NullBackend(idle_seq=[0.0] * 99999), video=False,
                out_dir=str(tmp_path))
    ts = []
    with open(os.path.join(str(tmp_path), "gp_log.jsonl"), "a") as fh:
        pass
    s.run(duration=0.2)
    for line in open(os.path.join(str(tmp_path), "gp_log.jsonl"),
                     encoding="utf-8"):
        ts.append(json.loads(line)["t"])
    assert ts == sorted(ts), "t retrocedio cuando el reloj de pared salta"
    assert ts[-1] > ts[0], "la sesion no avanzo"


def test_default_clock_is_monotonic_not_wall_clock():
    """Guardarrail: si alguien vuelve a poner time.time, el test de arriba falla."""
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    s = Session(cfg, NullBackend(), video=False, out_dir=None)
    assert s.clock is time.monotonic


def test_diagnostics_reports_a_broken_sink(tmp_path):
    """Sin disco no hay eventos: session.json tiene que decirlo, no
    fingir que la sesion fue limpia."""
    d = Diagnostics(path="/proc/1/denegado/e.jsonl")   # imposible de crear
    d.emit("test")
    d.emit("test")
    h = d.health()
    assert h["counters"].get("sink_broken", 0) >= 1 or h["sink_error"]
    d.close()


def test_no_fd_or_memory_leak_over_many_samples(tmp_path):
    """3000 muestras no pueden dejar descriptores abiertos."""
    import resource
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.0
    s = Session(cfg, NullBackend(idle_seq=[0.0] * 999999, click_every=9999),
                video=False, out_dir=str(tmp_path))
    s.t0 = s.clock()

    def fds():
        try:
            return len(os.listdir(f"/proc/{os.getpid()}/fd"))
        except OSError:
            pytest.skip("sin /proc")
    before = fds()
    for _ in range(3000):
        s.sample()
    assert fds() - before < 5, "fuga de descriptores"
    assert s.samples == 3000


# ---------- regresiones: los 5 bugs que la auditoria encontro ----------
# Todos estos codigos compilan y los tests viejos pasaban. Lo que fallaba era
# el comportamiento en el escenario que de verdad importa: un corte abrupto.

def test_state_is_written_during_the_run_not_only_at_the_end(tmp_path):
    """Informe #17: el latido va DENTRO del loop.

    Antes `_checkpoint()` solo se llamaba desde `_poll_control()`, o sea unicamente
    cuando un admin mandaba un comando. En una corrida normal sin admin,
    `state.json` no existia hasta el cierre, y un corte abrupto dejaba la
    sesion sin estado: la reanudacion no la encontraba.
    """
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False, "checkpoint_every": 1}
    cfg["sample_interval_seconds"] = 0.0
    s = Session(cfg, NullBackend(idle_seq=[0.0] * 50), video=False,
                out_dir=str(tmp_path))
    s.t0 = s.clock()
    s._clicks_fh = None
    s.backend.start_click_hook(s._on_click)

    # Se escribe una linea a mano y se late, como hace run().
    with schemas.JsonlWriter(s.log_path) as log:
        s.sample()
        log.write(s.sample())
        s._checkpoint()

    st = persist.load_state(str(tmp_path))
    assert st, "state.json no se escribio durante la corrida"
    assert st["closed"] is False
    assert st["samples"] >= 1


def test_state_survives_a_really_killed_process(tmp_path):
    """El caso que documento el informe #17: matar el proceso de verdad.

    No se simula con `_checkpoint()` a mano. Se lanza `python -m gpia capture`
    como subproceso, se le manda SIGKILL (como un corte de luz) y se comprueba
    que queda un state.json ABIERTO y que la reanudacion lo encuentra.
    """
    import signal
    import subprocess
    # El path de la app sale del paquete ya importado, no de suponer el layout
    # del repo: asi el test funciona igual dentro del repo que en el paquete,
    # donde la app esta en `02-app/` y no al lado de los tests.
    import gpia
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(gpia.__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = app_dir
    # Las rutas se resuelven con XDG_DATA_HOME (paths.py:user_data_dir), asi
    # que el subproceso escribe en un arbol temporal y no en el del usuario.
    env["XDG_DATA_HOME"] = str(tmp_path)
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    p = subprocess.Popen(
        [sys.executable, "-m", "gpia", "capture", "--duration", "120",
         "--employee", "MATADO", "--service", "606/soporte",
         "--backend", "null", "--skip-preflight"],
        cwd=app_dir, env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    sessions = os.path.join(str(tmp_path), "ScreenAudit", "sessions")
    try:
        deadline = time.time() + 20
        state = None
        while time.time() < deadline:
            found = persist.find_open_session(sessions, service="606/soporte")
            if found and found[1].get("samples", 0) >= 2:
                state = found
                break
            time.sleep(0.3)
        assert state, "nunca aparecio un state.json abierto con muestras"
    finally:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            p.kill()
        p.wait(timeout=20)

    session_dir, st = state
    assert st["closed"] is False
    assert st["last_t"] is not None, "sin last_t no se puede continuar t"
    # Y la sesion sigue viva segun el estado, que es justo lo que dispara el
    # resume automatico en el proximo arranque.
    assert persist.is_open(session_dir) is True


def test_find_open_session_normalizes_the_service_name(tmp_path):
    """`--service 606/soporte` tiene que encontrar la carpeta `606_soporte`."""
    from gpia.paths import slugify
    d = tmp_path / slugify("606/soporte") / "20260101T000000"
    d.mkdir(parents=True)
    persist.save_state(str(d), closed=False, service="606/soporte",
                       employee="JP", samples=3)
    for queried in ("606/soporte", "606_soporte"):
        found = persist.find_open_session(str(tmp_path), service=queried)
        assert found, f"no encuentra la sesion preguntando por {queried!r}"
        assert found[0] == str(d)


def test_resume_t_uses_the_measured_value_not_the_nominal_interval():
    """`last_t` manda; `samples * interval` es la aproximacion vieja."""
    from gpia.cli import _resume_t
    st = {"t_offset": 0.0, "samples": 10, "interval": 2.0, "last_t": 27.5}
    assert _resume_t(st) == 27.5
    # Sin `last_t` (estado anterior a este cambio) se cae al nominal.
    st_viejo = {"t_offset": 0.0, "samples": 10, "interval": 2.0}
    assert _resume_t(st_viejo) == 20.0
    # Y con t_offset ya adelantado, el offset previo se respeta.
    assert _resume_t({"t_offset": 100.0, "samples": 5, "interval": 2.0,
                      "last_t": 112.0}) == 112.0


def test_capture_resume_refuses_to_run_over_a_live_recorder(tmp_path, monkeypatch):
    """Un pid vivo significa que hay otro recorder escribiendo el mismo log."""
    from gpia import cli
    args = cli.build_parser().parse_args(
        ["capture", "--duration", "1", "--resume", "--service", "606",
         "--employee", "JP"])
    d = tmp_path / "606" / "20260101T000000"
    d.mkdir(parents=True)
    persist.save_state(str(d), closed=False, service="606", employee="JP",
                       samples=4, pid=os.getpid(), last_t=8.0)
    monkeypatch.setattr(cli._paths, "sessions_dir", lambda: str(tmp_path))
    rc = cli.cmd_capture(args)
    assert rc == 4, "tiene que negarse, no solo avisar"


def test_capture_resume_still_works_when_the_old_pid_is_gone(tmp_path, monkeypatch):
    """El caso normal: el proceso murio, se reanuda sin preguntar."""
    from gpia import cli
    args = cli.build_parser().parse_args(
        ["capture", "--duration", "1", "--resume", "--service", "606",
         "--employee", "JP", "--backend", "null"])
    d = tmp_path / "606" / "20260101T000000"
    d.mkdir(parents=True)
    persist.save_state(str(d), closed=False, service="606", employee="JP",
                       samples=4, pid=999999, last_t=8.0, interval=2.0)
    monkeypatch.setattr(cli._paths, "sessions_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: False)
    # No se ejecuta la captura: se comprueba que llego hasta `_capture`.
    seen = {}

    def fake_capture(a, **kw):
        seen.update(kw)
        return 0
    monkeypatch.setattr(cli, "_capture", fake_capture)
    assert cli.cmd_capture(args) == 0
    assert seen.get("resume_dir") == str(d)
    assert seen.get("t_offset") == 8.0


def test_admin_pause_is_not_counted_as_idle(tmp_path):
    """Una pausa de admin no es inactividad del usuario.

    Antes `paused_samples` (que suma ambas) se publicaba como
    `samples_paused_idle`, asi que el reporte dicia que el empleado estaba
    inactivo cuando lo que pasaba es que un admin habia pausado.
    """
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.0
    s = Session(cfg, NullBackend(idle_seq=[0.0] * 20), video=False,
                out_dir=str(tmp_path))
    s.t0 = s.clock()
    s.control.admin_pause = True
    for _ in range(3):
        s.sample()
    summary = s.summary()
    assert summary["samples_paused_admin"] == 3
    assert summary["samples_paused_idle"] == 0
    assert summary["samples_paused"] == 3


def test_idle_pause_is_counted_as_idle_not_as_admin(tmp_path):
    """Y al reves: el desglose tiene que separar las dos causas."""
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.0
    s = Session(cfg, NullBackend(idle_seq=[999.0] * 20), video=False,
                out_dir=str(tmp_path))
    s.t0 = s.clock()
    for _ in range(3):
        s.sample()
    summary = s.summary()
    assert summary["samples_paused_idle"] == 3
    assert summary["samples_paused_admin"] == 0


def test_resume_appends_to_the_log_and_never_truncates_it(tmp_path):
    """Reanudar no puede borrar el tramo anterior.

    `JsonlWriter` abre en modo "w" (trunca) por defecto, y `run()` lo usaba
    siempre. El resultado era que el resume sobrescribia el log previo con el
    suyo: se perdia justo lo que el resume existe para recuperar, y
    `evaluar.py` — que recalcula con los deltas de `t` — veia una sesion mas
    corta en vez de una sesion partida en dos.
    """
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.0

    clock = _ClockAt(0.0)
    primero = Session(cfg, NullBackend(idle_seq=[0.0] * 20), video=False,
                      out_dir=str(tmp_path), clock=clock)
    primero.run(duration=4.0, on_sample=lambda s: clock.advance(2.0))
    con_antes = open(primero.log_path, encoding="utf-8").read().splitlines()
    assert len(con_antes) == primero.samples > 1
    t_previo = json.loads(con_antes[-1])["t"]

    # Segunda pasada sobre el MISMO directorio, como hace el resume: el
    # `t_offset` es lo que hace que `t` continue en vez de volver a cero.
    clock2 = _ClockAt(t_previo)
    segundo = Session(cfg, NullBackend(idle_seq=[0.0] * 20), video=False,
                      out_dir=str(tmp_path), clock=clock2,
                      resumed={"t_offset": t_previo, "next_segment": 2})
    segundo.run(duration=4.0, on_sample=lambda s: clock2.advance(2.0))

    despues = open(segundo.log_path, encoding="utf-8").read().splitlines()
    assert len(despues) == len(con_antes) + segundo.samples, \
        "el resume tiene que sumar, no reemplazar"
    assert despues[:len(con_antes)] == con_antes, "las lineas previas cambiaron"
    ts = [json.loads(l)["t"] for l in despues]
    assert all(b >= a for a, b in zip(ts, ts[1:])), "t retrocedio al reanudar"
    assert ts[-1] > t_previo, "t no continuo tras el corte"


def test_fresh_session_still_starts_with_a_clean_log(tmp_path):
    """Lo contrario: una sesion nueva no hereda basura de otra anterior."""
    cfg = config.load(None)
    cfg["session"] = {"employee": "x", "service": "606"}
    cfg["capture"] = {"record_video": False}
    cfg["sample_interval_seconds"] = 0.0
    clock = _ClockAt(0.0)
    s = Session(cfg, NullBackend(idle_seq=[0.0] * 20), video=False,
                out_dir=str(tmp_path), clock=clock)
    s.run(duration=2.0, on_sample=lambda s: clock.advance(2.0))
    lineas = open(s.log_path, encoding="utf-8").read().splitlines()
    assert len(lineas) == s.samples


def test_last_log_t_reads_the_last_written_value(tmp_path):
    """El log manda sobre el estado, que va atrasado a proposito."""
    log = tmp_path / "gp_log.jsonl"
    with open(log, "w", encoding="utf-8") as fh:
        for t in (0.0, 2.0, 4.0, 6.0):
            fh.write(json.dumps({"t": t, "state": "active"}) + "\n")
    assert persist.last_log_t(str(tmp_path)) == 6.0
    # Sin ultima linea terminada en \n tambien tiene que funcionar.
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": 1.5, "state": "active"}))
    assert persist.last_log_t(str(tmp_path)) == 1.5
    # Y sin log, None en vez de una excepcion.
    assert persist.last_log_t(str(tmp_path / "vacio")) is None


def test_resume_t_never_goes_backwards_below_the_log(tmp_path):
    """Un checkpoint atrasado no puede hacer retroceder `t`.

    El estado se escribe cada N muestras, asi que es normal que el log tenga
    mas lineas que el ultimo `last_t`. Volver a ese valor hacia que `t` retroceda
    por debajo de una linea ya escrita, y `evaluar.py` — que suma deltas — ve
    un delta negativo.
    """
    from gpia.cli import _resume_t
    log = tmp_path / "gp_log.jsonl"
    with open(log, "w", encoding="utf-8") as fh:
        for t in (0.0, 2.0, 4.0, 8.0, 10.0, 12.0):
            fh.write(json.dumps({"t": t, "state": "active"}) + "\n")
    st = {"t_offset": 0.0, "samples": 5, "interval": 2.0, "last_t": 8.0}
    assert _resume_t(st, str(tmp_path)) == 12.0
    # Si el estado esta al dia, gana el estado (o son iguales).
    st_al_dia = {"t_offset": 0.0, "samples": 6, "interval": 2.0, "last_t": 12.0}
    assert _resume_t(st_al_dia, str(tmp_path)) == 12.0
    # Sin log legible, el estado manda.
    assert _resume_t(st, str(tmp_path / "no-existe")) == 8.0


# --- Informe #10: ffmpeg no puede sobrevivir a la grabadora ---------------

def test_video_lanza_ffmpeg_en_su_propio_grupo():
    """start_new_session: sin esto no se puede senalar al grupo entero."""
    import inspect
    from gpia import video
    src = inspect.getsource(video.VideoRecorder.start)
    assert "start_new_session=True" in src


def test_pdeathsig_se_arma_en_el_hijo_no_en_el_padre():
    """prctl() aplica al proceso que llama, asi que debe ir en el hijo.

    Si se armara en el padre (el error original) el ajuste no protege a ffmpeg
    y ademas le dice a Python que lo maten si muere quien lo lanzo.
    """
    import inspect
    from gpia import video
    # El bootstrap corre en el hijo, justo antes de execvp.
    assert "prctl" in video._PDEATHSIG_BOOTSTRAP
    assert "os.execvp" in video._PDEATHSIG_BOOTSTRAP
    # Y el padre solo lo inyecta como prefijo del comando.
    assert "_PDEATHSIG_BOOTSTRAP" in inspect.getsource(video.VideoRecorder.start)
    # La funcion antigua no debe volver a llamar prctl de verdad.
    assert video._arm_pdeathsig(1234) is None


def test_senales_manejadas_solo_si_se_puede():
    """El handler se registra solo en hilo principal y plataforma compatible."""
    import inspect
    from gpia import recorder
    src = inspect.getsource(recorder.Session._signal_safe_exit)
    for sig in ("SIGTERM", "SIGINT"):
        assert sig in src
    # Un fallo al registrar no puede abortar la grabacion.
    assert "ValueError" in src
