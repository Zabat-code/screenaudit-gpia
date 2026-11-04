#!/usr/bin/env python3
"""
PASO 2 - Clasificador de keyframes con vision LOCAL en CPU (SmolVLM2, HuggingFace, MIT/free).

Capa 1 (modelo): SmolVLM2 describe y da categoria.
Capa 2 (determinista, NUEVA): filtros por PIXELES (sin OCR, cumple [region]) que
compensan el sesgo del modelo pequeno (500M confunde YouTube/South Park con
"photo editing"). Detecta:
  - FILTRO_YOUTUBE/VIDEO: barra de progreso inferior + grid de miniaturas + caption.
  - FILTRO_NAVEGADOR: franja superior con botones (barra de URL) -> "es navegador".
  - FILTRO_TRABAJO_ALLOWLIST: edge_density alto + patron de lineas regular (grid)
    o caption con app de trabajo -> TRABAJO fuerte.
El cruce decide la etiqueta final y la confianza.

Cumple [region]: NO hace OCR del texto de pantalla; solo forma/color/bordes.
Borra los keyframes al final (privacidad).

Uso:
  HF_HOME=D:/screen_ai/hf_cache python step2_vision.py \
      --report reporte.json --out step2.json --log step2.log
"""
import argparse, json, os, time
from collections import Counter
from PIL import Image
import numpy as np
import cv2
from transformers import AutoProcessor, AutoModelForImageTextToText

MODEL = "HuggingFaceTB/SmolVLM2-500M-Instruct"

OCIO_HINTS = ["youtube", "netflix", "video", "movie", "film", "game", "gaming",
              "facebook", "instagram", "tiktok", "twitch", "stream", "song",
              "music", "watch", "entertainment", "social media", "meme",
              "whatsapp", "messenger", "chat idle", "news", "football", "sports",
              "south park", "cartoon", "anime", "tv", "series", "show", "sitcom",
              "hulu", "disney", "prime video"]
WORK_HINTS = ["excel", "spreadsheet", "word", "document", "code editor", "ide",
              "terminal", "email", "report", "powerpoint", "slides", "erp",
              "crm", "database", "table", "debug", "browser work", "invoice",
              "accounting", "ticket", "task", "notion", "figma", "autocad"]

# Few-shot para empujar al modelo pequeno (no lee texto, solo categoria)
FEWSHOT = (
    "Examples:\n"
    "- A screen with a YouTube video and progress bar at the bottom -> OCIO\n"
    "- A code editor with dark background and text -> TRABAJO\n"
    "- A spreadsheet grid with many cells -> TRABAJO\n"
    "- A fullscreen movie or TV show -> OCIO\n"
)


def classify_hint(text: str):
    c = text.lower()
    ocio = sum(h in c for h in OCIO_HINTS)
    work = sum(h in c for h in WORK_HINTS)
    if ocio > work:
        return "OCIO", work, ocio
    if work > ocio:
        return "TRABAJO", work, ocio
    return "INDEFINIDO", work, ocio


# ---------- CAPA 2: filtros deterministas por pixeles (sin OCR) ----------
def load_gray(path, w=320):
    img = cv2.imread(path)
    if img is None:
        return None
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h = int(g.shape[0] * w / g.shape[1])
    return cv2.resize(g, (w, h))


def filter_youtube(g):
    """Score 0-3: barra de progreso inferior clara + miniaturas a la derecha.
    Mas estricto: la barra debe ser una linea fina clara en los ultimos 4% y
    las miniaturas deben tener bordes definidos (no solo pixeles oscuros)."""
    if g is None:
        return 0
    h, w = g.shape
    score = 0
    # (a) barra de progreso: franja inferior ~ultimos 4%, con linea clara centrada
    bottom = g[int(h * 0.96):, :]
    # linea clara: franja delgada con muchos pixeles >210
    if (bottom > 210).mean() > 0.05:
        score += 1
    # (b) miniaturas: en la mitad derecha, patron de bloques con borde (Canny)
    right = g[:, int(w * 0.62):]
    sx = cv2.Sobel(right, cv2.CV_32F, 1, 0, ksize=3)
    sy = cv2.Sobel(right, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(sx * sx + sy * sy)
    # bordes fuertes en grid => miniaturas
    if (mag > 60).mean() > 0.08:
        score += 1
    # (c) video fullscreen: un rectangulo grande dominando (poco texto, mucho video)
    #     medido como area de pixeles con variacion media-alta pero sin patron de lineas finas
    return score


def filter_browser(g):
    """Devuelve 1 si hay franja superior tipo barra de URL (botones + candado)."""
    if g is None:
        return 0
    h, w = g.shape
    top = g[:int(h * 0.08), :]
    # barra de navegador: franja con varios pixeles claros y algunos muy oscuros (botones)
    if (top > 200).mean() > 0.4 and (top < 60).mean() > 0.05:
        return 1
    return 0


def filter_work_allowlist(g, caption):
    """TRABAJO fuerte si edge alto + patron de lineas regular (grid) o caption de app."""
    if g is None:
        return 0, 0.0
    # edge density
    sx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    sy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(sx * sx + sy * sy)
    edge = float((mag > 40).mean())
    # patron de lineas regulares: muchos bordes tanto horizontales como verticales
    h_edges = float((np.abs(sx) > 40).mean())
    v_edges = float((np.abs(sy) > 40).mean())
    grid = min(h_edges, v_edges)  # ambos altos => grid (excel, tabla, editor)
    score = 0
    if edge > 0.1 and grid > 0.03:
        score += 2  # grid fuerte = app de trabajo (hoja, tabla, IDE)
    elif edge > 0.15:
        score += 1
    c = caption.lower()
    if any(w in c for w in WORK_HINTS):
        score += 1
    return score, edge


def decide(label_model, caption, g, mv_var=0.0, mv_mean=0.0, cfg=None):
    """Cruce capa1 (modelo) + capa2 (filtros) + capa3 (patron movimiento Paso1)
    + capa4 (config del cliente: apps productivas/neutras/improductivas).
    mv_var alto + mv_mean bajo => tecleo/click repetitivo (TRABAJO).
    mv_mean alto y fluido => video/ocio.
    Capa 4: si el caption/contendio coincide con una app del cliente, esa categoria manda.
    """
    c = caption.lower()
    ocio_h = sum(h in c for h in OCIO_HINTS)
    work_h = sum(h in c for h in WORK_HINTS)

    # --- Capa 4: lista del cliente (config_cliente.json) ---
    if cfg:
        prod = [a.lower() for a in cfg.get("apps_productivas", [])]
        neut = [a.lower() for a in cfg.get("apps_neutras", [])]
        imp = [a.lower() for a in cfg.get("apps_improductivas", [])]
        # youtube de fondo (musica) => neutro, no ocio
        for n in neut:
            if n in c or (n == "youtube_musica" and "youtube" in c):
                return "NEUTRAL", "alta", f"capa4:app_neutra='{n}'"
        for p in prod:
            if p in c:
                return "TRABAJO", "alta", f"capa4:app_productiva='{p}'"
        for i in imp:
            if i in c:
                return "OCIO", "alta", f"capa4:app_improductiva='{i}'"

    yt = filter_youtube(g)
    br = filter_browser(g)
    wk, edge = filter_work_allowlist(g, caption)

    # Capa 3: patron de movimiento (click/escritura vs video)
    typing_signal = (mv_var > 20.0) and (mv_mean < 4.0)
    video_signal = (mv_mean > 5.0)  # movimiento global sostenido = video/animacion

    ocio_signal = (yt >= 2) or (br and yt >= 1) or ocio_h >= 2 or video_signal
    work_signal = wk >= 2 or work_h >= 2 or typing_signal

    if ocio_signal and not work_signal:
        return "OCIO", "alta", f"video(yt={yt},nav={br},mv_mean={mv_mean:.2f})+caption_ocio={ocio_h}"
    if work_signal and not ocio_signal:
        return "TRABAJO", "alta", f"grid(wk={wk},edge={edge:.3f})+typing(mv_var={mv_var:.1f})+caption_work={work_h}"
    if ocio_signal and work_signal:
        if typing_signal and not (yt >= 2):
            return "TRABAJO", "media", f"conflicto resuelto por typing(mv_var={mv_var:.1f})"
        return "OCIO", "media", f"conflicto: ocio(yt={yt},nav={br},mv={mv_mean:.2f}) vs work(wk={wk},typing={typing_signal})"
    if label_model in ("TRABAJO", "OCIO"):
        return label_model, "baja", f"modelo={label_model},sin_filtro_fuerte"
    return "INDEFINIDO", "baja", "sin_senal"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--out", default="step2.json")
    ap.add_argument("--log", default="step2.log")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--keep-keyframes", action="store_true",
                     help="NO borrar keyframes (modo dev: los deja para inspeccion)")
    ap.add_argument("--dev", action="store_true",
                     help="modo desarrollo: enfoca en funcionar full sin errores, sin borrar (privacidad off)")
    args = ap.parse_args()

    if args.dev:
        args.keep_keyframes = True  # en dev no borramos, inspeccionamos

    print(f"[step2] cargando {args.model} en {args.device} ...")
    t0 = time.time()
    processor = AutoProcessor.from_pretrained(args.model)
    model = AutoModelForImageTextToText.from_pretrained(args.model).to(args.device).eval()
    print(f"[step2] modelo cargado en {time.time()-t0:.1f}s")

    with open(args.report, encoding="utf-8") as fh:
        rep = json.load(fh)

    # Capa 4: config del cliente (apps productivas/neutras/improductivas, umbrales)
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_cliente.json")
    cfg = None
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, encoding="utf-8") as cfh:
                cfg = json.load(cfh)
        except Exception:
            cfg = None

    kfs_meta = rep.get("keyframes_meta", [])[: args.limit]
    if not kfs_meta:
        # fallback: si el reporte usa 'keyframes' plano
        kfs_meta = [{"path": k, "t": 0.0} for k in rep.get("keyframes", [])[: args.limit]]
    segments = rep.get("segments", [])
    out_dir = os.path.dirname(os.path.abspath(args.report))

    def motion_var_at(t):
        for s in segments:
            if s["start"] <= t < s["end"]:
                return s.get("motion_var_mean", 0.0), s.get("motion_mean", 0.0)
        return 0.0, 0.0

    results = []
    for i, km in enumerate(kfs_meta):
        try:
            kf = km["path"]
            kf_t = km.get("t", 0.0)
            kf_path = kf if os.path.isabs(kf) else os.path.join(out_dir, os.path.basename(kf))
            image = Image.open(kf_path).convert("RGB")
            g = load_gray(kf_path)
            mv_var, mv_mean = motion_var_at(kf_t)
            # Pregunta 1: descripcion + few-shot
            messages = [{
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": FEWSHOT + (
                        "Describe this screen in one short sentence. Then answer "
                        "ONLY with one word: TRABAJO, OCIO, or INDEFINIDO.")},
                ],
            }]
            prompt = processor.apply_chat_template(
                messages, add_generation_prompt=True, interaction=None, tokenize=False)
            inputs = processor(text=prompt, images=[image], return_tensors="pt").to(args.device)
            generated = model.generate(**inputs, max_new_tokens=60)
            caption = processor.decode(generated[0], skip_special_tokens=True)
            if "Assistant:" in caption:
                caption = caption.split("Assistant:")[-1]
            elif "User:" in caption:
                caption = caption.split("User:", 1)[-1]
            caption = caption.strip()
            # Pregunta 2 directa
            messages2 = [{
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": "Work or leisure? Reply with one word: TRABAJO, OCIO, or INDEFINIDO."},
                ],
            }]
            prompt2 = processor.apply_chat_template(
                messages2, add_generation_prompt=True, interaction=None, tokenize=False)
            inputs2 = processor(text=prompt2, images=[image], return_tensors="pt").to(args.device)
            gen2 = model.generate(**inputs2, max_new_tokens=8)
            cat = processor.decode(gen2[0], skip_special_tokens=True)
            if "Assistant:" in cat:
                cat = cat.split("Assistant:")[-1]
            elif "User:" in cat:
                cat = cat.split("User:", 1)[-1]
            cat = cat.strip().upper()
            if "TRABAJO" in cat:
                label_model = "TRABAJO"
            elif "OCIO" in cat:
                label_model = "OCIO"
            else:
                label_model, _, _ = classify_hint(caption)

            label, conf, razon = decide(label_model, caption, g, mv_var, mv_mean, cfg=cfg)
            results.append({
                "keyframe": os.path.basename(kf), "t": kf_t, "caption": caption,
                "label": label, "confianza": conf, "razon": razon,
                "motion_var": round(mv_var, 1), "motion_mean": round(mv_mean, 3),
            })
            print(f"[step2] {i+1}/{len(kfs_meta)} {os.path.basename(kf)} t={kf_t}s -> {label} ({conf})")
        except Exception as e:
            results.append({"keyframe": os.path.basename(km.get("path", "")), "error": str(e)})
            print(f"[step2] {i+1}/{len(kfs_meta)} ERROR {e}")

    # [region]: borrar keyframes tras analisis (cerrar handles y retry)
    if not args.keep_keyframes:
        removed = 0
        for kf in kfs:
            full = kf if os.path.isabs(kf) else os.path.join(out_dir, os.path.basename(kf))
            for _ in range(3):
                try:
                    if os.path.exists(full):
                        os.remove(full)
                        removed += 1
                    break
                except OSError:
                    time.sleep(0.2)
        print(f"[step2] keyframes borrados ({removed}) por privacidad [region]")

    labels = [r.get("label") for r in results if "label" in r]
    cnt = Counter(labels)
    out = {
        "report": os.path.basename(args.report),
        "model": args.model,
        "keyframes_processed": len(results),
        "label_counts": dict(cnt),
        "estimated_work_pct": round(100 * cnt.get("TRABAJO", 0) / max(1, len(labels)), 1),
        "results": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)

    lines = []
    lines.append("=" * 70)
    lines.append("PASO 2 - CLASIFICACION DE KEYFRAMES (SmolVLM2 + filtros deterministas, local/CPU)")
    lines.append(f"report              : {out['report']}")
    lines.append(f"modelo              : {out['model']}")
    lines.append(f"keyframes procesados: {out['keyframes_processed']}")
    lines.append(f"conteo etiquetas    : {out['label_counts']}")
    lines.append(f"% estimado TRABAJO  : {out['estimated_work_pct']}%")
    lines.append("-" * 70)
    for r in results:
        if "error" in r:
            lines.append(f"  {os.path.basename(r['keyframe'])}  ERROR {r['error']}")
        else:
            lines.append(f"  {os.path.basename(r['keyframe'])}  [{r['label']}] ({r['confianza']})")
            lines.append(f"      razon : {r['razon']}")
            lines.append(f"      caption: {r['caption']}")
    lines.append("=" * 70)
    logtext = "\n".join(lines)
    with open(args.log, "w", encoding="utf-8") as fh:
        fh.write(logtext + "\n")
    print(logtext)


if __name__ == "__main__":
    main()
