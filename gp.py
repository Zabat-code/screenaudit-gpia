#!/usr/bin/env python3
"""
GP - Grabador de Pantalla (Screen Recorder + Foreground-Window Logger)

Records the desktop with ffmpeg (no admin, no GPU) and, in parallel, samples
the FOREGROUND WINDOW every --interval seconds, writing a reference log
(gp_log.jsonl) with: timestamp, pid, exe, window title, derived app name.

The log is the AUTHORITATIVE source for "which program is running" (the
architect's primary criterion: game/video programs pause quality minutes).
No screen text is read; only OS-provided window title + exe name (privacy-conscious).

Usage:
  python gp.py --out rec.mp4 --dur 120 --fps 10
  python gp.py --out rec.mp4 --dur 600 --fps 10 --interval 2 --log gp_log.jsonl
  python gp.py --out rec.mp4 --dur 300 --fps 10 --region 1280x720+0+0
"""
import argparse
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import threading
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi


def find_ffmpeg():
    """Local ffmpeg.exe next to this script, else system PATH."""
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
    if os.path.exists(local):
        return local
    return "ffmpeg"


def get_exe(pid):
    """Process executable name from pid (no admin needed for own session)."""
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(2048)
    psapi.GetModuleFileNameExW.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.wintypes.HMODULE,
        ctypes.wintypes.LPWSTR, ctypes.wintypes.DWORD]
    psapi.GetModuleFileNameExW.restype = ctypes.wintypes.DWORD
    n = psapi.GetModuleFileNameExW(h, 0, buf, 2048)
    kernel32.CloseHandle(h)
    if not n:
        return ""
    return os.path.basename(buf.value).lower()


def get_foreground():
    """Return dict with hwnd, pid, title, exe, app of the foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    length = user32.GetWindowTextLengthW(hwnd) + 1
    buf = ctypes.create_unicode_buffer(length)
    user32.GetWindowTextW(hwnd, buf, length)
    title = buf.value
    exe = get_exe(pid.value)
    app = exe.replace(".exe", "") if exe else ""
    return {"hwnd": int(hwnd), "pid": int(pid.value),
            "title": title, "exe": exe, "app": app}


def build_ffmpeg_cmd(ff, out, dur, fps, region):
    cmd = [ff, "-y", "-f", "gdigrab", "-framerate", str(fps)]
    if region:
        cmd += ["-offset_x", region.split("+")[1].split("+")[0],
                "-offset_y", region.split("+")[1].split("+")[1],
                "-video_size", region.split("+")[0]]
        cmd += ["-i", "desktop"]
    else:
        cmd += ["-i", "desktop"]
    cmd += ["-t", str(dur), "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", out]
    return cmd


def main():
    ap = argparse.ArgumentParser(description="GP - Grabador de Pantalla")
    ap.add_argument("--out", default="rec.mp4")
    ap.add_argument("--dur", type=int, default=120, help="recording seconds")
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--interval", type=float, default=2.0,
                    help="foreground-window sample period (s)")
    ap.add_argument("--log", default="gp_log.jsonl")
    ap.add_argument("--region", default=None,
                    help="WxH+x+y (e.g. 1280x720+0+0); omit for full desktop")
    args = ap.parse_args()

    ff = find_ffmpeg()
    out_dir = os.path.dirname(os.path.abspath(args.out)) or "."
    log_path = args.log if os.path.isabs(args.log) else os.path.join(out_dir, args.log)
    cmd = build_ffmpeg_cmd(ff, args.out, args.dur, args.fps, args.region)

    print(f"[gp] ffmpeg: {ff}")
    print(f"[gp] recording {args.dur}s @ {args.fps}fps -> {args.out}")
    print(f"[gp] foreground log -> {log_path} (every {args.interval}s)")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)

    samples = []
    stop = threading.Event()
    t0 = time.time()

    def sampler():
        while not stop.is_set() and (time.time() - t0) < args.dur + 1:
            fg = get_foreground()
            if fg:
                fg["t"] = round(time.time() - t0, 2)
                samples.append(fg)
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(fg, ensure_ascii=False) + "\n")
            time.sleep(args.interval)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()

    try:
        proc.wait(timeout=args.dur + 30)
    except subprocess.TimeoutExpired:
        proc.kill()
    stop.set()
    th.join(timeout=2)

    # ensure log closed / flush
    if os.path.exists(log_path):
        n = sum(1 for _ in open(log_path, encoding="utf-8") if _.strip())
    else:
        n = 0
    size = os.path.getsize(args.out) if os.path.exists(args.out) else 0
    print(f"[gp] done. video={size} bytes, log_samples={n}")


if __name__ == "__main__":
    main()
