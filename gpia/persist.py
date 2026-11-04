"""Informe #17: estado de sesion en disco y reanudacion tras reinicio.

Si el proceso muere (crash, corte de luz, `kill`), el `state.json` queda
con `closed: false`. Eso es la senal de que la sesion quedo abierta y hay
que retomarla.

Dos cosas que hay que tener presentes al reanudar:

1. `t` tiene que seguir siendo montono. `evaluar.py` no lee `state`: recalcula
   todo con los deltas de `t`. Si al reanudar `t` volviera a 0, el motor
   sumaria dos sesiones como una sola. Asi que `t` continua desde el ultimo
   valor, y la interrupcion queda registrada aparte en `gaps`.

2. El ultimo `.mp4` quedo a medio escribir. ffmpeg escribe el indice (moov)
   al final, asi que un archivo de un proceso muerto no se puede reproducir.
   O se re-muxea, o se aparta. Prefiero re-muxear: a veces si se salva, y si
   no el archivo queda a la vista en vez de desaparecer.
"""
import json
import os
import subprocess
import time

STATE_FILE = "state.json"
STATE_VERSION = 1


def _slug(service):
    """`slugify` del servicio, sin importar `paths` al cargar este modulo.

    Import local a proposito: `paths` no sabe nada de `persist`, pero conviene
    que la dependencia vaya en un solo sentido y no en tiempo de importacion.
    """
    from .paths import slugify
    return slugify(service)


def state_path(session_dir):
    return os.path.join(session_dir, STATE_FILE)


def save_state(session_dir, **fields):
    """Escribe el estado. Atomico: un corte a mitad no deja un JSON truncado."""
    p = state_path(session_dir)
    doc = {}
    try:
        with open(p, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        doc = {}
    doc.update(fields)
    doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    doc["updated_epoch"] = int(time.time())
    doc.setdefault("version", STATE_VERSION)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except OSError:
        return None
    return doc


def load_state(session_dir):
    try:
        with open(state_path(session_dir), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def is_open(session_dir):
    st = load_state(session_dir)
    return bool(st) and not st.get("closed", True)


def find_open_session(root, service=None, employee=None, max_age=None):
    """Sesion abierta mas reciente. `max_age` en segundos.

    El servicio se pasa por `slugify()` igual que al crear la carpeta. Sin eso,
    `--service 606/soporte` buscaba en `606/soporte/` mientras el directorio
    real es `606_soporte/`, y la reanudacion no encontraba nada.
    """
    base = os.path.join(root, _slug(service)) if service else root
    best = None
    if not os.path.isdir(base):
        return None
    for dirpath, dirnames, files in os.walk(base):
        if STATE_FILE not in files:
            continue
        st = load_state(dirpath)
        if not st or st.get("closed", True):
            continue
        if employee and st.get("employee") != employee:
            continue
        stamp = st.get("ended_epoch") or st.get("updated_epoch") or 0
        try:
            m = os.path.getmtime(state_path(dirpath))
        except OSError:
            continue
        if max_age is not None and (time.time() - m) > max_age:
            continue
        if best is None or m > best[0]:
            best = (m, dirpath, st)
    return best[1:] if best else None


def close_state(session_dir, **fields):
    return save_state(session_dir, closed=True,
                      ended=time.strftime("%Y-%m-%dT%H:%M:%S"),
                      ended_epoch=int(time.time()), **fields)


def seconds_since(session_dir, state=None):
    """Hueco desde el ultimo latido. Robusto a un `updated_epoch` viejo.

    Se toma el maximo entre lo que dice el estado y la fecha del archivo: si
    el estado quedo desactualizado, el mtime es el dato fiable.
    """
    st = state if state is not None else load_state(session_dir)
    try:
        m = os.path.getmtime(state_path(session_dir))
    except OSError:
        m = 0.0
    last = max(float(st.get("updated_epoch") or 0.0), m)
    return max(0.0, time.time() - last) if last else 0.0


def last_log_t(session_dir, log_name="gp_log.jsonl"):
    """`t` de la ultima linea del log, o None si todavia no hay lineas.

    Al reanudar, el `state.json` puede estar atrasado: el checkpoint se escribe
    cada N muestras, asi que el log suele tener un par de lineas mas. Como
    `evaluar.py` reconstruye la sesion con los deltas de `t`, volver a un `t`
    menor que el ultimo ya escrito hace retroceder el reloj y descuadra el
    total. El log es la verdad de que se escribio; el estado es una foto.
    """
    path = os.path.join(session_dir, log_name)
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if not size:
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 65536))
            data = fh.read()
    except OSError:
        return None
    for raw in reversed(data.split(b"\n")):
        raw = raw.strip()
        if not raw:
            continue
        try:
            return float(json.loads(raw.decode("utf-8"))["t"])
        except (ValueError, KeyError, TypeError):
            # Una linea parcial (si el seek cayo en medio) o corrupta: se
            # sigue para atras. Solo puede dar un `t` mas viejo, que es el
            # lado seguro: `_resume_t` toma el maximo con el estado.
            continue
    return None


def add_gap(session_dir, reason, seconds):
    """Registro explicito del hueco. Un corte no es tiempo trabajado."""
    st = load_state(session_dir)
    gaps = st.get("gaps") or []
    gaps.append({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "reason": reason, "seconds": round(float(seconds), 1)})
    save_state(session_dir, gaps=gaps)
    return gaps[-1]


def _size(path):
    """Tamano sininventar. Un archivo que no existe es 0, no una excepcion."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def repair_last_segment(session_dir, ffmpeg="ffmpeg", diag=None):
    """Re-muxea el ultimo segmento. Devuelve que hizo.

    `ok`      el archivo estaba bien
    `remuxed` se salvo
    `lost`    no se pudo salvar, quedo apartado con su tamano real
    """
    from .video import find_ffmpeg
    segs = sorted_segments(session_dir)
    if not segs:
        return {"status": "none", "detail": "sin segmentos"}
    last = segs[-1]
    size = _size(last)
    if size == 0:
        return {"status": "lost", "file": os.path.basename(last),
                "bytes": 0, "detail": "vacio"}
    if _is_playable(last, ffmpeg):
        return {"status": "ok", "file": os.path.basename(last), "bytes": size}
    exe = find_ffmpeg()
    fixed = last + ".remux.mp4"
    try:
        p = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-i", last, "-c", "copy", fixed],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _quarantine(last, diag)
        if diag:
            diag.error("repair_segment", exc, file=os.path.basename(last))
        return {"status": "lost", "file": os.path.basename(last),
                "bytes": size, "detail": str(exc)}
    if p.returncode == 0 and _size(fixed) > 0:
        os.replace(fixed, last)
        return {"status": "remuxed", "file": os.path.basename(last),
                "bytes": _size(last)}
    _quarantine(last, diag)
    try:
        os.remove(fixed)
    except OSError:
        pass
    return {"status": "lost", "file": os.path.basename(last), "bytes": size,
            "detail": "ffmpeg no pudo re-muxear"}


def _quarantine(path, diag=None):
    """Lo aparta con su tamano: un corte no se pierde, y queda visible."""
    dst = path + ".unrecoverable"
    try:
        if os.path.exists(dst):
            os.remove(dst)
        os.replace(path, dst)
    except OSError as exc:
        if diag:
            diag.error("quarantine", exc, file=os.path.basename(path))
        return
    if diag:
        diag.emit("segment_unrecoverable",
                  file=os.path.basename(dst), bytes=_size(dst),
                  detail="ffmpegmurio sin escribir el indice; archivo apartado")


def _is_playable(path, ffmpeg):
    try:
        p = subprocess.run(
            [ffmpeg, "-hide_banner", "-v", "error", "-i", path,
             "-f", "null", "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=60)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def sorted_segments(session_dir):
    from .video import _segment_path
    root, ext = os.path.splitext(os.path.join(session_dir, "screen"))
    out = []
    if not os.path.isdir(session_dir):
        return out
    for f in os.listdir(session_dir):
        if f.startswith("screen-") and f.endswith(".mp4"):
            out.append(os.path.join(session_dir, f))
    return sorted(out)


def next_segment_index(session_dir):
    return len(sorted_segments(session_dir)) + 1
