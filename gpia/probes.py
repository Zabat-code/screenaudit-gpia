"""Los modulos de la cadena. Un probe por responsabilidad.

Cada funcion es independiente: recibe lo que necesita y devuelve detalle +
datos, o lanza `ProbeError` / `SkipProbe`. Ninguna depende del estado de otra,
asi que se pueden correr de a una o todas juntas.

`preflight()` arma la cadena completa. `runtime_chain()` arma la version
liviana que se usa durante la sesion.
"""
import os
import shutil
import subprocess
import tempfile
import sys
import time

from . import config as _config
from . import paths as _paths
from . import schemas
from .backends import factory, BackendUnavailable
from .diagnostics import (Chain, OK, WARN, FAIL, Result, ProbeError, SkipProbe,
                          warn, fail)
from .video import find_ffmpeg, build_command

# Campos que el motor heredado no puede dejar de ver.
LEGACY_FIELDS = ("t", "app", "exe", "title", "pid", "hwnd")


# ---------- configuracion ----------
def probe_config(cfg_path):
    def run():
        try:
            cfg = _config.load(cfg_path)
        except ValueError as exc:
            raise ProbeError(f"config invalido: {exc}")
        detail = (f"sample={cfg['sample_interval_seconds']}s "
                  f"idle={cfg['idle']['pause_after_seconds']}/"
                  f"{cfg['idle']['resume_after_seconds']}s "
                  f"audio={cfg['audio']['enabled']} policy={cfg['policy']['mode']}")
        return detail, {"path": _config.config_path(cfg_path),
                        "min_segment_seconds": cfg["capture"].get("min_segment_seconds")}
    return run


def probe_paths():
    def run():
        sdir = _paths.sessions_dir()
        try:
            os.makedirs(sdir, exist_ok=True)
        except OSError as exc:
            raise ProbeError(f"no se pudo crear {sdir}: {exc}")
        probe = os.path.join(sdir, ".write_probe")
        try:
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
        except OSError as exc:
            raise ProbeError(f"sesiones no escribibles: {exc}")
        return f"{sdir} (escribible)", {"dir": sdir}
    return run


def probe_disk():
    def run():
        sdir = _paths.sessions_dir()
        try:
            usage = shutil.disk_usage(sdir)
        except OSError as exc:
            raise SkipProbe(f"no se pudo medir disco: {exc}")
        gb = usage.free / 1e9
        if gb < 0.5:
            return fail(f"solo {gb:.2f} GB libres", free_gb=round(gb, 2))
        if gb < 5:
            return warn(f"{gb:.1f} GB libres", free_gb=round(gb, 2))
        return f"{gb:.1f} GB libres", {"free_gb": round(gb, 2)}
    return run


# ---------- herramientas externas ----------
def probe_ffmpeg():
    def run():
        exe = find_ffmpeg()
        if not exe:
            raise ProbeError("ffmpeg no esta en PATH")
        return exe, {"path": exe}
    return run


def probe_ffprobe():
    def run():
        exe = shutil.which("ffprobe")
        if not exe:
            raise SkipProbe("ffprobe no esta en PATH (audio_streams quedara None)")
        return exe, {"path": exe}
    return run


def probe_encoder():
    """Codifica real de 3 frames. Un h264 que existe no siempre compila.

    A un archivo temporal, no a stdout: mp4 sobre pipe no es buscable y
    ffmpeg cierra sin frames ("nothing was written into output file").
    """
    def run():
        ff = find_ffmpeg()
        if not ff:
            raise SkipProbe("sin ffmpeg")
        fd, tmp = tempfile.mkstemp(suffix=".mp4", prefix="gpia_enc_")
        os.close(fd)
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-f", "lavfi", "-i", "testsrc=size=64x64:rate=4",
               "-frames:v", "3", "-an", "-c:v", "libx264", tmp]
        t0 = time.perf_counter()
        try:
            p = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               timeout=20)
        except subprocess.TimeoutExpired:
            raise ProbeError("libx264 tardo mas de 20s en un test de 3 frames")
        except OSError as exc:
            raise ProbeError(f"no se pudo ejecutar ffmpeg: {exc}")
        ms = (time.perf_counter() - t0) * 1000
        try:
            size = os.path.getsize(tmp)
        except OSError:
            size = 0
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if p.returncode != 0 or size == 0:
            err = p.stderr.decode("utf-8", "replace").strip().splitlines()
            raise ProbeError("libx264 no encode: " + (err[-1] if err else "salida vacia"))
        return f"libx264 ok ({size} bytes, {ms:.0f}ms)", {
            "bytes": size, "ms": round(ms, 1)}
    return run


# ---------- backend ----------
def probe_backend(backend_name, display=None):
    def run():
        t0 = time.perf_counter()
        try:
            b = factory.create(backend_name)
        except BackendUnavailable as exc:
            raise ProbeError(f"backend no disponible: {exc}")
        ms = (time.perf_counter() - t0) * 1000
        return f"{b.name} construido en {ms:.0f}ms", {
            "backend": b.name, "ms": round(ms, 1), **b.describe()}
    return run


def probe_foreground(backend_name, display=None):
    def run():
        try:
            b = factory.create(backend_name)
            win = b.foreground_window()
        except Exception as exc:
            raise ProbeError(f"foreground_window fallo: {type(exc).__name__}: {exc}")
        if win is None:
            return warn("devuelve None (sin window manager)", backend=backend_name)
        return f"{win.exe} :: {win.title[:40]}", {
            "exe": win.exe, "pid": win.pid, "hwnd": win.hwnd}
    return run


def probe_idle(backend_name, display=None):
    """En X11 sin WM esto devuelve 0 siempre: es una limitacion conocida."""
    def run():
        try:
            b = factory.create(backend_name)
            v = float(b.idle_seconds())
        except Exception as exc:
            raise ProbeError(f"idle_seconds fallo: {type(exc).__name__}: {exc}")
        if v == 0.0:
            return warn("siempre 0: el gate de pausa no podra dispararse aqui",
                        value=v)
        return f"{v:.1f}s", {"value": v}
    return run


def probe_clicks(backend_name, display=None):
    def run():
        got = []
        try:
            b = factory.create(backend_name)
            okk = b.start_click_hook(lambda x, y, btn: got.append((x, y)))
            b.stop_click_hook()
        except Exception as exc:
            return warn(f"no se pudo instalar el hook: {type(exc).__name__}: {exc}",
                        backend=backend_name)
        if not okk:
            return fail("el backend reports start_click_hook=False",
                        backend=backend_name)
        return warn("hook instalado pero sin eventos de prueba (normal en X11 sin WM)",
                    installed=True, events=len(got))
    return run


def probe_legacy_contract():
    """El motor heredado no puede dejar de ver estos campos."""
    def run():
        line = schemas.make_sample(
            elapsed=0.0,
            window={"hwnd": 1, "pid": 2, "title": "t", "exe": "e.exe", "app": "e"},
            idle_s=0.0, clicks=0, service="606", employee="X", state="active")
        missing = schemas.check_legacy_compat(line)
        if missing:
            raise ProbeError(f"faltan campos del contrato: {missing}")
        return f"{len(LEGACY_FIELDS)} campos legacy presentes", {
            "fields": list(LEGACY_FIELDS)}
    return run


def probe_video_command(cfg):
    """Verifica que la linea de ffmpeg se arme con audio off explicito."""
    def run():
        cap = cfg["capture"]
        cmd = build_command(
            find_ffmpeg(), "/tmp/_probe.mp4",
            fps=cap.get("fps", 10), crf=cap.get("crf", 23),
            preset=cap.get("preset", "ultrafast"),
            region=cap.get("region"),
            audio=bool(cfg.get("audio", {}).get("enabled", False)),
            draw_mouse=bool(cap.get("draw_mouse", True)))
        if cfg["audio"]["enabled"]:
            if "-an" in cmd:
                raise ProbeError("audio habilitado pero la linea lleva -an")
            return f"{len(cmd)} args, audio pedido", {"n_args": len(cmd)}
        if "-an" not in cmd:
            raise ProbeError("la linea de ffmpeg no lleva -an explicito")
        if "libx264" not in cmd:
            raise ProbeError("no se encontro el encoder esperado")
        return f"{len(cmd)} args, -an explicito", {"n_args": len(cmd)}
    return run


# ---------- cadenas completas ----------
def preflight(cfg_path=None, backend_name="auto", display=None, disk=True):
    """Cadena completa de arranque. Devuelve (Report, cfg, backend|None)."""
    try:
        cfg = _config.load(cfg_path)
    except ValueError as exc:
        ch = Chain("preflight")
        ch.add("config", _bad_config(cfg_path, exc), critical=True, label="config")
        return ch.run(), None, None

    ch = Chain("preflight")
    ch.add("config", probe_config(cfg_path), critical=True)
    ch.add("paths", probe_paths(), critical=True)
    if disk:
        ch.add("disk", probe_disk())
    ch.add("ffmpeg", probe_ffmpeg(), critical=True)
    ch.add("ffprobe", probe_ffprobe())
    ch.add("encoder", probe_encoder())
    ch.add("legacy_contract", probe_legacy_contract(), critical=True)
    ch.add("video_command", probe_video_command(cfg))
    ch.add("backend", probe_backend(backend_name), critical=True)
    ch.add("foreground", probe_foreground(backend_name), critical=True)
    ch.add("idle", probe_idle(backend_name))
    ch.add("clicks", probe_clicks(backend_name))

    diag = None
    report = ch.run()
    try:
        backend = factory.create(backend_name)
    except BackendUnavailable:
        backend = None
    return report, cfg, backend


def _bad_config(cfg_path, exc):
    def run():
        raise ProbeError(str(exc))
    return run
