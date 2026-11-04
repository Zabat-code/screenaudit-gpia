"""
logger_utils.py - Logging con parametros de error y auto-borrado por antiguedad.

Diseno (libre, sin admin, sin GPU, portable):
- Cada corrida escribe un log en logs/ con timestamp en el nombre.
- Los logs se auto-borran pasado el limite de dias configurado.
- limite_dias = 0 significa INFINITO (no se borra nunca).
- No se aceptan valores negativos (validado al cargar config).
- Formato pensado para analizar errores rapido: nivel, modulo, mensaje, y
  contexto opcional (el "en que fallo").
"""

import os
import re
import json
import time
import glob
import logging
from datetime import datetime, timedelta

DEFAULT_LIMIT_DAYS = 60  # 2 meses por defecto


def load_config(path="config_cliente.json"):
    """Carga config_cliente.json; devuelve dict con defaults si falta."""
    defaults = {
        "log_limit_days": DEFAULT_LIMIT_DAYS,
        "language": "es-en",
        "theme": "day",
        "apps_productivas": ["excel", "code", "notepad", "notion", "word", "chrome_con_trabajo"],
        "apps_neutras": ["youtube_musica", "navegador_general"],
        "apps_improductivas": ["tiktok", "facebook", "instagram"],
        "umbrales": {
            "motion_active": 1.5,
            "motion_busy": 6.0,
            "edge_grid": 0.02,
            "typing_var": 20.0,
        },
    }
    if not os.path.exists(path):
        return defaults
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # merge con defaults para claves faltantes
        for k, v in defaults.items():
            if k not in cfg:
                cfg[k] = v
        return cfg
    except Exception as e:
        # no romper la app si el config esta corrupto
        print(f"[logger_utils] config ilegible ({e}); usando defaults")
        return defaults


def sanitize_limit_days(val):
    """Valida limite de dias: 0 = infinito, positivo = dias, negativo = reject.

    Devuelve entero >= 0. Los negativos se fuerzan a DEFAULT_LIMIT_DAYS.
    """
    try:
        v = int(val)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT_DAYS
    if v < 0:
        # numeros negativos no permitidos: caer a default
        return DEFAULT_LIMIT_DAYS
    return v  # 0 = infinito


class AppLogger:
    def __init__(self, log_dir="logs", limit_days=None, cfg_path="config_cliente.json"):
        self.cfg = load_config(cfg_path)
        if limit_days is None:
            limit_days = sanitize_limit_days(self.cfg.get("log_limit_days", DEFAULT_LIMIT_DAYS))
        else:
            limit_days = sanitize_limit_days(limit_days)
        self.limit_days = limit_days
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, f"run_{ts}.log")
        self.logger = logging.getLogger(f"screenaudit_{ts}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        fh = logging.FileHandler(self.log_path, encoding="utf-8")
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
        )
        fh.setFormatter(fmt)
        self.logger.addHandler(fh)
        # tambien consola
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        self.logger.addHandler(ch)
        self.info(f"Logger iniciado. limite_borrado_dias={self.limit_days} (0=infinito)")

    def debug(self, msg, ctx=None):
        self.logger.debug(self._fmt(msg, ctx))

    def info(self, msg, ctx=None):
        self.logger.info(self._fmt(msg, ctx))

    def warning(self, msg, ctx=None):
        self.logger.warning(self._fmt(msg, ctx))

    def error(self, msg, ctx=None):
        # ctx = donde fallo, para analizar rapido
        self.logger.error(self._fmt(msg, ctx))

    def critical(self, msg, ctx=None):
        self.logger.critical(self._fmt(msg, ctx))

    def _fmt(self, msg, ctx):
        if ctx:
            return f"{msg} || FALLO_EN: {ctx}"
        return msg

    def purge_old(self):
        """Borra logs mas viejos que limit_days. Si limit_days==0 no borra nada."""
        if self.limit_days == 0:
            self.info("Auto-borrado desactivado (limite=0 = infinito).")
            return 0
        cutoff = time.time() - self.limit_days * 86400
        removed = 0
        for fp in glob.glob(os.path.join(self.log_dir, "run_*.log")):
            try:
                if os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
                    removed += 1
            except OSError:
                pass
        if removed:
            self.info(f"Auto-borrado: {removed} log(s) viejos eliminados (>{self.limit_days}d).")
        return removed

    def close(self):
        self.purge_old()
        for h in list(self.logger.handlers):
            try:
                h.close()
                self.logger.removeHandler(h)
            except Exception:
                pass


if __name__ == "__main__":
    # smoke test
    lg = AppLogger(limit_days=0)
    lg.info("prueba ok")
    lg.error("ejemplo de error", ctx="step2_vision:modelo no carga")
    lg.close()
    print("logger_utils smoke OK ->", lg.log_path)
