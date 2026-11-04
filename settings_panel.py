"""settings_panel.py - Graphical settings panel (Tkinter, pure Python, lightweight).

Features (per client requirements):
- Bilingual es/en labels.
- Day/night theme. Night uses deep navy + cream palette (low glare, WCAG AA).
- Log purge threshold: editable, default 60 days, INFINITE = 0, no negatives.
- Productive/neutral/unproductive apps and vision thresholds.
- OCIO domain list: each domain on its own row, with typo-resistant autocomplete
  (type "tiktoc" -> suggests "tiktok.com"). Saved to config_cliente.json.
- Vision thresholds tab includes plain-language context for every field.

Usage:  python settings_panel.py
"""

import os
import json
import tkinter as tk
from tkinter import ttk, messagebox, Listbox


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_cliente.json")

# Default OCIO domain catalog (each domain is separate). Used for autocomplete.
OCIO_DOMAINS = [
    "tiktok.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "reddit.com", "netflix.com", "twitch.tv", "discord.com", "pinterest.com",
    "snapchat.com", "spotify.com", "youtube.com", "primevideo.com", "disneyplus.com",
    "hbomax.com", "paramountplus.com", "peacocktv.com", "vimeo.com", "9gag.com",
    "buzzfeed.com", "tumblr.com", "mercadolibre.com", "amazon.com", "ebay.com",
    "espn.com", "ign.com", "roblox.com", "steamcommunity.com", "epicgames.com",
    "soundcloud.com", "deezer.com", "music.apple.com", "tinder.com", "badoo.com",
]

TXT = {
    "title": "Ajustes / Settings",
    "language": "Idioma / Language",
    "theme": "Tema / Theme",
    "day": "Dia / Day",
    "night": "Noche / Night",
    "log_limit": "Borrado de logs (dias) / Log purge (days)",
    "log_hint": "0 = infinito / 0 = infinite  |  sin negativos / no negatives",
    "productivas": "Apps productivas / Productive apps",
    "neutras": "Apps neutras / Neutral apps",
    "improductivas": "Apps improductivas / Unproductive apps",
    "dominios": "Dominios de ocio / Leisure domains",
    "dom_search": "Buscar o escribir dominio / Search or type domain",
    "dom_suggest": "Sugerencias (anti-tipeo) / Suggestions (typo-proof)",
    "dom_current": "Dominios ocio activos / Active leisure domains",
    "dom_add": "Agregar / Add",
    "dom_del": "Quitar / Remove",
    "dom_hint": "Escribe parte del dominio. Si tecleas mal (ej. 'tiktoc') se sugiere el correcto.",
    "umbrales": "Umbrales de vision / Vision thresholds",
    "umbrales_info": "Estos valores deciden como la IA etiqueta TRABAJO / NEUTRAL / OCIO al analizar el video.",
    "motion_active": "Movimiento activo / Active motion",
    "motion_active_info": "Movimiento minimo (px/frame) para contar como actividad. Mas bajo = mas sensible.",
    "motion_busy": "Movimiento intenso / Busy motion",
    "motion_busy_info": "Movimiento muy alto (video, juego). Superarlo tiende a marcar OCIO.",
    "edge_grid": "Rejilla UI / UI grid edge",
    "edge_grid_info": "Densidad de ventanas/iconos en pantalla. Mas alto = mas ventanas = TRABAJO.",
    "typing_var": "Varianza tecleo / Typing variance",
    "typing_var_info": "Variacion del ritmo de tecleo. La detecta como escritura activa = TRABAJO.",
    "save": "Guardar / Save",
    "saved": "Configuracion guardada / Settings saved",
    "invalid": "Numero invalido / Invalid number",
    "neg": "Sin negativos / No negatives",
}


DEFAULTS = {
    "log_limit_days": 60,
    "language": "es-en",
    "theme": "day",
    "apps_productivas": ["excel", "code", "notepad", "notion", "word"],
    "apps_neutras": ["youtube_musica", "navegador_general"],
    "apps_improductivas": ["tiktok", "facebook", "instagram"],
    "dominios_ocio": list(OCIO_DOMAINS),
    "umbrales": {"motion_active": 1.5, "motion_busy": 6.0, "edge_grid": 0.02, "typing_var": 20.0},
}


def load():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULTS))


def save(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def levenshtein(a, b):
    """Edit distance between two strings (typo detection)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class Panel:
    def __init__(self, root):
        self.root = root
        self.cfg = load()
        self.root.title(TXT["title"])
        self.root.geometry("640x720")
        self.style = ttk.Style()
        self.build_palette()
        self.apply_theme()
        self.build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_palette(self):
        # Color science (WCAG AA): deep navy + cream for night, cream/blue for day.
        self.pal = {
            "night": {
                "bg": "#15233b", "surface": "#1e3050", "fg": "#f3e9d2",
                "muted": "#c9b896", "accent": "#e0b97d", "border": "#33486e",
                "select": "#2a4570",
            },
            "day": {
                "bg": "#f4f1ea", "surface": "#ffffff", "fg": "#2a2a2a",
                "muted": "#6b6256", "accent": "#3b6ea5", "border": "#d8d2c4",
                "select": "#dce6f2",
            },
        }

    def apply_theme(self):
        night = self.cfg.get("theme", "day") == "night"
        p = self.pal["night"] if night else self.pal["day"]
        self.root.configure(bg=p["bg"])
        self.style.theme_use("default")
        # base widgets
        self.style.configure("TLabel", background=p["bg"], foreground=p["fg"])
        self.style.configure("TFrame", background=p["bg"])
        self.style.configure("TButton", background=p["surface"], foreground=p["fg"])
        self.style.configure("TEntry", fieldbackground=p["surface"], foreground=p["fg"])
        self.style.configure("TCombobox", fieldbackground=p["surface"], foreground=p["fg"])
        # Notebook tabs: inactive vs selected MUST differ in BOTH bg and fg
        # so the active tab name never blends into its own highlight.
        self.style.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
        sel_fg = p["bg"] if night else "#ffffff"          # dark text on lit tab (night) / white on lit tab (day)
        sel_bg = p["accent"]                              # lit tab uses accent (gold/blue)
        self.style.map("TNotebook.Tab",
                       background=[("selected", sel_bg), ("!selected", p["bg"])],
                       foreground=[("selected", sel_fg), ("!selected", p["fg"])],
                       bordercolor=[("selected", p["accent"]), ("!selected", p["border"])],
                       lightcolor=[("selected", p["accent"]), ("!selected", p["bg"])])
        # Combobox popup readability
        self.style.map("TCombobox", fieldbackground=[("readonly", p["surface"])])
        self.p = p

    def build(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.build_general(nb)
        self.build_apps(nb)
        self.build_domains(nb)
        self.build_vision(nb)

        ttk.Button(self.root, text=TXT["save"], command=self.do_save).pack(pady=10)

    def build_general(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="General")
        ttk.Label(f, text=TXT["language"]).pack(anchor="w", padx=8, pady=(8, 0))
        self.lang = tk.StringVar(value=self.cfg.get("language", "es-en"))
        ttk.Combobox(f, textvariable=self.lang, values=["es-en", "en", "es"], state="readonly").pack(anchor="w", padx=8)
        ttk.Label(f, text=TXT["theme"]).pack(anchor="w", padx=8, pady=(8, 0))
        self.theme = tk.StringVar(value=TXT["night"] if self.cfg.get("theme") == "night" else TXT["day"])
        ttk.Combobox(f, textvariable=self.theme, values=[TXT["day"], TXT["night"]], state="readonly").pack(anchor="w", padx=8)

        ttk.Label(f, text=TXT["log_limit"]).pack(anchor="w", padx=8, pady=(12, 0))
        self.limit = tk.StringVar(value=str(self.cfg.get("log_limit_days", 60)))
        ttk.Entry(f, textvariable=self.limit, width=12).pack(anchor="w", padx=8)
        ttk.Label(f, text=TXT["log_hint"], foreground=self.p["muted"], font=("TkDefaultFont", 8)).pack(anchor="w", padx=8)

    def build_apps(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="Apps")
        ttk.Label(f, text=TXT["productivas"]).pack(anchor="w", padx=8, pady=(8, 0))
        self.prod = tk.Text(f, height=4, bg=self.p["surface"], fg=self.p["fg"], insertbackground=self.p["fg"])
        self.prod.insert("1.0", "\n".join(self.cfg.get("apps_productivas", [])))
        self.prod.pack(fill="x", padx=8)
        ttk.Label(f, text=TXT["neutras"]).pack(anchor="w", padx=8, pady=(8, 0))
        self.neut = tk.Text(f, height=3, bg=self.p["surface"], fg=self.p["fg"], insertbackground=self.p["fg"])
        self.neut.insert("1.0", "\n".join(self.cfg.get("apps_neutras", [])))
        self.neut.pack(fill="x", padx=8)
        ttk.Label(f, text=TXT["improductivas"]).pack(anchor="w", padx=8, pady=(8, 0))
        self.imp = tk.Text(f, height=3, bg=self.p["surface"], fg=self.p["fg"], insertbackground=self.p["fg"])
        self.imp.insert("1.0", "\n".join(self.cfg.get("apps_improductivas", [])))
        self.imp.pack(fill="x", padx=8)

    def build_domains(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="Dominios")
        ttk.Label(f, text=TXT["dominios"]).pack(anchor="w", padx=8, pady=(8, 0))
        ttk.Label(f, text=TXT["dom_hint"], foreground=self.p["muted"], font=("TkDefaultFont", 8)).pack(anchor="w", padx=8)

        self.dom_current = list(self.cfg.get("dominios_ocio", []))

        # search entry
        self.dom_search = tk.StringVar()
        ent = ttk.Entry(f, textvariable=self.dom_search, width=40)
        ent.pack(anchor="w", padx=8, pady=(4, 0))
        ent.bind("<KeyRelease>", self.on_dom_type)
        ent.bind("<Return>", self.on_dom_add)

        # suggestions list (typo-proof)
        ttk.Label(f, text=TXT["dom_suggest"]).pack(anchor="w", padx=8, pady=(6, 0))
        self.dom_suggest_lb = Listbox(f, height=8, bg=self.p["surface"], fg=self.p["fg"],
                                      selectbackground=self.p["select"], exportselection=False)
        self.dom_suggest_lb.pack(fill="x", padx=8)
        self.dom_suggest_lb.bind("<Double-1>", self.on_dom_add)

        # current domains list
        ttk.Label(f, text=TXT["dom_current"]).pack(anchor="w", padx=8, pady=(6, 0))
        self.dom_current_lb = Listbox(f, height=10, bg=self.p["surface"], fg=self.p["fg"],
                                      selectbackground=self.p["select"], exportselection=False)
        self.dom_current_lb.pack(fill="both", expand=True, padx=8)
        self.refresh_dom_current()

        bf = ttk.Frame(f)
        bf.pack(anchor="w", padx=8, pady=4)
        ttk.Button(bf, text=TXT["dom_add"], command=self.on_dom_add).pack(side="left", padx=2)
        ttk.Button(bf, text=TXT["dom_del"], command=self.on_dom_del).pack(side="left", padx=2)

        self.refresh_dom_suggest("")

    def refresh_dom_current(self):
        self.dom_current_lb.delete(0, "end")
        for d in self.dom_current:
            self.dom_current_lb.insert("end", d)

    def refresh_dom_suggest(self, text):
        self.dom_suggest_lb.delete(0, "end")
        t = text.strip().lower()
        if not t:
            for d in OCIO_DOMAINS[:20]:
                self.dom_suggest_lb.insert("end", d)
            return
        exact = [d for d in OCIO_DOMAINS if t in d]
        if exact:
            for d in exact[:20]:
                self.dom_suggest_lb.insert("end", d)
            return
        # typo-proof: fuzzy match within edit distance 2
        fuzzy = [d for d in OCIO_DOMAINS if levenshtein(t, d) <= 2
                 or levenshtein(t, d.replace(".com", "").replace(".tv", "")) <= 2]
        for d in fuzzy[:20]:
            self.dom_suggest_lb.insert("end", d)

    def on_dom_type(self, event=None):
        self.refresh_dom_suggest(self.dom_search.get())

    def on_dom_add(self, event=None):
        # Prefer a selected suggestion (the typo-corrected domain).
        sel = self.dom_suggest_lb.curselection()
        if sel:
            domain = self.dom_suggest_lb.get(sel[0])
        else:
            domain = self.dom_search.get().strip().lower()
            if not domain:
                return
            # if typed text is not an exact catalog domain, snap to best suggestion
            if domain not in OCIO_DOMAINS and self.dom_suggest_lb.size() > 0:
                domain = self.dom_suggest_lb.get(0)
        if domain and domain not in self.dom_current:
            self.dom_current.append(domain)
            self.refresh_dom_current()
        self.dom_search.set("")
        self.refresh_dom_suggest("")

    def on_dom_del(self, event=None):
        sel = self.dom_current_lb.curselection()
        if sel:
            self.dom_current.pop(sel[0])
            self.refresh_dom_current()

    def build_vision(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="Vision")
        ttk.Label(f, text=TXT["umbrales_info"], foreground=self.p["muted"],
                  font=("TkDefaultFont", 9), wraplength=560).pack(anchor="w", padx=8, pady=(8, 4))

        um = self.cfg.get("umbrales", DEFAULTS["umbrales"])
        self.u = {}
        rows = [
            ("motion_active", TXT["motion_active"], TXT["motion_active_info"]),
            ("motion_busy", TXT["motion_busy"], TXT["motion_busy_info"]),
            ("edge_grid", TXT["edge_grid"], TXT["edge_grid_info"]),
            ("typing_var", TXT["typing_var"], TXT["typing_var_info"]),
        ]
        for key, label, info in rows:
            ttk.Label(f, text=label).pack(anchor="w", padx=8, pady=(8, 0))
            v = tk.StringVar(value=str(um.get(key, 0)))
            ttk.Entry(f, textvariable=v, width=12).pack(anchor="w", padx=8)
            ttk.Label(f, text=info, foreground=self.p["muted"], font=("TkDefaultFont", 8),
                      wraplength=560).pack(anchor="w", padx=8)
            self.u[key] = v

    def do_save(self):
        try:
            lim = int(self.limit.get())
        except ValueError:
            messagebox.showerror(TXT["invalid"], TXT["invalid"] + " / " + TXT["log_limit"])
            return
        if lim < 0:
            messagebox.showerror(TXT["neg"], TXT["neg"] + " (0 = infinito)")
            return
        self.cfg["log_limit_days"] = lim
        self.cfg["language"] = self.lang.get()
        self.cfg["theme"] = "night" if self.theme.get() == TXT["night"] else "day"
        self.cfg["apps_productivas"] = [x.strip() for x in self.prod.get("1.0", "end").splitlines() if x.strip()]
        self.cfg["apps_neutras"] = [x.strip() for x in self.neut.get("1.0", "end").splitlines() if x.strip()]
        self.cfg["apps_improductivas"] = [x.strip() for x in self.imp.get("1.0", "end").splitlines() if x.strip()]
        self.cfg["dominios_ocio"] = list(self.dom_current)
        um = {}
        for key, v in self.u.items():
            try:
                um[key] = float(v.get())
            except ValueError:
                um[key] = DEFAULTS["umbrales"][key]
        self.cfg["umbrales"] = um
        save(self.cfg)
        self.apply_theme()
        messagebox.showinfo(TXT["saved"], TXT["saved"])

    def on_close(self):
        self.do_save()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Panel(root)
    root.mainloop()
