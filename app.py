#!/usr/bin/env python3
"""
ORQUESTADOR DEL PIPELINE DE MONITOREO (portable, libre, sin admin, sin GPU).
Une: grabacion (ffmpeg gdigrab) -> analisis OpenCV (Paso1) -> vision local (Paso2) -> reporte.
Incluye panel de ajustes grafico (settings_panel.py) y logs con auto-borrado.

Uso:
  python app.py --video sesion.mp4            # analiza un video existente
  python app.py --record 900 --out ses.mp4    # graba y analiza (requiere ffmpeg)
  python app.py --panel                        # abre el panel de ajustes
"""

import argparse, os, subprocess, sys, time, json, glob

HERE = os.path.dirname(os.path.abspath(__file__))

# ffmpeg: primero junto a la app (empaquetado), luego ruta por defecto
_FFMPEG_LOCAL = os.path.join(HERE, "ffmpeg.exe")
FFMPEG = _FFMPEG_LOCAL if os.path.exists(_FFMPEG_LOCAL) else r"E:/captura de pantalla/ffmpeg.exe"


def grabar(sec, out, lg):
    cmd = [FFMPEG, "-hide_banner", "-y", "-f", "gdigrab", "-framerate", "10",
           "-i", "desktop", "-t", str(sec), "-c:v", "libx264", "-preset",
           "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", out]
    lg.info(f"grabando {sec}s -> {out}")
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        lg.info("grabacion completada")
    except subprocess.CalledProcessError as e:
        lg.error("ffmpeg fallo", ctx=f"record:{e.returncode}")
        raise


def analizar(video, seg, fps, keyframes, lg):
    out_dir = os.path.dirname(os.path.abspath(video))
    rep = os.path.join(out_dir, "reporte.json")
    log = os.path.join(out_dir, "capacidades.log")
    lg.info("paso1 inicia", ctx=f"video={video}")
    try:
        subprocess.run([sys.executable, os.path.join(HERE, "analyze_with_log.py"),
                        "--video", video, "--out", rep, "--log", log,
                        "--seg", str(seg), "--fps", str(fps), "--keyframes", str(keyframes)],
                       check=True, capture_output=True, text=True)
        lg.info("paso1 completado", ctx=rep)
    except subprocess.CalledProcessError as e:
        lg.error("paso1 fallo", ctx=f"analyze:{e.returncode}")
        raise
    return rep


def visuar(rep, lg):
    out = os.path.join(os.path.dirname(rep), "step2.json")
    log = os.path.join(os.path.dirname(rep), "step2.log")
    env = dict(os.environ)
    env["HF_HOME"] = os.path.join(HERE, "hf_cache")
    lg.info("paso2 inicia (vision local)")
    try:
        subprocess.run([sys.executable, os.path.join(HERE, "step2_vision.py"),
                        "--report", rep, "--out", out, "--log", log,
                        "--limit", "10", "--device", "cpu", "--dev"],
                       env=env, check=True, capture_output=True, text=True)
        lg.info("paso2 completado", ctx=out)
    except subprocess.CalledProcessError as e:
        lg.error("paso2 fallo", ctx=f"step2:{e.returncode}")
        raise
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", help="video existente a analizar")
    ap.add_argument("--record", type=int, help="segundos a grabar")
    ap.add_argument("--out", default=os.path.join(HERE, "rec_30", "sesion.mp4"), help="salida de grabacion")
    ap.add_argument("--seg", type=float, default=60)
    ap.add_argument("--fps", type=float, default=1)
    ap.add_argument("--keyframes", type=int, default=10)
    ap.add_argument("--panel", action="store_true", help="abre el panel de ajustes")
    args = ap.parse_args()

    if args.panel:
        subprocess.run([sys.executable, os.path.join(HERE, "settings_panel.py")], check=False)
        return

    # logger con auto-borrado segun config_cliente.json (limite dias)
    from logger_utils import AppLogger, load_config
    cfg = load_config(os.path.join(HERE, "config_cliente.json"))
    lg = AppLogger(limit_days=cfg.get("log_limit_days", 60), cfg_path=os.path.join(HERE, "config_cliente.json"))

    t0 = time.time()
    try:
        if args.record:
            grabar(args.record, args.out, lg)
            video = args.out
        elif args.video:
            video = args.video
        else:
            print("Usa --video <mp4> o --record <seg>  |  --panel para ajustes")
            lg.close()
            return

        rep = analizar(video, args.seg, args.fps, args.keyframes, lg)
        out2 = visuar(rep, lg)

        with open(out2, encoding="utf-8") as fh:
            r = json.load(fh)
        print("=" * 70)
        print(f"[app] LISTO en {time.time()-t0:.1f}s")
        print(f"[app] etiquetas: {r['label_counts']}")
        print(f"[app] % TRABAJO estimado: {r['estimated_work_pct']}%")
        for x in r["results"]:
            if "error" in x:
                print(f"  {x['keyframe']} ERROR {x['error']}")
            else:
                print(f"  {x['keyframe']} t={x.get('t',0)}s [{x['label']}] ({x['confianza']}) {x['razon']}")
        print("=" * 70)
        lg.info("pipeline completo sin errores", ctx=f"labels={r['label_counts']}")
    except Exception as e:
        lg.critical("pipeline abortado", ctx=str(e))
        print(f"[app] ERROR: {e}")
    finally:
        lg.close()


if __name__ == "__main__":
    main()
