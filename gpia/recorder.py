"""Recorder: the loop. Owns informe #3,4,5,6,7,8,15.

It is backend-agnostic on purpose. The identical code path runs against
X11, Win32 and the Null backend, so the logic you prove on Linux is the
logic that ships on Windows.
"""
import json
import os
import signal
import threading
import time
from contextlib import contextmanager

from . import config as _config
from . import paths as _paths
from . import schemas
from .idle import IdleGate, ACTIVE, PAUSED
from .policy import Policy
from . import control as control_mod
from . import persist
from .diagnostics import Diagnostics
from .video import VideoRecorder

FILTERED = "filtered"


class Session:
    def __init__(self, cfg, backend, video=True, out_dir=None,
                 platform=None, display=None, clock=None, diag=None, resumed=None):
        self.cfg = cfg
        self.backend = backend
        self.policy = Policy.from_config(cfg)
        gate = cfg.get("idle", {})
        self.gate = IdleGate(gate.get("pause_after_seconds", 180),
                             gate.get("resume_after_seconds", 5))
        sess = cfg.get("session", {})
        self.employee = sess.get("employee", "")
        self.service = sess.get("service", "")
        self.interval = float(cfg.get("sample_interval_seconds", 2.0))
        self.video_enabled = bool(video) and bool(
            cfg.get("capture", {}).get("record_video", True))
        # Informe #15: the service gets a real folder, not just a field in the
        # log. Two services in one shift never share a directory.
        self.out_dir = out_dir or _paths.ensure(
            os.path.join(_paths.service_dir(self.service), _stamp()))
        self.log_path = os.path.join(
            self.out_dir, self.cfg.get("output", {}).get("log_name", "gp_log.jsonl"))
        self.clicks_path = os.path.join(self.out_dir, "clicks.jsonl")
        self.session_json = os.path.join(self.out_dir, "session.json")
        self.video_base = os.path.join(self.out_dir, "screen.mp4")
        # Informe #16: `t` se mide con un reloj monotonico, NO con el reloj
        # de pared. Si NTP corrige la hora, el equipo sale de suspension o el
        # reloj se ajusta a mano, el reloj de pared salta para atras: `t`
        # retrocede y `evaluar.py` (que suma deltas de t) colapsa el total y
        # puede acumular tiempos negativos por app. El instante real queda
        # en el campo `ts` de cada linea, que si usa hora de pared.
        self.clock = clock or time.monotonic
        self.recorder = VideoRecorder(self.video_base, cfg, platform, display) \
            if self.video_enabled else None

        self.t0 = None
        self.samples = 0
        self.paused_samples = 0
        self.filtered_samples = 0
        self.clicks = 0
        self._click_lock = threading.Lock()
        self._clicks_fh = None
        self._start_ts = None
        self._peak_idle = 0.0
        self._clicks_ok = None
        self._clicks_error = None
        self._seg_started = None
        self._deferred_stops = 0
        self.admin_paused_samples = 0
        self.idle_paused_samples = 0
        self.stop_requested = None
        self.resumed = dict(resumed or {})
        self.t_offset = float(self.resumed.get("t_offset", 0.0))
        self.min_segment_seconds = float(
            cfg.get("capture", {}).get("min_segment_seconds", 5.0))
        # Informe #16: sumidero de errores y tiempos. events.jsonl queda al
        # lado de gp_log.jsonl, que no se toca por contrato.
        self.diag = diag or Diagnostics(
            path=os.path.join(self.out_dir, "events.jsonl"))
        # Informe #17: canal de control + checkpoint para poder reanudar.
        self.control = control_mod.ControlChannel(self.out_dir, self.diag)
        self.control.ensure()
        self._ckpts = 0
        self.checkpoint_every = int(cfg.get("capture", {})
                                    .get("checkpoint_every", 5))

        self.diag.emit("session_start", service=self.service,
                       employee=self.employee, backend=getattr(
                           self.backend, "name", "?"),
                       pid=os.getpid(), interval=self.interval)

    # ---------- helpers ----------
    def _on_click(self, x, y, button):
        """Informe #7 + #15: every click, timestamped, in its own file."""
        with self._click_lock:
            self.clicks += 1
            if self._clicks_fh is None:
                return
            try:
                self._clicks_fh.write(json.dumps({
                    "ts": schemas.now_iso(),
                    "t": round(self.clock() - self.t0, 2) if self.t0 else 0.0,
                    "x": x, "y": y, "button": button,
                    "service": self.service, "employee": self.employee,
                }, ensure_ascii=False) + "\n")
                self._clicks_fh.flush()
            except Exception as exc:
                # Antes esto se comia el error y el click se perdia en
                # silencio. Ahora queda en events.jsonl.
                self.diag.error("click_write", exc, x=x, y=y)

    def _sync_video(self, want):
        """Start/stop ffmpeg to follow the idle state.

        A segment is never closed before capture.min_segment_seconds. Closing
        one early produces a 0-byte file, because the grabber needs a couple
        of seconds to deliver its first frame. So a short idle does not stop
        the recorder; the next sample re-evaluates and closes it once the
        segment is old enough. The footage is never silently lost.
        """
        if not self.recorder:
            return
        if want and not self.recorder.is_running():
            self.recorder.start()
            self._seg_started = self.clock()
            return
        if not want and self.recorder.is_running():
            age = self.clock() - (self._seg_started or self.clock())
            if age < self.min_segment_seconds:
                self._deferred_stops += 1
                return
            self.recorder.stop()

    def sample(self):
        """Produce one log line. Exposed so tests can drive it without threads.

        Guardarraíl: cada lectura del backend se mide y todo fallo se reporta
        a diagnostics en vez de convertirse en un 0 silencioso. El loop nunca
        se cae por un backend roto, pero tampoco lo esconde.
        """
        win = None
        t0 = time.perf_counter()
        try:
            win = self.backend.foreground_window()
        except Exception as exc:
            self.diag.error("foreground_window", exc, sample=self.samples)
        else:
            if win is None:
                self.diag.bump("foreground_none")
        self.diag.sw.add("foreground_window", time.perf_counter() - t0)

        t0 = time.perf_counter()
        idle_s = 0.0
        try:
            idle_s = self.backend.idle_seconds()
        except Exception as exc:
            self.diag.error("idle_seconds", exc, sample=self.samples)
        else:
            if float(idle_s) == 0.0:
                self.diag.bump("idle_zero")
        self.diag.sw.add("idle_seconds", time.perf_counter() - t0)

        self._peak_idle = max(self._peak_idle, float(idle_s))

        state = self.gate.update(idle_s)
        allowed = self.policy.allows(win.exe if win else "",
                                     win.title if win else "")
        if not allowed:
            state = FILTERED
        # Informe #17: una pausa por idle y una pausa por administrador se
        # distinguen. El gate no se toca, para que un resume del admin no
        # desincronice la histeresis.
        paused_by = None
        if state == PAUSED:
            paused_by = "idle"
        if self.control and self.control.admin_pause:
            if state == ACTIVE:
                state = PAUSED
            paused_by = "admin"
        elif self.control and self.control.admin_pause is False and paused_by == "admin":
            paused_by = "idle"

        s = schemas.make_sample(
            elapsed=(self.clock() - self.t0) + self.t_offset,
            window={"hwnd": win.hwnd, "pid": win.pid, "title": win.title,
                    "exe": win.exe, "app": win.app} if win else {},
            idle_s=idle_s, clicks=self.clicks,
            service=self.service, employee=self.employee, state=state,
            paused_by=paused_by)
        self.samples += 1
        if state == PAUSED:
            self.paused_samples += 1
            # `paused_samples` es el total; el desglose va aparte. Antes el
            # total se publicaba como `samples_paused_idle`, asi que una pausa
            # de admin aparecia como inactividad del usuario.
            if paused_by == "admin":
                self.admin_paused_samples += 1
            else:
                self.idle_paused_samples += 1
        if state == FILTERED:
            self.filtered_samples += 1
        self._sync_video(state == ACTIVE)
        self.diag.sw.add("sample_total", time.perf_counter() - t0)
        return s

    def _poll_control(self):
        """Informe #17: aplica pause/resume/stop de un admin, si hay."""
        if self.control is None:
            return
        cmd = self.control.poll()
        if cmd is None:
            return
        if cmd == control_mod.PAUSE:
            self.control.admin_pause = True
        elif cmd == control_mod.RESUME:
            self.control.admin_pause = False
        elif cmd == control_mod.STOP:
            self.stop_requested = "admin"
            self.diag.emit("stop_requested", by="admin",
                           samples=self.samples,
                           segment=(self.recorder.current if self.recorder else None))
        self._checkpoint()

    def _last_t(self):
        """`t` exacto de la ultima muestra, para reanudar sin estimar."""
        if self.t0 is None:
            return round(self.t_offset, 2)
        return round((self.clock() - self.t0) + self.t_offset, 2)

    def _checkpoint(self):
        """Estado en disco. Es lo que permite retomar tras un corte."""
        if not self.control:
            return
        self._ckpts += 1
        every = max(1, self.checkpoint_every)
        # El primero se escribe siempre: si el proceso muere en los primeros
        # segundos, tiene que quedar un state.json abierto, no un directorio
        # huerfano que la reanudacion no pueda encontrar. Despues, cada `every`.
        if self._ckpts > 1 and self._ckpts % every and self.stop_requested is None:
            return
        # `last_t` es el valor exacto de `t` de la ultima muestra escrita. Al
        # reanudar se retoma desde aqui y no de `samples * interval`: con
        # on_sample lento, o con el intervalo real del reloj, esa cuenta se
        # desvía del tiempo que efectivamente se grabo.
        last_t = self._last_t()
        persist.save_state(
            self.out_dir, closed=False,
            service=self.service, employee=self.employee,
            backend=getattr(self.backend, "name", "?"),
            pid=os.getpid(), t_offset=self.t_offset, last_t=last_t,
            samples=self.samples, segments=self.recorder.segments if self.recorder else [],
            next_segment=persist.next_segment_index(self.out_dir),
            admin_pause=bool(self.control.admin_pause),
            interval=self.interval,
            started=self._start_ts)

    def _finalize_video(self):
        """Stop ffmpeg at session end, giving a young segment time to fill.

        Same reasoning as the min_segment floor, but at shutdown the wait is
        bounded and paid once: without it the last segment of every session
        flushes as a 48-byte header with no frames in it.
        """
        if not self.recorder or not self.recorder.is_running():
            return
        age = self.clock() - (self._seg_started or self.clock())
        remaining = self.min_segment_seconds - age
        if remaining > 0:
            time.sleep(min(remaining, self.min_segment_seconds))
        self.recorder.stop()

    # ---------- run ----------
    @contextmanager
    def _signal_safe_exit(self):
        """Make SIGTERM/SIGINT unwind through the normal shutdown path.

        Informe #10. Python's default SIGTERM action is to die on the spot:
        no exception, no `finally`, no `atexit`. So `systemctl stop` and a
        session logout killed the recorder while ffmpeg kept going and kept
        writing the mp4 (measured: 7 orphans, load 43, disk at 97%).

        Installing a handler converts both signals into a cooperative stop, so
        the loop breaks, `_finalize_video()` runs and `state.json` gets closed.
        SIGKILL cannot be caught, which is why video.py also sets PDEATHSIG.
        """
        received = []
        previous = {}

        def _handler(signum, _frame):
            received.append(signum)
            self.stop_requested = True

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                previous[sig] = signal.signal(sig, _handler)
            except (ValueError, OSError, AttributeError):
                # Not the main thread, or unsupported platform. video.py's
                # PDEATHSIG still applies, so this degrades rather than breaks.
                pass
        try:
            yield received
        finally:
            for sig, old in previous.items():
                try:
                    signal.signal(sig, old)
                except (ValueError, OSError):
                    pass

    def run(self, duration, on_sample=None):
        with self._signal_safe_exit():
            return self._run(duration, on_sample)

    def _run(self, duration, on_sample=None):
        """Record for `duration` seconds. Returns the summary dict."""
        self.t0 = self.clock()
        self._start_ts = schemas.session_start_iso()
        _paths.ensure(self.out_dir)
        # Al reanudar hay que ABRIR EL LOG, no reescribirlo. `JsonlWriter`
        # trunca por defecto, y truncar aqui borraba el tramo anterior: el
        # resume se comia justo los datos que existia para recuperar. Perder
        # esas lineas ademas rompe a `evaluar.py`, que recalcula los totales
        # con los deltas de `t`.
        with schemas.JsonlWriter(self.log_path, append=bool(self.resumed)) as log:
            self._clicks_fh = open(self.clicks_path, "a", encoding="utf-8")
            try:
                self._clicks_ok = bool(self.backend.start_click_hook(self._on_click))
            except Exception as exc:
                self._clicks_ok = False
                self._clicks_error = f"{type(exc).__name__}: {exc}"
                self.diag.error("start_click_hook", exc)
            if not self._clicks_ok:
                self._clicks_error = (getattr(self.backend, "_click_error", None)
                                      or self._clicks_error
                                      or "backend no pudo capturar clicks")
                # No es critico: el log de ventanas y el video siguen valiendo.
                self.diag.emit("warning", module="clicks",
                               detail=self._clicks_error, fatal=False)
            try:
                while True:
                    now = self.clock()
                    elapsed = (now - self.t0) + self.t_offset
                    if duration and elapsed >= self.t_offset + duration:
                        break
                    self._poll_control()
                    if self.stop_requested:
                        break
                    s = self.sample()
                    problems = schemas.check_legacy_compat(s)
                    if problems:  # never emit a line the engine cannot read
                        raise RuntimeError(
                            f"contrato roto, campos legacy faltantes: {problems}")
                    log.write(s)
                    if on_sample:
                        on_sample(s)
                    # Latido. Sin esto `state.json` solo se escribiria al
                    # cerrar, y un corte abrupto (kill, energia, OOM) dejaria
                    # la sesion sin estado: la reanudacion no la encontraria
                    # y el trabajo de esos segundos quedaria huerfano.
                    self._checkpoint()
                    s_t0 = time.perf_counter()
                    time.sleep(self.interval)
                    self.diag.sw.add("sleep", time.perf_counter() - s_t0)
            finally:
                try:
                    self.backend.stop_click_hook()
                except Exception as exc:
                    self.diag.error("stop_click_hook", exc)
                if self._clicks_fh:
                    self._clicks_fh.close()
                    self._clicks_fh = None
                self._finalize_video()
        self._close_state()
        return self.summary()

    def _close_state(self):
        """Marca la sesion como terminada.

        Sin esto, `state.json` queda con `closed: false` y un corte de luz
        seria indistinguible de una salida ordenada. La distincion es la
        que permite saber despues si reanudar o no.
        """
        if not self.control:
            return
        reason = "admin_stop" if self.stop_requested else "finished"
        if self.resumed:
            reason = "resumed_then_" + reason
        persist.close_state(self.out_dir, reason=reason, samples=self.samples,
                            t_offset=self.t_offset, last_t=self._last_t(),
                            segments=self.recorder.segments if self.recorder else [],
                            paused_samples=self.paused_samples,
                            idle_paused_samples=self.idle_paused_samples,
                            admin_paused_samples=self.admin_paused_samples)
        self.diag.emit("session_end", reason=reason, samples=self.samples,
                       admin_paused=self.admin_paused_samples)

    def empty_segments(self):
        """Segments that finalised as 0 bytes. Reported, never hidden."""
        if not self.recorder:
            return []
        out = []
        for p in self.recorder.segments:
            try:
                if os.path.getsize(p) == 0:
                    out.append(p)
            except OSError:
                out.append(p)
        return out

    def summary(self):
        active = max(0, self.samples - self.paused_samples - self.filtered_samples)
        empty = self.empty_segments()
        return {
            "started_at": self._start_ts,
            "ended_at": schemas.now_iso(),
            "backend": self.backend.name,
            "employee": self.employee,
            "service": self.service,
            "log": self.log_path,
            "clicks_file": self.clicks_path,
            "service_dir": self.out_dir,
            "video": self.recorder.segments[0] if self.recorder and
            self.recorder.segments else None,
            "videos": list(self.recorder.segments) if self.recorder else [],
            "video_segments": len(self.recorder.segments) if self.recorder else 0,
            "video_bytes": self.recorder.size() if self.recorder else 0,
            "audio_streams": self.recorder.audio_streams() if self.recorder
            else None,
            "empty_segments": empty,
            "min_segment_seconds": self.min_segment_seconds,
            "deferred_stops": self._deferred_stops,
            "samples": self.samples,
            "samples_active": active,
            "samples_paused": self.paused_samples,
            "samples_paused_idle": self.idle_paused_samples,
            "samples_paused_admin": self.admin_paused_samples,
            "stopped_by": self.stop_requested,
            "control": self.control.status() if self.control else None,
            "resumed": self.resumed or None,
            "samples_filtered": self.filtered_samples,
            "clicks": self.clicks,
            "clicks_available": self._clicks_ok,
            "clicks_error": self._clicks_error,
            "peak_idle_seconds": round(self._peak_idle, 2),
            "pause_after_seconds": self.gate.pause_after,
            "policy_mode": self.policy.mode,
            # Informe #16: que fallo, cuanto tardo, que va lento.
            "diagnostics": self.diag.health(),
        }

    def write_summary(self, summary=None):
        s = summary or self.summary()
        with open(self.session_json, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2, ensure_ascii=False)
        return self.session_json


def _stamp():
    return schemas.now_iso().replace(":", "").replace("-", "")[:15]
