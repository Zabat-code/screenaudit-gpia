#!/usr/bin/env python3
"""
Analizador de actividad de pantalla a partir de video GRABADO (post-proceso).
No corre en la PC del cliente: se alimenta del .mp4 que capture-screem ya genero.

Que mide (SIN leer contenido -> cumple legal [region]):
  - movimiento de pixeles entre frames consecutivos (proxy de "la pantalla cambia")
  - densidad de borde / complejidad (proxy de "hay UI/texto vs fondo liso")
  - segmenta el video en ventanas y clasifica actividad vs inactividad
  - extrae keyframes para revision humana

NO distingue "trabajando" de "viendo Netflix" -> eso requiere modelo de vision
(OCR/LLM multimodal). Este modulo deja los keyframes + stats listos para ese paso.

Uso:
  python video_activity_analyzer.py --video grabacion.mp4 --out reporte.json --seg 10 --fps 1
"""
import argparse, json, os, sys, time
import cv2
import numpy as np

# umbrales (por pixel 0-255, sobre frame reducido a 160px de ancho -> 4x mas rapido)
MOTION_ACTIVE = 1.5     # diferencia media >= esto => pantalla se mueve
MOTION_BUSY   = 6.0     # diferencia media >= esto => movimiento intenso
EDGE_ACTIVE   = 0.02    # fraccion de pixeles con borde >= esto => hay UI/texto
PROC_W = 160            # ancho de procesamiento (mitad de 320) -> menos pixeles
PROC_H = 90


def load_and_process(path, sample_fps=1.0, keyframes=5):
    """Un SOLO pase: muestrea frames a sample_fps, calcula features y extrae
    keyframes en el mismo bucle (no re-leer el video). Devuelve frames y paths."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"[ERR] no puedo abrir {path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(src_fps / sample_fps)))
    frames = []
    kf_paths = []
    kf_targets = set(int(total * (i + 0.5) / keyframes) for i in range(keyframes)) if keyframes else set()
    idx = 0
    out_dir = os.path.dirname(os.path.abspath(path))
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        # keyframe en el mismo pase
        if idx in kf_targets:
            ts_kf = idx / src_fps if src_fps else idx * (1.0 / sample_fps)
            pth = os.path.join(out_dir, f"keyframe_{len(kf_paths):02d}.jpg")
            cv2.imwrite(pth, frame)
            kf_paths.append((pth, round(ts_kf, 1)))
        if idx % step == 0:
            g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(g, (PROC_W, PROC_H))
            ts = idx / src_fps if src_fps else idx * (1.0 / sample_fps)
            frames.append((ts, g))
        idx += 1
    cap.release()
    return frames, src_fps, total, kf_paths


def motion_between(a, b):
    """Diferencia absoluta media normalizada 0-255 entre dos frames grises.
    Devuelve (media, varianza) para distinguir animacion continua de transicion unica."""
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(d.mean()), float(d.var())


def edge_density(g):
    """Fraccion de pixeles con borde fuerte (proxy de UI/texto presente)."""
    sx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    sy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(sx * sx + sy * sy)
    return float((mag > 40).mean())


def classify(motion, edge):
    if motion >= MOTION_BUSY:
        return "activo_intenso"
    if motion >= MOTION_ACTIVE:
        return "activo"
    if edge >= EDGE_ACTIVE:
        return "estatico_con_ui"   # pantalla quieta pero con contenido (documento abierto)
    return "inactivo"


def analyze(frames, seg_sec=10):
    feats = []
    for i in range(len(frames)):
        ts, g = frames[i]
        if i > 0:
            m, v = motion_between(frames[i - 1][1], g)
        else:
            m, v = 0.0, 0.0
        e = edge_density(g)
        feats.append({"t": round(ts, 1), "motion": round(m, 3),
                      "motion_var": round(v, 1), "edge": round(e, 4)})
    # segmentar
    segments = []
    if feats:
        tmax = feats[-1]["t"]
        start = 0.0
        while start < tmax:
            end = min(start + seg_sec, tmax)
            seg = [f for f in feats if start <= f["t"] < end]
            if seg:
                mm = np.mean([f["motion"] for f in seg])
                vv = np.mean([f["motion_var"] for f in seg])
                ee = np.mean([f["edge"] for f in seg])
                label = classify(mm, ee)
                segments.append({
                    "start": round(start, 1), "end": round(end, 1),
                    "motion_mean": round(float(mm), 3),
                    "motion_var_mean": round(float(vv), 1),
                    "edge_mean": round(float(ee), 4),
                    "label": label,
                    "working": label in ("activo", "activo_intenso", "estatico_con_ui"),
                })
            start = end
    active = [s for s in segments if s["working"]]
    total_dur = feats[-1]["t"] if feats else 0.0
    active_dur = sum(s["end"] - s["start"] for s in active)
    pct = round(100.0 * active_dur / total_dur, 1) if total_dur else 0.0
    return feats, segments, pct, total_dur


def extract_keyframes(video_path, out_dir, n=5):
    # Ya no se usa en el flujo unificado; se mantiene por compatibilidad.
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        cap.release()
        return []
    picks = [int(total * (i + 0.5) / n) for i in range(n)]
    saved = []
    idx = 0
    for p in picks:
        while idx < p:
            ok, _ = cap.read()
            if not ok:
                break
            idx += 1
        ok, frame = cap.read()
        if ok:
            pth = os.path.join(out_dir, f"keyframe_{len(saved):02d}.jpg")
            cv2.imwrite(pth, frame)
            saved.append(pth)
        idx = p + 1
    cap.release()
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default="reporte.json")
    ap.add_argument("--seg", type=float, default=10.0)
    ap.add_argument("--fps", type=float, default=1.0, help="muestreo de frames por segundo")
    ap.add_argument("--keyframes", type=int, default=5)
    args = ap.parse_args()

    t0 = time.time()
    frames, src_fps, total, kf = load_and_process(args.video, args.fps, args.keyframes)
    feats, segments, pct, dur = analyze(frames, args.seg)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    # si por alguna razon no se extrajeron en el pase, extraer aparte (fallback)
    if not kf:
        kf = extract_keyframes(args.video, out_dir, args.keyframes) if args.keyframes else []

    report = {
        "video": os.path.abspath(args.video),
        "src_fps": round(src_fps, 2),
        "frames_analyzed": len(frames),
        "duration_sec": round(dur, 1),
        "active_pct": pct,
        "active_label": "TRABAJANDO" if pct >= 50 else "INACTIVO_BAJO",
        "segments": segments,
        "keyframes": kf,
        "elapsed_sec": round(time.time() - t0, 2),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(json.dumps({k: report[k] for k in ("video", "duration_sec", "active_pct", "active_label", "segments")}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
