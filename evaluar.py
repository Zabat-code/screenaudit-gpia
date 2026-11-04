#!/usr/bin/env python3
"""
PASO 3 - Evaluador de Minutos de Calidad (criterio del arquitecto).

PRIMARY PARAMETER (the architect's priority for the pilot):
  QUALITY MINUTES = total recorded time MINUS the time spent in
  GAME / VIDEO foreground apps (those pause the counter).

SECONDARY (what we built so far): per-segment TRABAJO/OCIO/NEUTRAL
from vision, kept as supporting detail only.

Input : gp_log.jsonl  (foreground-window samples from GP)
        reporte.json  (Paso1 motion, optional, for secondary detail)
        step2.json    (Paso2 vision, optional, for secondary detail)
Output: calidad.json  + calidad.txt (human-readable)

privacy-conscious: only reads OS window title + exe (no screen OCR).

Usage:
  python evaluar.py --log gp_log.jsonl --out calidad.json
  python evaluar.py --log gp_log.jsonl --report reporte.json --step2 step2.json
"""
import argparse
import json
import os
from collections import defaultdict


def load_cfg(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}
    return {}


def is_game(exe, title, cfg):
    games = [g.lower() for g in cfg.get("apps_juego", [])]
    vids = [v.lower() for v in cfg.get("video_keywords", [])]
    e = (exe or "").lower()
    t = (title or "").lower()
    # exact exe match (strong)
    for g in games:
        if g in e:
            return True, f"game_exe={g}"
    # title keyword (weaker): video sites pause too
    for v in vids:
        if v in t:
            return True, f"video_title={v}"
    return False, ""


def is_office(exe, cfg):
    """Secondary reporting: a foreground Office/word-processor app is
    explicitly flagged TRABAJO in the secondary section, per user request
    ('apps de Word/oficina al frente = trabajo, reportar como secundario')."""
    office = [o.lower() for o in cfg.get("apps_oficina", [])]
    e = (exe or "").lower()
    a = (exe or "").lower()
    for o in office:
        if o in e or o in a:
            return True, f"office_exe={o}"
    return False, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True, help="gp_log.jsonl from GP")
    ap.add_argument("--report", default=None)
    ap.add_argument("--step2", default=None)
    ap.add_argument("--out", default="calidad.json")
    ap.add_argument("--cfg", default="config_cliente.json")
    args = ap.parse_args()

    cfg = load_cfg(args.cfg)

    samples = []
    with open(args.log, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except Exception:
                pass
    if not samples:
        print("[evaluar] no samples in log")
        return

    # interval: median gap between samples
    gaps = [samples[i + 1].get("t", 0) - samples[i].get("t", 0)
            for i in range(len(samples) - 1)]
    interval = sum(gaps) / len(gaps) if gaps else 1.0

    t0 = samples[0].get("t", 0.0)
    t_end = samples[-1].get("t", 0.0)
    # each sample covers [t_i, t_i+interval); total covered = span + interval
    total = (t_end - t0) + interval
    if total <= 0:
        total = len(samples) * interval  # fallback
    pause_time = 0.0
    app_time = defaultdict(float)
    paused_samples = 0
    detail = []
    for s in samples:
        dt = interval
        app_time[s.get("app", "?")] += dt
        game, why = is_game(s.get("exe", ""), s.get("title", ""), cfg)
        if game:
            pause_time += dt
            paused_samples += 1
        detail.append((round(s.get("t", 0), 1), s.get("app", "?"),
                       s.get("title", "")[:60], "PAUSE" if game else "calidad", why))

    quality_min = max(0.0, (total - pause_time)) / 60.0
    pause_min = pause_time / 60.0
    total_min = total / 60.0
    quality_pct = round(100 * (total - pause_time) / total, 1) if total else 0.0

    # Secondary: load vision detail if present
    vision = None
    if args.step2 and os.path.exists(args.step2):
        with open(args.step2, encoding="utf-8") as fh:
            vision = json.load(fh)

    out = {
        "total_seconds": round(total, 1),
        "total_minutes": round(total_min, 2),
        "paused_seconds": round(pause_time, 1),
        "paused_minutes": round(pause_min, 2),
        "quality_minutes": round(quality_min, 2),
        "quality_pct": quality_pct,
        "interval_seconds": round(interval, 2),
        "sample_count": len(samples),
        "app_seconds": {k: round(v, 1) for k, v in app_time.items()},
        "vision_secondary": vision.get("label_counts") if vision else None,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    # Human-readable
    lines = []
    lines.append("=" * 64)
    lines.append("EVALUADOR - MINUTOS DE CALIDAD (criterio del arquitecto)")
    lines.append("=" * 64)
    lines.append(f"Total grabado      : {total_min:.2f} min ({int(total)}s)")
    lines.append(f"Pausado (juego/vid): {pause_min:.2f} min ({int(pause_time)}s)")
    lines.append(f"** MINUTOS CALIDAD : {quality_min:.2f} min ({quality_pct}%) **")
    lines.append("-" * 64)
    lines.append("Tiempo por aplicacion (foreground):")
    for app, sec in sorted(app_time.items(), key=lambda x: -x[1]):
        pflag = "  [PAUSA]" if is_game("", app + ".exe", cfg)[0] else ""
        lines.append(f"  {app:20s} {sec/60:6.2f} min{pflag}")
    lines.append("-" * 64)
    lines.append("Secundario (vision Paso2): " + str(out["vision_secondary"]))
    # Secondary: foreground Office apps explicitly flagged TRABAJO
    office_time = defaultdict(float)
    for s in samples:
        off, _ = is_office(s.get("exe", ""), cfg)
        if off:
            office_time[s.get("app", "?")] += interval
    if office_time:
        lines.append("-" * 64)
        lines.append("Secundario - TRABAJO (oficina al frente, reporte):")
        for app, sec in sorted(office_time.items(), key=lambda x: -x[1]):
            lines.append(f"  {app:20s} {sec/60:6.2f} min  [TRABAJO]")
    lines.append("=" * 64)
    txt = "\n".join(lines)
    with open(os.path.splitext(args.out)[0] + ".txt", "w", encoding="utf-8") as fh:
        fh.write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
