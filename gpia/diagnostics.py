"""Informe #16: cadena de modulos, guardarrailes y rendimiento.

Hoy el recorder es a prueba de caidas pero a ciegas: un `except Exception`
convierte un backend roto en un log limpio con `idle=0`, y un ffmpeg que no
graba se ve igual que uno que graba. Este modulo hace lo contrario.

Tres piezas, deliberadamente separadas:

  Chain        una lista de modulos, cada uno con un probe. Un modulo que
               lanza excepcion no tumba la cadena: se registra como `fail`
               y los demas siguen. Ahi esta la parte modular: agregar un
               chequeo es `chain.add(...)`, no tocar el runner.
  Diagnostics  el sumidero en caliente. Escribe cada evento a
               `diagnostics.jsonl` (una linea = un evento,Machine-readable)
               y lleva conteos y tiempos en memoria para el resumen.
  Stopwatch    tiempos por operacion, con p50/p95/max/mean, para responder
               "que tan rapido va de verdad" y no "cuanto tardo en el
               primer sample".

Los estados son cuatro y significan cosas distintas:
  ok    el modulo cumplio su parte
  warn  cumplio, pero con una condicion que conviene mirar
  fail  no cumplio su parte
  skip  no se pudo evaluar (falta una dependencia, por ejemplo)
"""
import json
import os
import time
import traceback

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

ORDER = {OK: 0, WARN: 1, SKIP: 2, FAIL: 3}
_GLYPH = {OK: "OK  ", WARN: "AVISO", FAIL: "FALLA", SKIP: "SALTO"}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class ProbeError(Exception):
    """Una comprobacion fallo. `status` decide si es fail o warn."""


class SkipProbe(Exception):
    """No se puede evaluar: falta algo. No es culpa del modulo."""


class Result:
    """El veredicto de un modulo. Inmutable en la practica."""

    __slots__ = ("name", "label", "status", "ms", "detail", "data",
                 "error", "critical", "traceback")

    def __init__(self, name, status, ms=0.0, detail="", data=None,
                 error=None, critical=False, tb=None, label=None):
        self.name = name
        self.label = label or name
        self.status = status
        self.ms = float(ms)
        self.detail = detail
        self.data = data if data is not None else {}
        self.error = error
        self.critical = bool(critical)
        self.traceback = tb

    def to_dict(self):
        d = {"module": self.name, "label": self.label, "status": self.status,
             "ms": round(self.ms, 2), "detail": self.detail}
        if self.data:
            d["data"] = self.data
        if self.error:
            d["error"] = self.error
        if self.critical:
            d["critical"] = True
        if self.traceback:
            d["traceback"] = self.traceback
        return d

    @property
    def ok(self):
        return self.status in (OK, SKIP)

    def __repr__(self):
        return f"<Result {self.name} {self.status} {self.ms:.1f}ms>"


class Chain:
    """Lista ordenada de modulos con su probe.

    Uso tipico:

        chain = Chain("preflight")
        chain.add("ffmpeg", probe_ffmpeg, critical=True)
        chain.add("display", probe_display)
        report = chain.run()

    Un probe es `fn()` que devuelve `str` (detalle) o `(str, dict)` para
    detalle + datos. Para devolver un estado distinto de ok sin lanzar
    excepcion, devuelve `ProbeResult` con `status=` puesto, o usa
    `warn()` / `skip()`.
    """

    def __init__(self, name, diagnostics=None):
        self.name = name
        self.diagnostics = diagnostics
        self._mods = []

    def add(self, name, fn, critical=False, label=None):
        self._mods.append({"name": name, "fn": fn, "critical": critical,
                           "label": label or name})
        return self

    def modules(self):
        return [m["name"] for m in self._mods]

    def run(self, only=None):
        """Ejecuta la cadena. Ninguna excepcion escapa de aqui."""
        out = []
        for m in self._mods:
            if only and m["name"] not in only:
                out.append(Result(m["name"], SKIP, label=m["label"],
                                  detail="no seleccionado"))
                continue
            r = _run_one(m)
            out.append(r)
            if self.diagnostics:
                self.diagnostics.record(r, chain=self.name)
        return Report(self.name, out)


def _run_one(m):
    name, fn, critical, label = m["name"], m["fn"], m["critical"], m["label"]
    t0 = time.perf_counter()
    try:
        got = fn()
    except SkipProbe as exc:
        return Result(name, SKIP, (time.perf_counter() - t0) * 1000,
                      detail=str(exc) or "sin datos", critical=critical,
                      label=label)
    except ProbeError as exc:
        return Result(name, FAIL, (time.perf_counter() - t0) * 1000,
                      detail=str(exc), error=type(exc).__name__,
                      tb=traceback.format_exc(limit=3), critical=critical,
                      label=label)
    except Exception as exc:                      # un modulo nunca tumba la cadena
        return Result(name, FAIL, (time.perf_counter() - t0) * 1000,
                      detail=f"{type(exc).__name__}: {exc}",
                      error=type(exc).__name__,
                      tb=traceback.format_exc(limit=3), critical=critical,
                      label=label)
    ms = (time.perf_counter() - t0) * 1000

    if isinstance(got, Result):
        # El modulo es el autor del veredicto, no el helper inline.
        got.name = name
        got.label = label
        got.ms = ms
        got.critical = got.critical or critical
        return got
    if isinstance(got, tuple):
        detail, data = (got + ("",))[:2] if len(got) == 2 else (got[0], got[1])
        return Result(name, OK, ms, str(detail), data, critical=critical,
                      label=label)
    if isinstance(got, dict):
        detail = got.pop("detail", "") if "detail" in got else ""
        return Result(name, OK, ms, str(detail), got, critical=critical,
                      label=label)
    return Result(name, OK, ms, str(got if got is not None else ""),
                  critical=critical, label=label)


def ok(detail, **data):
    return Result("<inline>", OK, detail=detail, data=data)


def warn(detail, **data):
    return Result("<inline>", WARN, detail=detail, data=data)


def fail(detail, **data):
    return Result("<inline>", FAIL, detail=detail, data=data)


def skip(detail):
    return Result("<inline>", SKIP, detail=detail)


class Report:
    def __init__(self, name, results):
        self.name = name
        self.results = results

    @property
    def ok(self):
        """True si ningun modulo critico fallo. Los no criticos son avisos."""
        return not any(r.status == FAIL and r.critical for r in self.results)

    @property
    def failures(self):
        return [r for r in self.results if r.status == FAIL]

    @property
    def warnings(self):
        return [r for r in self.results if r.status == WARN]

    @property
    def total_ms(self):
        return sum(r.ms for r in self.results)

    def counts(self):
        c = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for r in self.results:
            c[r.status] = c.get(r.status, 0) + 1
        return c

    def to_dict(self):
        return {"chain": self.name, "ok": self.ok, "total_ms": round(self.total_ms, 2),
                "counts": self.counts(), "modules": [r.to_dict() for r in self.results]}

    def render(self, width=62):
        lines = ["=" * width, f"GPIA - cadena {self.name}", "=" * width]
        for r in sorted(self.results, key=lambda x: (ORDER[x.status], x.ms),
                        reverse=False):
            mark = "*" if r.critical else " "
            lines.append(f" [{_GLYPH[r.status]}] {r.label:<26.26} {r.ms:7.1f}ms {mark}")
            if r.detail:
                lines.append(f"        {r.detail}")
            if r.error:
                lines.append(f"        {r.error}")
        c = self.counts()
        lines.append("-" * width)
        lines.append(f" ok={c[OK]} aviso={c[WARN]} salto={c[SKIP]} falla={c[FAIL]}"
                     f"  total={self.total_ms:.0f}ms  (* = critico)")
        lines.append("=" * width)
        return "\n".join(lines)


class Stopwatch:
    """Tiempos por operacion. Percentiles en vez de promedio solo."""

    def __init__(self, limit=2000):
        self._d = {}
        self._limit = limit

    def add(self, name, seconds):
        v = self._d.setdefault(name, [])
        if len(v) < self._limit:
            v.append(float(seconds) * 1000.0)

    def stage(self, name):
        return _Stage(self, name)

    def count(self, name):
        return len(self._d.get(name, []))

    def stats(self):
        out = {}
        for k, v in self._d.items():
            if not v:
                continue
            s = sorted(v)
            out[k] = {
                "n": len(s),
                "mean": round(sum(s) / len(s), 2),
                "p50": round(s[len(s) // 2], 2),
                "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2),
                "max": round(s[-1], 2),
            }
        return out

    def slowest(self, n=3):
        st = self.stats()
        return sorted(st.items(), key=lambda kv: -kv[1]["p95"])[:n]


class _Stage:
    def __init__(self, sw, name):
        self.sw, self.name = sw, name

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.sw.add(self.name, time.perf_counter() - self.t0)
        return False


class Diagnostics:
    """Sumidero de eventos. JSONL append-only: una linea por evento.

    Necesario porque `gp_log.jsonl` no se puede tocar (contrato heredado) y
    porque un error necesita quedar en disco aunque el proceso muera.
    """

    #: Cuantas veces se escribe el mismo error antes de empezar a saltarlo.
    ERROR_REPEAT = 3
    #: Cada cuantas repeticiones se escribe una linea de nuevo.
    ERROR_EVERY = 50

    def __init__(self, path=None, echo=False, clock=time.perf_counter):
        self.path = path
        self.echo = echo
        self.clock = clock
        self.sw = Stopwatch()
        self._fh = None
        self._errors = []
        self._counters = {}
        self._seen = {}
        self._sink_error = None
        self.chains = []

    def _writer(self):
        if self._fh is None and self.path:
            try:
                d = os.path.dirname(self.path)
                if d:
                    os.makedirs(d, exist_ok=True)
                self._fh = open(self.path, "a", encoding="utf-8")
            except OSError as exc:
                # Informe #16: sin disco no hay ni eventos ni salida de
                # diagnostico. Se cuenta para que session.json lo diga, en
                # vez de fingir que todo fue bien.
                self._counters["sink_broken"] = self._counters.get("sink_broken", 0) + 1
                self._sink_error = str(exc)
                self._fh = None
        return self._fh

    def emit(self, event, **fields):
        rec = {"ts": now_iso(), "event": event}
        rec.update(fields)
        fh = self._writer()
        if fh is not None:
            try:
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                fh.flush()
            except (OSError, ValueError) as exc:
                # ENOSPC o permisos: el evento se pierde, pero no en silencio.
                self._counters["sink_write_failed"] = \
                    self._counters.get("sink_write_failed", 0) + 1
                self._sink_error = str(exc)
                try:
                    fh.close()
                except OSError:
                    pass
                self._fh = None
        if self.echo:
            print("  [diag] " + json.dumps(rec, ensure_ascii=False, default=str))
        return rec

    def record(self, result, chain=""):
        self.chains.append(result)
        if result.status == FAIL:
            self._errors.append(result)
            self.bump("errors")
        elif result.status == WARN:
            self.bump("warnings")
        self.bump(f"{result.status}:{result.name}")
        return self.emit("module", chain=chain, **result.to_dict())

    def error(self, module, exc, **context):
        """Un error en caliente, sin patear la sesion.

        Deduplicado por huella (modulo + tipo + mensaje). Un backend roto
        falla en cada sample, y a 2s de intervalo una jornada de 8h son
        ~14 400 repeticiones del mismo error: el archivo tiene que seguir
        siendo legible. Se escriben las primeras, despues cada 50, y el
        total queda en los contadores.
        """
        self._errors.append(exc)
        self.bump("errors")
        self.bump(f"error:{module}")
        key = (module, type(exc).__name__, str(exc)[:80])
        n = self._seen.get(key, 0) + 1
        self._seen[key] = n
        if n > self.ERROR_REPEAT and n % self.ERROR_EVERY:
            return None
        return self.emit("error", module=module,
                         error=type(exc).__name__, message=str(exc),
                         repeat=n, tb=traceback.format_exc(limit=4), **context)

    def bump(self, name, by=1):
        self._counters[name] = self._counters.get(name, 0) + by

    def counters(self):
        return dict(self._counters)

    def errors(self):
        return list(self._errors)

    def stage(self, name):
        return self.sw.stage(name)

    def timing(self, module, seconds, **fields):
        self.sw.add(module, seconds)
        return self.emit("timing", module=module,
                         ms=round(seconds * 1000, 2), **fields)

    def note(self, module, detail, **fields):
        """Un dato que no es error: por ejemplo `idle siempre 0`."""
        return self.emit("note", module=module, detail=detail, **fields)

    def health(self):
        return {
            "counters": self.counters(),
            "sink_error": self._sink_error,
            "timings": self.sw.stats(),
            "slowest": [{"module": k, **v} for k, v in self.sw.slowest()],
            "errors": [
                (r.to_dict() if isinstance(r, Result)
                 else {"error": type(r).__name__, "message": str(r)})
                for r in self._errors],
            "chains": [r.to_dict() for r in self.chains],
        }

    def close(self):
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None


class NullDiagnostics(Diagnostics):
    """Para tests y para `selftest`: cuenta pero no escribe."""

    def __init__(self):
        super().__init__(path=None, echo=False)
