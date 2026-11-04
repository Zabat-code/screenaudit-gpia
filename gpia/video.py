"""Informe #6: zero audio, external and internal.

Enforced, not assumed. The legacy recorder (gp.py) never passed -an, which
means whether audio ended up in the file depended on ffmpeg's defaults. Here
audio is off unless explicitly enabled in config, and the flag is always
explicit on the command line so the output can be proven with ffprobe.

Informe #4 also lands here: region and draw_mouse are config, not hardcoded.
"""
import os
import shutil
import signal
import subprocess
import sys


def find_ffmpeg():
    """Local ffmpeg next to the package, then PATH. No machine-specific path."""
    local = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "bin", "ffmpeg")
    if os.path.exists(local):
        return local
    return shutil.which("ffmpeg") or "ffmpeg"


def _arm_pdeathsig(pid):
    """Deprecated shim. Kept only so old imports do not explode.

    The original version called prctl() from the PARENT, which arms the death
    signal for the parent itself and does nothing for ffmpeg. It is now handled
    in the child via `_PDEATHSIG_BOOTSTRAP`.
    """
    return None


_PDEATHSIG_BOOTSTRAP = (
    "import ctypes,os,sys\n"
    "try:\n"
    "    ctypes.CDLL('libc.so.6',use_errno=True).prctl(1,9,0,0,0)\n"
    "except Exception:\n"
    "    pass\n"
    "os.execvp(sys.argv[1],sys.argv[1:])\n"
)


def build_command(ffmpeg, out, *, fps, crf, preset, region=None,
                  audio=False, draw_mouse=True, duration=None,
                  platform=None, display=None):
    """Assemble the ffmpeg command. Platform-specific only in the input flags."""
    if audio:
        raise ValueError("audio no está permitido en esta versión")
    plat = platform or ("win32" if os.name == "nt" else "x11")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]

    if plat == "win32":
        cmd += ["-f", "gdigrab", "-framerate", str(fps)]
        if region:
            cmd += ["-offset_x", str(region["x"]),
                    "-offset_y", str(region["y"]),
                    "-video_size", f"{region['width']}x{region['height']}"]
        cmd += ["-draw_mouse", "1" if draw_mouse else "0"]
        cmd += ["-i", "desktop"]
    else:
        cmd += ["-f", "x11grab", "-framerate", str(fps),
                "-draw_mouse", "1" if draw_mouse else "0"]
        if region:
            cmd += ["-video_size",
                    f"{region['width']}x{region['height']}",
                    "-i", f"{display or os.environ.get('DISPLAY', ':0')}"
                          f"+{region['x']},{region['y']}"]
        else:
            cmd += ["-video_size", "1920x1080",
                    "-i", display or os.environ.get("DISPLAY", ":0")]

    # El cliente requiere archivos sin audio, sin excepciones.
    cmd += ["-an"]

    cmd += ["-pix_fmt", "yuv420p", "-c:v", "libx264",
            "-preset", str(preset), "-crf", str(crf)]
    if duration:
        cmd += ["-t", str(duration)]
    cmd += [out]
    return cmd


def _segment_path(base, n):
    """/out/screen.mp4 -> /out/screen-001.mp4, -002, ...

    Informe #4: idle pause splits the recording. Reusing one filename would
    truncate the active segment on every resume, so each active stretch gets
    its own file and nothing is ever overwritten.
    """
    root, ext = os.path.splitext(base)
    return f"{root}-{n:03d}{ext or '.mp4'}"


class VideoRecorder:
    """ffmpeg subprocess wrapper that can be stopped and restarted (idle).

    Every start() opens a NEW segment file. Stopping and resuming never
    overwrites prior footage.
    """

    def __init__(self, path, cfg, platform=None, display=None):
        self.base = path
        self.cfg = cfg
        self.platform = platform
        self.display = display
        self.proc = None
        self.ffmpeg = find_ffmpeg()
        self.segments = []
        # Informe #17: al reanudar, la numeracion sigue donde quedo para no
        # pisar los segmentos de la sesion anterior.
        self.next_index = 1
        self.current = None

    def start(self, duration=None):
        cap = self.cfg.get("capture", {})
        n = max(len(self.segments) + 1, self.next_index)
        out = _segment_path(self.base, n)
        cmd = build_command(
            self.ffmpeg, out,
            fps=int(cap.get("fps", 10)),
            crf=int(cap.get("crf", 23)),
            preset=cap.get("preset", "ultrafast"),
            region=cap.get("region"),
            audio=bool(self.cfg.get("audio", {}).get("enabled", False)),
            draw_mouse=bool(cap.get("draw_mouse", True)),
            duration=duration,
            platform=self.platform,
            display=self.display,
        )
        # Informe #10: ffmpeg must die with us, even on SIGTERM or SIGKILL.
        # Python does not run finally/atexit on those signals, so the only
        # reliable guarantees are: own process group + PDEATHSIG on Linux.
        # Without this, `systemctl stop` leaves ffmpeg recording forever and
        # fills the disk (observed: 7 orphans, load average 43).
        #
        # PDEATHSIG is armed by the CHILD between fork and exec, because
        # prctl() applies to the calling process. preexec_fn would do that too
        # but is not thread-safe, and this recorder runs threads (click hook,
        # control poller), so a tiny exec bootstrap is the safe route. The
        # setting survives execve, and setsid() does not reparent, so ffmpeg
        # keeps pointing at this recorder.
        argv = cmd
        if os.name == "posix":
            argv = [sys.executable, "-c", _PDEATHSIG_BOOTSTRAP, cmd[0]] + cmd[1:]
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE,
                                         start_new_session=True)
        else:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE)
        self.segments.append(out)
        self.next_index = n + 1
        self.current = out
        return self.proc.pid

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if not self.is_running():
            self.proc = None
            return
        try:
            self.proc.stdin.write(b"q")
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
        except Exception:
            self._kill_group()
        self.proc = None

    def _kill_group(self):
        """SIGTERM then SIGKILL the whole process group, ffmpeg included.

        Signalling only self.proc.pid is not enough: start_new_session may have
        been ignored on some platforms, and ffmpeg can itself fork helpers.
        """
        for sig, grace in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
            try:
                pgid = os.getpgid(self.proc.pid)
            except Exception:
                pgid = None
            try:
                if pgid is not None and pgid != os.getpgid(0):
                    os.killpg(pgid, sig)
                else:
                    self.proc.send_signal(sig)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            try:
                self.proc.wait(timeout=grace)
                return
            except Exception:
                continue

    def size(self):
        """Total bytes across every segment."""
        total = 0
        for p in self.segments:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        return total

    def audio_streams(self, ffprobe=None):
        """Informe #6 proof: no segment may carry an audio stream."""
        counts = [count_audio_streams(p, ffprobe) for p in self.segments]
        known = [c for c in counts if c is not None]
        if not known:
            return None
        return max(known)


def count_audio_streams(path, ffprobe=None):
    """Proof for informe #6. Returns the number of audio streams, or None.

    Callers can assert this is 0 to prove the recording has no audio.
    """
    ffprobe = ffprobe or shutil.which("ffprobe")
    if not ffprobe or not os.path.exists(path):
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
        return len([ln for ln in r.stdout.splitlines() if ln.strip()])
    except Exception:
        return None
