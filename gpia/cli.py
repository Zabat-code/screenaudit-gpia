"""Single entry point. `python -m gpia <command>`.

  init      escribe config por defecto en el perfil del usuario
  doctor    muestra backend, rutas, ffmpeg y su estado
  capture   graba N segundos y escribe gp_log.jsonl
  selftest  ejercita el loop con backend sintetico (sin display, sin ffmpeg)
"""
import argparse
import os
import json
import sys
import time

from . import config as _config
from . import paths as _paths
from . import schemas, video
from .backends import factory, BackendUnavailable
from .recorder import Session


def cmd_control(args):
    """Informe #17: pausar, reanudar o detener la grabacion. Solo admin."""
    from . import control as ctl
    session = args.session
    if not session:
        session = ctl.latest_session(_paths.sessions_dir(), service=args.service)
    if not session:
        print("no encontre ninguna sesion para controlar", file=sys.stderr)
        print("  --session /ruta/a/la/sesion", file=sys.stderr)
        return 2
    if args.action == "status":
        try:
            with open(os.path.join(session, "session.json"), encoding="utf-8") as fh:
                s = json.load(fh)
        except (OSError, ValueError):
            s = {}
        st = ctl.ControlChannel(session)
        ap = os.path.join(session, "control", "applied.json")
        print(f"sesion      : {session}")
        print(f"muestras    : {s.get('samples')}")
        print(f"pausa idle  : {s.get('samples_paused_idle')}")
        print(f"pausa admin : {s.get('samples_paused_admin')}")
        print(f"video       : {s.get('video_segments')} segmentos, "
              f"{s.get('video_bytes')} bytes")
        print(f"ultimo cmd  : " + (json.dumps(st._last_seq) and "" or ""))
        try:
            with open(ap, encoding="utf-8") as fh:
                print(f"aplicado    : {json.load(fh)}")
        except (OSError, ValueError):
            print("aplicado    : (ninguno)")
        return 0
    try:
        doc = ctl.issue(session, args.action, actor=args.actor)
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if doc.get("dry_run"):
        print(f"[simulado] {args.action} -> {doc['dir']}")
        return 0
    print(f"{args.action} enviado a {session}")
    print(f"  por {doc['actor']} seq={doc['seq']} a las {doc['at']}")
    print("  el recorder lo aplica en su proximo sample")
    return 0


def cmd_resume(args):
    """Informe #17: retomar la sesion que quedo abierta tras un corte."""
    from . import persist
    from .diagnostics import Diagnostics
    found = persist.find_open_session(
        _paths.sessions_dir(), service=args.service, employee=args.employee)
    if not found:
        print("no hay ninguna sesion abierta que retomar", file=sys.stderr)
        return 2
    session_dir, st = found
    print(f"sesion abierta: {session_dir}")
    print(f"  employee={st.get('employee')} service={st.get('service')} "
          f"muestras={st.get('samples')} pid_viejo={st.get('pid')}")
    if st.get("pid"):
        alive = _pid_alive(st["pid"])
        print(f"  proceso {st['pid']}: "
              f"{'sigue vivo, no se reanuda' if alive else 'muerto'}")
        if alive and not args.force:
            print("\n  ya hay un recorder corriendo en esa sesion", file=sys.stderr)
            return 4
    gap = int(time.time()) - int(st.get("updated_epoch") or 0)
    print(f"  interrupcion: ~{float(gap):.1f}s")
    gap = persist.seconds_since(session_dir, st)
    rep = persist.repair_last_segment(session_dir, diag=Diagnostics())
    if rep["status"] == "lost":
        print(f"  ultimo segmento NO recuperable "
              f"({rep.get('bytes')} bytes): {rep.get('file')}.unrecoverable")
    elif rep["status"] == "remuxed":
        print(f"  ultimo segmento re-muxeado y salvado: {rep['file']}")
    else:
        print(f"  ultimo segmento: {rep.get('status')}")
    return _capture(args, resume_dir=session_dir,
                    t_offset=_resume_t(st, session_dir), gap=gap)


def _pid_alive(pid):
    if os.name == "nt":
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def cmd_install_autostart(args):
    """Informe #17: arrancar solo al iniciar sesion, en Linux."""
    from . import paths as _p
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    unit = os.path.join(unit_dir, "screenaudit.service")
    py = os.path.abspath(sys.executable)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    svc, emp = args.service or "${SERVICE}", args.employee or "${EMPLOYEE}"
    body = f"""[Unit]
Description=ScreenAudit (GPIA) - captura de pantalla
After=graphical-session.target

[Service]
Type=simple
Environment=DISPLAY=:0
Environment=GPIA_BACKEND=auto
ExecStart={py} -m gpia --config {args.config or _config.config_path()} \\
    capture --duration 0 --resume --service {svc} --employee {emp}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""
    os.makedirs(unit_dir, exist_ok=True)
    with open(unit, "w", encoding="utf-8") as fh:
        fh.write(body)
    print(f"escrito: {unit}")
    print()
    print("para activarlo (como tu usuario, no root):")
    print(f"  systemctl --user daemon-reload")
    print(f"  systemctl --user enable --now screenaudit.service")
    print(f"  loginctl enable-linger {os.environ.get('USER', 'TU_USUARIO')}")
    print()
    print("  --duration 0 = grabar hasta que se le pida parar")
    print("  --resume     = retomar la sesion abierta si el equipo se reinicio")
    print()
    print("en Windows esto es una tarea programada que corra en el inicio de")
    print("sesion; la unidad de arriba es solo Linux.")
    return 0


def cmd_init(args):
    p = _paths.ensure(_paths.user_config_dir())
    cfg_path = _config.config_path(args.config)
    # Test existence, not truthiness. load() falls back to DEFAULTS, which is
    # always truthy, so a missing file used to report "config ya existe" and
    # never write anything.
    exists = os.path.exists(cfg_path)
    if exists and not args.force:
        print(f"config ya existe: {cfg_path}  (--force para sobreescribir)")
    else:
        _config.write_default(args.config)
        print(f"{'config sobreescrito' if exists else 'config escrito'}: {cfg_path}")
    _paths.ensure(_paths.sessions_dir())
    print(f"sesiones: {_paths.sessions_dir()}")
    print(f"perfil:   {p}")
    return 0


def cmd_doctor(args):
    """Informe #16: corre la cadena de modulos y dice que salio bien y que no.

    No es un `print` de versiones: cada modulo se prueba de verdad (el
    encoder codifica 3 frames, el backend se construye, las rutas se
    escriben). Devuelve codigo de salida != 0 si algo critico fallo, para
    poder engancharlo a un servicio o a un cron sin parsear texto.
    """
    from . import probes
    from .diagnostics import Diagnostics
    diag = Diagnostics(path=os.path.join(_paths.sessions_dir(), "doctor.jsonl"))
    report, cfg, backend = probes.preflight(
        args.config, backend_name=args.backend or "auto")
    for r in report.results:
        diag.record(r, chain="preflight")
    print(report.render())
    if not report.ok:
        print("\ncritico roto: la sesion no debe arrancar", file=sys.stderr)
    else:
        print("\ncriticos OK. Avisos arriba son limites del entorno, no fallos.")
    diag.emit("doctor_done", ok=report.ok, counts=report.counts())
    diag.close()
    return 0 if report.ok else 1


def cmd_health(args):
    """Lee events.jsonl de una sesion y resume que fallo y que fue lento."""
    from .diagnostics import Diagnostics
    d = args.dir or os.path.join(_paths.sessions_dir(), "latest")
    ev = os.path.join(d, "events.jsonl")
    sj = os.path.join(d, "session.json")
    if not os.path.exists(ev) and not os.path.exists(sj):
        print(f"no hay eventos en {d}", file=sys.stderr)
        return 2
    if os.path.exists(sj):
        with open(sj, encoding="utf-8") as fh:
            s = json.load(fh)
        h = s.get("diagnostics", {})
        c = h.get("counters", {})
        print("=" * 62)
        print(f"salud de {d}")
        print("=" * 62)
        print(f"muestras        : {s.get('samples')} "
              f"(pausa={s.get('samples_paused_idle')} "
              f"filtrado={s.get('samples_filtered')})")
        print(f"video           : {s.get('video_segments')} segmentos, "
              f"{s.get('video_bytes')} bytes, vacios={len(s.get('empty_segments') or [])}")
        print(f"audio_streams   : {s.get('audio_streams')}")
        print(f"clicks          : {s.get('clicks')} "
              f"(disponible={s.get('clicks_available')})")
        errs = h.get("errors") or []
        print(f"errores         : {len(errs)}")
        for e in errs[:6]:
            print(f"   - {e.get('module', '?')}: "
                  f"{e.get('detail') or e.get('message')}")
        if errs[6:]:
            print(f"   ... y {len(errs) - 6} mas")
        print("-" * 62)
        print("tiempos (ms)")
        t = h.get("timings") or {}
        for k, v in sorted(t.items(), key=lambda kv: -kv[1]["p95"]):
            print(f"   {k:<20} n={v['n']:<5} p50={v['p50']:<8} "
                  f"p95={v['p95']:<8} max={v['max']}")
        print("-" * 62)
        print("contadores      : " + (", ".join(f"{k}={v}"
                                                for k, v in sorted(c.items()))
                                     or "-"))
    if os.path.exists(ev):
        print(f"\neventos crudos: {ev}  "
              f"({sum(1 for _ in open(ev, encoding='utf-8'))} lineas)")
    return 0


def cmd_bench(args):
    """Informe #16: rendimiento. Como se comporta el backend, no la teoria."""
    from . import probes
    from .diagnostics import Stopwatch
    from .backends import factory
    b = factory.create(args.backend or "auto")
    sw = Stopwatch()
    n = int(args.samples)
    print("=" * 62)
    print(f"bench {b.name} - {n} llamadas por operacion")
    print("=" * 62)
    for name, fn in (("foreground_window", b.foreground_window),
                     ("idle_seconds", b.idle_seconds)):
        fails = 0
        for _ in range(n):
            t0 = time.perf_counter()
            try:
                fn()
            except Exception as exc:
                fails += 1
                sw.add(name + ".error", time.perf_counter() - t0)
                continue
            sw.add(name, time.perf_counter() - t0)
        if fails:
            print(f"  {name}: {fails}/{n} llamadas fallaron (ver events.jsonl)")
    st = sw.stats()
    for k, v in sorted(st.items(), key=lambda kv: -kv[1]["p95"]):
        tag = "" if ".error" not in k else "  <- fallos"
        print(f"  {k:<22} n={v['n']:<5} p50={v['p50']:<8} p95={v['p95']:<8} "
              f"max={v['max']:<8} por llamada={v['mean'] / v['n']:.2f}ms{tag}")
    print("-" * 62)
    worst = st.get("foreground_window", {}).get("max", 0.0)
    worst_idle = st.get("idle_seconds", {}).get("max", 0.0)
    print(f"presupuesto por sample: ventana {worst:.1f}ms + idle {worst_idle:.1f}ms"
          f" = {worst + worst_idle:.1f}ms")
    print("  si eso supera el intervalo de muestreo, las muestras se atrasan")
    return 0


def cmd_selftest(args):
    """Prove the loop end-to-end with no display, no ffmpeg, deterministic."""
    from .backends.null import NullBackend
    from .recorder import _stamp
    cfg = _config.load(args.config)
    if args.idle_pause is not None:
        cfg["idle"]["pause_after_seconds"] = float(args.idle_pause)
    if args.idle_resume is not None:
        cfg["idle"]["resume_after_seconds"] = float(args.idle_resume)
    cfg["session"]["employee"] = args.employee
    cfg["session"]["service"] = args.service
    cfg["policy"] = {"mode": args.policy, "include": [], "exclude": []}
    backend = NullBackend(idle_seq=[0.0, 0.0, args.big_idle, 0.0, 0.0],
                          click_every=0.03)
    out = _paths.ensure(os.path.join(_paths.sessions_dir(), f"selftest_{_stamp()}"))
    clock = _FakeClock()
    s = Session(cfg, backend, video=False, out_dir=out, clock=clock)
    s.t0 = clock()
    s._start_ts = schemas.session_start_iso()
    s._clicks_fh = open(s.clicks_path, "a", encoding="utf-8")
    s._clicks_ok = bool(backend.start_click_hook(s._on_click))
    print(f"sesion -> {s.log_path}")
    with schemas.JsonlWriter(s.log_path) as log:
        for _ in range(args.samples):
            line = s.sample()
            log.write(line)
            print(f"  t={line['t']:6.2f} {line['state']:12s} "
                  f"idle={line['idle_s']:6.1f} clicks={line['clicks']} "
                  f"{line['exe'] or '-':12s} {line['title'][:32]}")
            clock.advance(2.0)
            time.sleep(0.05)
    s._clicks_fh.close()
    summary = s.summary()
    print("-" * 62)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("OK: loop, contrato legacy, idle, policy y clicks verificados")
    print(f"log:     {s.log_path}")
    print(f"clicks:  {s.clicks_path}")
    return 0


class _FakeClock:
    def __init__(self):
        self._t = 1000.0

    def __call__(self):
        return self._t

    def advance(self, dt):
        self._t += dt


def _resume_t(st, session_dir=None):
    """Desde que `t` hay que seguir, leyendo el estado del checkpoint.

    Se usa el MAXIMO entre lo que dice el log y lo que dice el estado. El
    estado va atrasado a proposito (se escribe cada N muestras), asi que volver
    a `last_t` sin mirar el log hace retroceder `t` por debajo de la ultima
    linea ya escrita. Como `evaluar.py` suma deltas de `t`, eso descuadra el
    total de la sesion. El fallback a `samples * interval` es solo para
    estados anteriores a que existiera `last_t`.
    """
    candidatos = []
    if st.get("last_t") is not None:
        candidatos.append(float(st["last_t"]))
    if session_dir:
        from . import persist as _p
        en_log = _p.last_log_t(session_dir, st.get("log_name") or "gp_log.jsonl")
        if en_log is not None:
            candidatos.append(en_log)
    if candidatos:
        return max(candidatos)
    return float(st.get("t_offset", 0.0)) + \
        float(st.get("samples", 0)) * float(st.get("interval") or 0.0)


def cmd_capture(args):
    if getattr(args, "resume", False):
        # Mismo camino que `gpia resume`: encuentra la sesion abierta, repara
        # el ultimo segmento y sigue con la misma numeracion y el mismo `t`.
        from . import persist
        found = persist.find_open_session(
            _paths.sessions_dir(), service=getattr(args, "service", None),
            employee=getattr(args, "employee", None))
        if not found:
            print("no hay sesion abierta; empiezo una nueva", file=sys.stderr)
            args.resume = False
        else:
            session_dir, st = found
            gap = int(persist.seconds_since(session_dir, st))
            print(f"reanudando {session_dir} "
                  f"(muestras previas={st.get('samples')}, hueco ~{float(gap):.1f}s)")
            if st.get("pid") and _pid_alive(st["pid"]):
                if not args.force_resume:
                    # Reanudar encima de un recorder vivo partiria la sesion en
                    # dos procesos escribiendo el mismo log y los mismos mp4.
                    # Antes solo avisaba y seguia adelante.
                    print(f"error: el proceso {st['pid']} de esa sesion sigue "
                          f"vivo; no se reanuda encima.\n"
                          f"       si es otro recorder, pasale --force-resume, o "
                          f"esperalo a que termine.", file=sys.stderr)
                    return 4
                print(f"  aviso: --force-resume sobre un pid vivo ({st['pid']})",
                      file=sys.stderr)
            return _capture(args, resume_dir=session_dir,
                           t_offset=_resume_t(st, session_dir), gap=gap)
    return _capture(args)


def _capture(args, resume_dir=None, t_offset=0.0, gap=None):
    cfg = _config.load(args.config)
    if args.employee:
        cfg["session"]["employee"] = args.employee
    if args.service:
        cfg["session"]["service"] = args.service
    if args.policy:
        cfg["policy"]["mode"] = args.policy
    if args.idle_pause is not None:
        cfg["idle"]["pause_after_seconds"] = float(args.idle_pause)
    if args.no_video:
        cfg["capture"]["record_video"] = False
    if args.skip_preflight:
        report = None
    else:
        from . import probes
        report, _, _ = probes.preflight(args.config,
                                         backend_name=args.backend or "auto",
                                         disk=False)
    try:
        backend = factory.create(args.backend)
    except BackendUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("prueba con: python -m gpia selftest", file=sys.stderr)
        return 2
    if report is not None and not report.ok:
        print("\npreflight: fallo critico, la sesion no arranca", file=sys.stderr)
        for r in report.failures:
            if r.critical:
                print(f"  - {r.label}: {r.detail}", file=sys.stderr)
        print("  python -m gpia doctor para el detalle", file=sys.stderr)
        return 3
    if report is not None:
        for r in report.results:
            if r.status in ("warn", "fail") and not r.critical:
                print(f"  aviso: {r.label}: {r.detail}")
    print(f"backend={backend.name} dur={args.duration}s "
          f"empleado={cfg['session']['employee']!r} "
          f"servicio={cfg['session']['service']!r}")
    resumed = {}
    if resume_dir:
        # Reanudar sobre la misma carpeta: los segmentos siguen numerados y
        # `t` continua, para que evaluar.py no sume dos sesiones como una.
        from . import persist
        prev = persist.load_state(resume_dir)
        nxt = persist.next_segment_index(resume_dir)
        from .video import _segment_path
        base = os.path.join(resume_dir, "screen.mp4")
        resumed = {"t_offset": t_offset, "from": resume_dir,
                   "prev_samples": prev.get("samples", 0),
                   "prev_segments": prev.get("segments", []),
                   "next_segment": nxt}
        persist.add_gap(resume_dir, "reinicio/login", max(0, gap or 0))
        if getattr(args, "log", None) is not None:
            _h = f"~{float(gap):.1f}s" if gap is not None else "?"
            print(f"reanudando en {resume_dir} (t desde {t_offset:.1f}s, "
                  f"hueco {_h})")
    s = Session(cfg, backend, video=not args.no_video,
                out_dir=resume_dir, resumed=resumed)
    if resumed and s.recorder is not None:
        s.recorder.next_index = resumed["next_segment"]
    print(f"sesion -> {s.out_dir}")
    try:
        summary = s.run(args.duration, on_sample=_progress)
    except KeyboardInterrupt:
        print("\ninterrumpido; escribiendo resumen")
        summary = s.summary()
    s.write_summary(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary.get("audio_streams") is not None:
        print(f"informe #6 -> audio_streams={summary['audio_streams']} "
              f"({'OK, cero audio' if summary['audio_streams'] == 0 else 'FALLA'})")
    return 0


def _progress(s):
    print(f"  t={s['t']:7.1f} {s['state']:12s} idle={s['idle_s']:6.1f} "
          f"clicks={s['clicks']} {(s['exe'] or '-'):14s} {s['title'][:40]}")


def build_parser():
    ap = argparse.ArgumentParser(prog="gpia",
                                 description="ScreenAudit - capa de captura")
    ap.add_argument("--config", help="ruta alterna de config")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ip = sub.add_parser("init")
    ip.add_argument("--force", action="store_true")
    ip.set_defaults(func=cmd_init)

    d = sub.add_parser("doctor")
    d.add_argument("--backend")
    d.set_defaults(func=cmd_doctor)

    ct = sub.add_parser("control", help="pausar/reanudar/detener (solo admin)")
    ct.add_argument("action", choices=["pause", "resume", "stop", "status"])
    ct.add_argument("--session", help="carpeta de sesion (por defecto: la ultima)")
    ct.add_argument("--service")
    ct.add_argument("--actor", help="quien emite el comando")
    ct.set_defaults(func=cmd_control)

    rs = sub.add_parser("resume", help="retomar la sesion abierta tras un corte")
    rs.add_argument("--service")
    rs.add_argument("--employee")
    rs.add_argument("--force", action="store_true",
                    help="retomar aunque el proceso viejo siga vivo")
    rs.add_argument("--duration", type=int, default=0)
    rs.add_argument("--backend")
    rs.add_argument("--no-video", action="store_true")
    rs.add_argument("--idle-pause", type=float)
    rs.add_argument("--config")
    rs.add_argument("--log", action="store_true")
    rs.set_defaults(func=cmd_resume, policy=None, skip_preflight=True)

    ia = sub.add_parser("install-autostart",
                        help="unidad systemd para grabar al iniciar sesion")
    ia.add_argument("--service", default="")
    ia.add_argument("--employee", default="")
    ia.add_argument("--config", default="")
    ia.set_defaults(func=cmd_install_autostart)

    h = sub.add_parser("health", help="que fallo y que fue lento en una sesion")
    h.add_argument("--dir", help="carpeta de sesion (por defecto: la ultima)")
    h.set_defaults(func=cmd_health)

    b = sub.add_parser("bench", help="rendimiento del backend")
    b.add_argument("--backend")
    b.add_argument("--samples", type=int, default=50)
    b.set_defaults(func=cmd_bench)

    c = sub.add_parser("capture")
    c.add_argument("--duration", type=int, default=60)
    c.add_argument("--employee", default="")
    c.add_argument("--service", default="")
    c.add_argument("--backend")
    c.add_argument("--policy", choices=["all", "include", "exclude"])
    c.add_argument("--idle-pause", type=float)
    c.add_argument("--no-video", action="store_true")
    c.add_argument("--resume", action="store_true",
                   help="retomar la sesion abierta si el equipo se reinicio")
    c.add_argument("--force-resume", action="store_true",
                   help="reanudar aunque el proceso viejo siga vivo")
    c.add_argument("--skip-preflight", action="store_true",
                   help="no correr la cadena antes de grabar")
    c.set_defaults(func=cmd_capture)

    st = sub.add_parser("selftest")
    st.add_argument("--samples", type=int, default=10)
    st.add_argument("--employee", default="EMP001")
    st.add_argument("--service", default="606")
    st.add_argument("--policy", choices=["all", "include", "exclude"],
                    default="all")
    st.add_argument("--idle-pause", type=float, default=180.0)
    st.add_argument("--idle-resume", type=float, default=5.0)
    st.add_argument("--big-idle", type=float, default=200.0)
    st.set_defaults(func=cmd_selftest)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrumpido", file=sys.stderr)
        return 130
    except ValueError as exc:
        # Un config con un typo no deberia devolverle un traceback a un
        # operador: es el error mas probable y el mas facil de arreglar.
        print(f"error de configuracion:\n{exc}", file=sys.stderr)
        print("  python -m gpia init --force para regenerarlo", file=sys.stderr)
        return 1
    except BackendUnavailable as exc:
        print(f"backend no disponible: {exc}", file=sys.stderr)
        print("  python -m gpia doctor", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
