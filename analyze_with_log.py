#!/usr/bin/env python3
"""
Analiza un video grabado y escribe un LOG de capacidades:
  - que SENIALES puede captar (movimiento, complejidad UI, keyframes)
  - que NO puede captar (contenido textual, que app es, ocio vs trabajo real)

Uso:
  python analyze_with_log.py --video sesion_30min.mp4 --out reporte.json --log capacidades.log
"""
import argparse, json, os, time
import video_activity_analyzer as V

CAP_CAN = [
    "movimiento de pixeles entre frames (proxy de actividad visual)",
    "densidad de bordes/complejidad de la imagen (proxy de UI/texto presente)",
    "segmentacion temporal en ventanas de N segundos",
    "extraccion de keyframes para revision humana",
    "porcentaje de tiempo con pantalla activa",
]
CAP_NO = [
    "LEER el texto de la pantalla (OCR) -> no se hace, cumple legal [region]",
    "IDENTIFICAR que aplicacion esta abierta por nombre/titulo",
    "DISTINGUIR trabajo real de ocio (YouTube/Netflix tambien mueve pixeles)",
    "CAPTAR audio del empleado como contenido (solo el grabador lo graba, no este analizador)",
    "DETECTAR mouse/teclas reales (eso era el monitor en vivo, descartado para el cliente)",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default="reporte.json")
    ap.add_argument("--log", default="capacidades.log")
    ap.add_argument("--seg", type=float, default=60.0)
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--keyframes", type=int, default=10)
    args = ap.parse_args()

    t0 = time.time()
    frames, src_fps, total, kf = V.load_and_process(args.video, args.fps, args.keyframes)
    feats, segments, pct, dur = V.analyze(frames, args.seg)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if not kf:
        kf = V.extract_keyframes(args.video, out_dir, args.keyframes) if args.keyframes else []

    report = {
        "video": os.path.basename(args.video),   # anonimizado: solo nombre, no ruta con usuario
        "src_fps": round(src_fps, 2),
        "frames_analyzed": len(frames),
        "duration_sec": round(dur, 1),
        "active_pct": pct,
        "active_label": "TRABAJANDO" if pct >= 50 else "INACTIVO_BAJO",
        "segments": segments,
        "keyframes": kf,   # rutas completas para que Paso2 las localice (el LOG si anonimiza)
        "keyframes_meta": [{"path": p, "t": t} for p, t in kf],  # timestamp para cruce con segmentos
        "elapsed_sec": round(time.time() - t0, 2),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    # ---- log de capacidades ----
    lines = []
    lines.append("=" * 70)
    lines.append(f"ANALISIS DE ACTIVIDAD DE PANTALLA - LOG DE CAPACIDADES")
    lines.append(f"video     : {report['video']}  (ruta omitida por privacidad [region])")
    lines.append(f"duracion  : {report['duration_sec']} s  ({report['duration_sec']/60:.1f} min)")
    lines.append(f"frames    : {report['frames_analyzed']} muestreados a {args.fps} fps")
    lines.append(f"segmentos : ventanas de {args.seg:.0f}s")
    lines.append(f"TIEMPO CON PANTALLA ACTIVA: {pct}%  -> {report['active_label']}")
    lines.append("  NOTA HONESTA: este % es % de pantalla NO NEGRA, NO % de trabajo real.")
    lines.append("  Para distinguir trabajo de ocio usa el Paso 2 (vision local).")
    lines.append("-" * 70)
    lines.append("[SENIALES QUE SI PUEDO CAPTAR]")
    for c in CAP_CAN:
        lines.append(f"  + {c}")
    lines.append("-" * 70)
    lines.append("[SENIALES QUE NO PUEDO CAPTAR (limite honesto)]")
    for c in CAP_NO:
        lines.append(f"  - {c}")
    lines.append("-" * 70)
    lines.append("[DESGLOSE POR SEGMENTO]")
    for s in segments:
        lines.append(f"  {s['start']:6.1f}-{s['end']:6.1f}s  motion={s['motion_mean']:6.2f}  "
                     f"edge={s['edge_mean']:.4f}  {s['label']:16s} {'TRABAJA' if s['working'] else 'NO'}")
    lines.append("-" * 70)
    lines.append(f"keyframes para revision humana: {len(kf)} archivos JPG")
    lines.append(f"tiempo de analisis: {report['elapsed_sec']}s")
    lines.append("=" * 70)
    logtext = "\n".join(lines)
    with open(args.log, "w", encoding="utf-8") as fh:
        fh.write(logtext + "\n")
    print(logtext)


if __name__ == "__main__":
    main()
