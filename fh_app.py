"""Farm Helper — app pequeno (janela, sem prompt de comando).

  - Log correndo (colorido) do bot, botão Iniciar/Parar, modo claro e escuro
  - Aba "Construções": liga/desliga cada rotina (o que estiver desligado o bot pula)
  - Aba "Calibração": calibra pela SUA tela de jogo, mostrando cada etapa, e guarda para sempre

Abrir:  pythonw fh_app.py   (ou o "Farm Helper.exe" gerado por build_exe.bat; ele fica na mesma pasta do bot)
O bot continua sendo o main.py (processo separado, sem janela). Fechar o app para o bot.
"""
from __future__ import annotations

import ctypes
import datetime  # noqa: F401  (o PyInstaller precisa ver estes imports; userconfig.py/outros são carregados da pasta)
import importlib.util
import json
import logging  # noqa: F401
import os
import platform  # noqa: F401
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path
from tkinter import messagebox, ttk

FROZEN = bool(getattr(sys, "frozen", False))
HERE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
os.chdir(HERE)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PORT = 9222
BOT_LOCK_PORT = 47653           # porta presa pelo main.py enquanto ele roda (instância única)
APP_LOCK_PORT = 47654
PREFS = HERE / "app_prefs.json"
STOP_FILE = HERE / "STOP"
MAX_LINES = 3000

PAL = {
    "dark": dict(bg="#0f1419", panel="#171d25", panel2="#202a36", border="#2c3846", fg="#e6edf3", muted="#8b98a8",
                 accent="#34d27b", accent2="#2bb869", accent_fg="#04210f", danger="#ff6b6b", danger2="#e85a5a",
                 warn="#f5c542", info="#6cb2ff", log_bg="#0b0f14", sel="#26456b"),
    "light": dict(bg="#f2f5f8", panel="#ffffff", panel2="#eaeef3", border="#d3dae3", fg="#18202c", muted="#5f6d7e",
                  accent="#1f9d55", accent2="#188447", accent_fg="#ffffff", danger="#d64545", danger2="#bd3636",
                  warn="#a86d00", info="#1d5fbf", log_bg="#ffffff", sel="#cfe3fb"),
}
LOG_HUES = {
    "dark": dict(green="#6ee7a0", blue="#7cb7ff", yellow="#f5c542", cyan="#5fd7e6", magenta="#d78bff", red="#ff6b6b", gray="#8b98a8"),
    "light": dict(green="#1a7f45", blue="#1d5fbf", yellow="#8a6100", cyan="#0b7d8c", magenta="#8a3fc0", red="#c23030", gray="#6b7787"),
}
TAG_HUE = {"FÁBRICA": "magenta", "INVENTÁRIO": "blue", "ANIMAL": "yellow", "MINA": "cyan", "MEL": "yellow", "ÁRVORES": "green",
           "COLHEITA": "green", "PLANTIO": "green", "REGA": "blue", "PLANO": "magenta", "ENERGIA": "yellow",
           "INTERRUPÇÃO": "magenta", "STATUS": "gray", "SESSÃO": "yellow", "ERRO": "red", "ATENÇÃO": "red", "FATAL": "red",
           "AVISO": "yellow", "MOINHO": "cyan", "LATICÍNIO": "cyan", "PADARIA": "cyan", "CONFIG": "blue", "CALIB": "gray",
           "APP": "cyan", "NAVEGADOR": "cyan"}
HIDE_TAGS = ("FERRAMENTA", "SACO", "CALIB", "DRY")      # detalhes: só no bot.log
STATES = {   # estado -> (texto, cor)
    "stopped": ("Parado", "muted"), "starting": ("Iniciando…", "warn"), "running": ("Rodando", "accent"),
    "reconnecting": ("Reconectando…", "warn"), "stopping": ("Parando…", "warn"), "external": ("Rodando (fora do app)", "accent"),
    "calibrating": ("Calibrando…", "info"), "restart": ("Reiniciando em instantes…", "warn"),
}
RE_OUT = re.compile(r"^(\d\d:\d\d:\d\d)  (\S+)\s+(.*)$")
RE_FILE = re.compile(r"^(\d\d:\d\d:\d\d) (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)$")
RE_TAG = re.compile(r"^\[([^\]]+)\]\s*(.*)$", re.S)
RE_STEP = re.compile(r"^\[(\d+)/(\d+)\]")


# ----------------------------------------------------------------------------------------------- utilidades
def load_userconfig():
    spec = importlib.util.spec_from_file_location("userconfig", HERE / "userconfig.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_python() -> list[str]:
    f = HERE / "python.txt"
    if f.exists():
        p = f.read_text("utf-8-sig").strip().strip('"')
        if p and Path(p).exists():
            return [p]
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p and "WindowsApps" not in p:
            return [p]
    if shutil.which("py"):
        return ["py", "-3"]
    if not FROZEN:
        return [sys.executable.replace("pythonw.exe", "python.exe")]
    return []


def child_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH", "_MEIPASS2") and not k.startswith("_PYI")}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", NO_COLOR="1")
    return env


def port_open(port: int = PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3):
            return True
    except Exception:  # noqa: BLE001
        return False


def port_taken(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def system_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:  # noqa: BLE001
        return True


def load_prefs() -> dict:
    try:
        return json.loads(PREFS.read_text("utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}


def save_prefs(p: dict) -> None:
    try:
        PREFS.write_text(json.dumps(p, indent=1), "utf-8")
    except Exception:  # noqa: BLE001
        pass


class Switch(tk.Canvas):
    """Chave liga/desliga desenhada (segue o tema)."""

    def __init__(self, parent, app, value: bool, command=None, bgkey: str = "panel"):
        super().__init__(parent, width=42, height=22, highlightthickness=0, bd=0, cursor="hand2")
        self.app, self.value, self.command, self.bgkey = app, bool(value), command, bgkey
        self.bind("<Button-1>", lambda e: self.toggle())
        self.draw()

    def toggle(self) -> None:
        self.set(not self.value)
        if self.command:
            self.command(self.value)

    def set(self, v: bool) -> None:
        self.value = bool(v)
        self.draw()

    def draw(self) -> None:
        p = self.app.pal
        self.configure(bg=p[self.bgkey])
        self.delete("all")
        col = p["accent"] if self.value else p["border"]
        self.create_oval(1, 1, 21, 21, fill=col, outline=col)
        self.create_oval(21, 1, 41, 21, fill=col, outline=col)
        self.create_rectangle(11, 1, 31, 21, fill=col, outline=col)
        x = 31 if self.value else 11
        self.create_oval(x - 8, 3, x + 8, 19, fill="#ffffff", outline="#ffffff")


# ----------------------------------------------------------------------------------------------- app
class App:
    def __init__(self) -> None:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
        self.root = tk.Tk()
        self.root.title("Farm Helper")
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.root.geometry(f"{int(720 * self.scale)}x{int(580 * self.scale)}")
        self.root.minsize(int(580 * self.scale), int(460 * self.scale))
        ico = HERE / "fh_icon.ico"
        if ico.exists():
            try:
                self.root.iconbitmap(str(ico))
            except Exception:  # noqa: BLE001
                pass
        self.prefs = load_prefs()
        self.theme = self.prefs.get("theme") or ("dark" if system_dark() else "light")
        self.pal = PAL[self.theme]
        self.uc = load_userconfig()
        self.q: queue.Queue = queue.Queue()
        self.py = find_python()
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"
        self.stopping = False
        self.closing = False
        self.stop_deadline = 0.0
        self.restart_at = 0.0
        self.calibrating = False
        self.cal_start = 0.0
        self.tail_pos: int | None = None
        self.last_ext_check = 0.0
        self.themed: list = []
        self.tab = "log"
        self.log_lines = 0
        self._ready = False
        self._build()
        self._ready = True
        self.apply_theme()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self.tick)
        self.root.after(250, self._titlebar)               # barra de título escura (só dá certo com a janela já mapeada)
        self.refresh_calibration_info()
        self.sys_line("App aberto. Pasta: " + str(HERE))
        if not self.py:
            self.sys_line("Python não encontrado. Rode o instalar.bat primeiro.", err=True)
        if self.prefs.get("auto_start", False) and self.py:
            self.root.after(1200, self.start_bot)

    # ------------------------------------------------------------------ construção da interface
    def reg(self, widget, **opts) -> None:
        self.themed.append(lambda p, w=widget, o=opts: w.configure(**{k: p[v] for k, v in o.items()}))

    def reg_fn(self, fn) -> None:
        self.themed.append(fn)

    def font(self, size=10, bold=False, mono=False):
        return ("Consolas" if mono else "Segoe UI", size, "bold" if bold else "normal")

    def button(self, parent, text, cmd, primary=False):
        b = tk.Button(parent, text=text, command=cmd, relief="flat", bd=0, padx=14, pady=6, cursor="hand2", font=self.font(10, primary))
        if primary:
            self.reg(b, bg="accent", fg="accent_fg", activebackground="accent2", activeforeground="accent_fg", disabledforeground="muted")
        else:
            self.reg(b, bg="panel2", fg="fg", activebackground="border", activeforeground="fg", disabledforeground="muted")
        return b

    def label(self, parent, text="", size=10, bold=False, muted=False, bg="bg", **kw):
        lb = tk.Label(parent, text=text, font=self.font(size, bold), anchor="w", **kw)
        self.reg(lb, bg=bg, fg="muted" if muted else "fg")
        return lb

    def _build(self) -> None:
        r = self.root
        self.reg(r, bg="bg")
        # topo
        top = tk.Frame(r, padx=16, pady=10)
        self.reg(top, bg="panel")
        top.pack(fill="x")
        self.logo = tk.Canvas(top, width=26, height=26, highlightthickness=0)
        self.reg(self.logo, bg="panel")
        self.logo.pack(side="left")
        self.label(top, "Farm Helper", 14, True, bg="panel").pack(side="left", padx=(8, 12))
        self.pill = tk.Label(top, text="● Parado", font=self.font(9, True), padx=10, pady=3)
        self.pill.pack(side="left")
        self.theme_btn = tk.Button(top, text="☀", font=self.font(12), relief="flat", bd=0, width=3, cursor="hand2", command=self.toggle_theme)
        self.reg(self.theme_btn, bg="panel2", fg="fg", activebackground="border", activeforeground="fg")
        self.theme_btn.pack(side="right")
        self.start_btn = tk.Button(top, text="Iniciar", relief="flat", bd=0, padx=18, pady=6, cursor="hand2", font=self.font(10, True), command=self.on_start_stop)
        self.start_btn.pack(side="right", padx=8)
        # abas
        tabs = tk.Frame(r, padx=12)
        self.reg(tabs, bg="bg")
        tabs.pack(fill="x", pady=(6, 0))
        self.tab_btn = {}
        for key, txt in (("log", "Log"), ("build", "Construções"), ("calib", "Calibração")):
            b = tk.Label(tabs, text=txt, font=self.font(10, True), padx=14, pady=7, cursor="hand2")
            b.bind("<Button-1>", lambda e, k=key: self.show_tab(k))
            b.pack(side="left")
            self.tab_btn[key] = b
        self.reg_fn(lambda p: self._style_tabs())
        self.sep = tk.Frame(r, height=1)
        self.reg(self.sep, bg="border")
        self.sep.pack(fill="x")
        # rodapé
        foot = tk.Frame(r, padx=16, pady=5)
        self.reg(foot, bg="panel")
        foot.pack(side="bottom", fill="x")
        self.foot_lbl = self.label(foot, "", 9, muted=True, bg="panel")
        self.foot_lbl.pack(side="left")
        # conteúdo
        self.content = tk.Frame(r)
        self.reg(self.content, bg="bg")
        self.content.pack(fill="both", expand=True)
        self.frames = {"log": self._build_log(), "build": self._build_build(), "calib": self._build_calib()}
        self.style = ttk.Style(r)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.show_tab("log")

    def _build_log(self) -> tk.Frame:
        f = tk.Frame(self.content, padx=12, pady=10)
        self.reg(f, bg="bg")
        box = tk.Frame(f, bd=0, highlightthickness=1)
        self.reg(box, bg="log_bg", highlightbackground="border", highlightcolor="border")
        self.log = tk.Text(box, wrap="word", bd=0, padx=10, pady=8, height=6, font=self.font(10, mono=True), state="disabled", cursor="arrow", spacing1=1, spacing3=1)
        self.reg(self.log, bg="log_bg", fg="fg", insertbackground="fg", selectbackground="sel")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        opts = tk.Frame(f)                                   # rodapé primeiro (side=bottom), para nunca ser cortado
        self.reg(opts, bg="bg")
        opts.pack(side="bottom", fill="x", pady=(10, 0))
        bar = tk.Frame(f)
        self.reg(bar, bg="bg")
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        for txt, cmd in (("Limpar", self.clear_log), ("Copiar", self.copy_log), ("Abrir navegador", self.open_browser_clicked), ("bot.log", self.open_botlog)):
            self.button(bar, txt, cmd).pack(side="left", padx=(0, 6))
        box.pack(fill="both", expand=True)
        self.auto_restart = self.prefs.get("auto_restart", True)
        self.auto_start = self.prefs.get("auto_start", False)
        for text, attr in (("Reiniciar sozinho se o bot cair", "auto_restart"), ("Iniciar o bot ao abrir o app", "auto_start")):
            row = tk.Frame(opts, padx=0)
            self.reg(row, bg="bg")
            row.pack(side="left", padx=(0, 18))
            sw = Switch(row, self, getattr(self, attr), lambda v, a=attr: self.set_pref(a, v), bgkey="bg")
            self.reg_fn(lambda p, s=sw: s.draw())
            sw.pack(side="left")
            self.label(row, text, 9).pack(side="left", padx=6)
        return f

    def _build_build(self) -> tk.Frame:
        f = tk.Frame(self.content, padx=12, pady=10)
        self.reg(f, bg="bg")
        self.label(f, "Desmarque o que você NÃO tem na land: o bot pula essas rotinas. Vale na próxima vez que o bot iniciar.", 9, muted=True, wraplength=int(640 * self.scale), justify="left").pack(fill="x")
        bar = tk.Frame(f)
        self.reg(bar, bg="bg")
        bar.pack(fill="x", pady=8)
        self.button(bar, "Marcar tudo", lambda: self.set_all(False)).pack(side="left", padx=(0, 6))
        self.button(bar, "Só o que foi encontrado", lambda: self.set_all(True)).pack(side="left", padx=(0, 6))
        self.saved_lbl = self.label(bar, "", 9, muted=True)
        self.saved_lbl.pack(side="left", padx=8)
        wrap = tk.Frame(f)
        self.reg(wrap, bg="bg")
        wrap.pack(fill="both", expand=True)
        self.b_canvas = tk.Canvas(wrap, highlightthickness=0, bd=0)
        self.reg(self.b_canvas, bg="bg")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.b_canvas.yview)
        self.b_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.b_canvas.pack(side="left", fill="both", expand=True)
        self.b_inner = tk.Frame(self.b_canvas)
        self.reg(self.b_inner, bg="bg")
        self.b_win = self.b_canvas.create_window((0, 0), window=self.b_inner, anchor="nw")
        self.b_inner.bind("<Configure>", lambda e: self.b_canvas.configure(scrollregion=self.b_canvas.bbox("all")))
        self.desc_labels: list[tk.Label] = []
        self.b_canvas.bind("<Configure>", self._on_b_resize)
        self.b_canvas.bind("<Enter>", lambda e: self.b_canvas.bind_all("<MouseWheel>", self._wheel))
        self.b_canvas.bind("<Leave>", lambda e: self.b_canvas.unbind_all("<MouseWheel>"))
        self.switches: dict[str, Switch] = {}
        self.badges: dict[str, tk.Label] = {}
        self.build_rows()
        return f

    def _on_b_resize(self, e) -> None:
        self.b_canvas.itemconfigure(self.b_win, width=e.width)
        wrap = max(160, e.width - 280)                      # a descrição quebra de linha para a chave nunca ser cortada
        for lb in self.desc_labels:
            try:
                lb.configure(wraplength=wrap)
            except tk.TclError:
                pass

    def _wheel(self, e) -> None:
        self.b_canvas.yview_scroll(int(-e.delta / 120) * 2, "units")

    def build_rows(self) -> None:
        for w in self.b_inner.winfo_children():
            w.destroy()
        self.desc_labels = []
        mods = self.uc.load_modules()
        calib = self.uc.load_calibration()
        order = list(dict.fromkeys(c["group"] for c in self.uc.CATALOG))          # grupos na ordem em que aparecem
        cats = sorted(self.uc.CATALOG, key=lambda c: order.index(c["group"]))
        group = None
        for cat in cats:
            if cat["group"] != group:
                group = cat["group"]
                self.label(self.b_inner, group.upper(), 8, True, muted=True).pack(fill="x", pady=(10, 4))
            row = tk.Frame(self.b_inner, padx=12, pady=8)
            self.reg(row, bg="panel")
            row.pack(fill="x", pady=2)
            tk.Label(row, text=cat.get("icon", ""), font=("Segoe UI Emoji", 15), width=3).pack(side="left")
            self.reg(row.winfo_children()[-1], bg="panel", fg="fg")
            mid = tk.Frame(row)
            self.reg(mid, bg="panel")
            mid.pack(side="left", fill="x", expand=True)
            self.label(mid, cat["label"], 10, True, bg="panel").pack(fill="x")
            dl = self.label(mid, cat["desc"], 8, muted=True, bg="panel", wraplength=max(160, self.b_canvas.winfo_width() - 280), justify="left")
            dl.pack(fill="x")
            self.desc_labels.append(dl)
            if not cat["types"]:
                txt, hue = "✔ disponível", "accent"
            elif calib:
                found = any(calib.get("buildings", {}).get(t) for t in cat["types"])
                txt, hue = ("✔ encontrada", "accent") if found else ("não achei na land", "warn")
            else:
                txt, hue = "calibre para detectar", "muted"
            badge = tk.Label(row, text=txt, font=self.font(8, True))
            self.reg(badge, bg="panel", fg=hue)
            badge.pack(side="left", padx=8)
            self.badges[cat["key"]] = badge
            auto = cat.get("auto", True)
            sw = Switch(row, self, mods.get(cat["key"], True) and auto, lambda v, k=cat["key"]: self.save_module(k, v))
            self.reg_fn(lambda p, s=sw: s.draw())
            sw.pack(side="right")
            if not auto:
                sw.set(False)
                sw.unbind("<Button-1>")
                sw.configure(cursor="arrow")
            self.switches[cat["key"]] = sw
        if self._ready:
            self.apply_theme()

    def _build_calib(self) -> tk.Frame:
        f = tk.Frame(self.content, padx=12, pady=10)
        self.reg(f, bg="bg")
        self.cal_info = self.label(f, "", 9, wraplength=int(660 * self.scale), justify="left")
        self.cal_info.pack(fill="x")
        row = tk.Frame(f)
        self.reg(row, bg="bg")
        row.pack(fill="x", pady=10)
        self.cal_btn = self.button(row, "Calibrar agora", self.start_calibration, primary=True)
        self.cal_btn.pack(side="left")
        self.prev_btn = self.button(row, "Ver conferência", self.show_preview)
        self.prev_btn.pack(side="left", padx=6)
        self.cal_state = self.label(row, "", 10, True)
        self.cal_state.pack(side="left", padx=10)
        self.cal_time = self.label(row, "", 9, muted=True)
        self.cal_time.pack(side="right")
        self.pbar = ttk.Progressbar(f, mode="determinate", maximum=100)
        self.pbar.pack(fill="x")
        box = tk.Frame(f, highlightthickness=1)
        self.reg(box, bg="log_bg", highlightbackground="border", highlightcolor="border")
        box.pack(fill="both", expand=True, pady=(10, 0))
        self.cal_log = tk.Text(box, wrap="word", bd=0, padx=10, pady=8, height=6, font=self.font(10, mono=True), state="disabled", cursor="arrow", spacing1=1, spacing3=1)
        self.reg(self.cal_log, bg="log_bg", fg="fg", selectbackground="sel")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.cal_log.yview)
        self.cal_log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cal_log.pack(side="left", fill="both", expand=True)
        self._put(self.cal_log, "", "", "As etapas da calibração aparecem aqui, uma por uma, com a barra de progresso acima.", "dim")
        return f

    # ------------------------------------------------------------------ tema
    def apply_theme(self) -> None:
        self.pal = PAL[self.theme]
        keep = []
        for fn in self.themed:
            try:
                fn(self.pal)
                keep.append(fn)
            except tk.TclError:
                pass                                         # widget destruído
        self.themed = keep
        p = self.pal
        s = self.style if hasattr(self, "style") else None
        if s:
            s.configure("Vertical.TScrollbar", background=p["border"], troughcolor=p["log_bg"], bordercolor=p["log_bg"], arrowcolor=p["muted"], lightcolor=p["border"], darkcolor=p["border"])
            s.map("Vertical.TScrollbar", background=[("active", p["muted"])])
            s.configure("Horizontal.TProgressbar", background=p["accent"], troughcolor=p["panel2"], bordercolor=p["panel2"], lightcolor=p["accent"], darkcolor=p["accent"], thickness=8)
        hues = LOG_HUES[self.theme]
        for t in (self.log, self.cal_log):
            t.tag_configure("time", foreground=p["muted"])
            t.tag_configure("msg", foreground=p["fg"])
            t.tag_configure("err", foreground=hues["red"])
            t.tag_configure("dim", foreground=p["muted"])
            t.tag_configure("ok", foreground=hues["green"], font=self.font(10, True, True))
            t.tag_configure("step", foreground=hues["blue"], font=self.font(10, True, True))
            t.tag_configure("warn", foreground=hues["yellow"])
            for tag, hue in TAG_HUE.items():
                t.tag_configure("t_" + tag, foreground=hues[hue], font=self.font(10, True, True))
            t.tag_configure("t_", foreground=p["fg"], font=self.font(10, True, True))
        self.theme_btn.configure(text="☀" if self.theme == "dark" else "🌙")
        self.draw_logo()
        self.update_ui()
        self._titlebar()

    def _titlebar(self) -> None:
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            val = ctypes.c_int(1 if self.theme == "dark" else 0)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4) == 0:
                    break
        except Exception:  # noqa: BLE001
            pass

    def draw_logo(self) -> None:
        p = self.pal
        c = self.logo
        c.delete("all")
        c.create_oval(1, 1, 25, 25, fill=p["accent"], outline=p["accent"])
        c.create_text(13, 13, text="R", fill=p["accent_fg"], font=("Segoe UI", 12, "bold"))

    def toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        self.set_pref("theme", self.theme)
        self.apply_theme()

    def set_pref(self, key: str, val) -> None:
        setattr(self, key, val) if key in ("auto_restart", "auto_start") else None
        self.prefs[key] = val
        save_prefs(self.prefs)

    def _style_tabs(self) -> None:
        p = self.pal
        for k, b in self.tab_btn.items():
            on = k == self.tab
            b.configure(bg=p["bg"], fg=p["accent"] if on else p["muted"])

    def show_tab(self, key: str) -> None:
        self.tab = key
        for k, fr in self.frames.items():
            fr.pack_forget()
        self.frames[key].pack(fill="both", expand=True)
        self._style_tabs()
        if key == "build":
            self.build_rows()
        if key == "calib":
            self.refresh_calibration_info()

    # ------------------------------------------------------------------ log
    def _put(self, widget: tk.Text, time_s: str, tag: str, text: str, kind: str = "") -> None:
        at_end = widget.yview()[1] >= 0.98
        widget.configure(state="normal")
        if time_s:
            widget.insert("end", time_s + "  ", "time")
        if tag:
            widget.insert("end", f"{tag:<11} ", "t_" + tag if tag in TAG_HUE else "t_")
        widget.insert("end", text + "\n", kind or "msg")
        if widget is self.log:
            self.log_lines += 1
            if self.log_lines > MAX_LINES:
                widget.delete("1.0", "500.0")
                self.log_lines -= 499
        widget.configure(state="disabled")
        if at_end:
            widget.see("end")

    def sys_line(self, text: str, err: bool = False) -> None:
        self._put(self.log, time.strftime("%H:%M:%S"), "ERRO" if err else "APP", text, "err" if err else "msg")

    def handle_bot_line(self, raw: str) -> None:
        raw = raw.rstrip()
        if not raw or any(c in raw for c in "╔║╚═"):
            return
        m = RE_OUT.match(raw)
        if m:
            self._bot_msg(m.group(1), m.group(2), m.group(3))
        elif raw.lstrip().startswith("↳"):
            self._put(self.log, "", "", raw.strip(), "err")
        else:
            self._put(self.log, "", "", raw, "err" if re.search(r"Traceback|Error|Exception", raw) else "dim")

    def _bot_msg(self, t: str, tag: str, text: str) -> None:
        kind = "err" if tag in ("ERRO", "ATENÇÃO", "FATAL") else ("warn" if tag == "AVISO" else "msg")
        self._put(self.log, t, tag, text, kind)
        if tag == "STATUS":
            self.foot_lbl.configure(text=text)
        elif tag == "BOT" and "Iniciado" in text:
            self.stopping = False
            if self.state in ("starting", "reconnecting"):
                self.set_state("running")
        elif tag == "FATAL" and "tentando reconectar" in text and self.state == "running":
            self.set_state("reconnecting")

    def handle_file_line(self, raw: str) -> None:
        m = RE_FILE.match(raw.rstrip())
        if not m or m.group(2) == "DEBUG":
            return
        t, lvl, msg = m.groups()
        mt = RE_TAG.match(msg)
        tag, text = (mt.group(1), mt.group(2)) if mt else ("", msg)
        if lvl in ("ERROR", "CRITICAL"):
            tag = tag if tag in ("ERRO", "ATENÇÃO", "FATAL") else "ERRO"
        elif lvl == "WARNING" and not tag:
            tag = "AVISO"
        if tag in HIDE_TAGS and lvl == "INFO":
            return
        self._bot_msg(t, tag or "BOT", text)

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.log_lines = 0

    def copy_log(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end"))
        self.foot_lbl.configure(text="Log copiado para a área de transferência")

    def open_botlog(self) -> None:
        f = HERE / "bot.log"
        if f.exists():
            os.startfile(str(f))

    # ------------------------------------------------------------------ estado / botões
    def set_state(self, st: str) -> None:
        self.state = st
        self.update_ui()

    def update_ui(self) -> None:
        text, hue = STATES.get(self.state, STATES["stopped"])
        self.pill.configure(text="● " + text, fg=self.pal[hue], bg=self.pal["panel2"])
        running = self.state in ("starting", "running", "reconnecting", "external", "restart")
        if self.state == "stopping" or self.calibrating:
            self.start_btn.configure(text="Aguarde…", state="disabled", bg=self.pal["panel2"], fg=self.pal["muted"], disabledforeground=self.pal["muted"])
        elif running:
            self.start_btn.configure(text="Parar", state="normal", bg=self.pal["danger"], fg="#ffffff", activebackground=self.pal["danger2"], activeforeground="#ffffff")
        else:
            self.start_btn.configure(text="Iniciar", state="normal", bg=self.pal["accent"], fg=self.pal["accent_fg"], activebackground=self.pal["accent2"], activeforeground=self.pal["accent_fg"])
        self.cal_btn.configure(state="disabled" if self.calibrating or self.state == "stopping" else "normal")

    def on_start_stop(self) -> None:
        if self.state in ("starting", "running", "reconnecting", "external", "restart"):
            self.stop_bot()
        elif self.state == "stopped":
            self.start_bot()

    def external_running(self) -> bool:
        return self.proc is None and port_taken(BOT_LOCK_PORT)

    # ------------------------------------------------------------------ navegador
    def ask(self, title: str, msg: str) -> bool:
        """Pergunta ao usuário a partir de uma thread de trabalho (a caixa de diálogo roda na thread da interface)."""
        box = {"ev": threading.Event(), "ans": False}
        self.q.put(("ask", title, msg, box))
        box["ev"].wait()
        return box["ans"]

    def run_hidden(self, cmd: list[str], tag: str, on_line=None) -> int:
        p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=child_env(),
                             creationflags=NO_WINDOW, text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            line = line.rstrip()
            if line:
                self.q.put(("sys", line) if on_line is None else on_line(line))
        return p.wait()

    def ensure_browser(self) -> bool:
        if port_open():
            return True
        ps1 = HERE / "start_browser.ps1"
        if not ps1.exists():
            self.q.put(("sys_err", "start_browser.ps1 não encontrado. Rode o instalar.bat."))
            return False
        self.q.put(("sys", "Abrindo o navegador com a depuração (porta 9222)..."))
        base = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
        rc = self.run_hidden(base, "NAVEGADOR")
        if rc == 1 and not port_open():
            if self.ask("Navegador aberto sem depuração", "O navegador está aberto sem a porta de depuração.\n\nFechar e reabrir agora? (as abas voltam)"):
                rc = self.run_hidden(base + ["-Restart"], "NAVEGADOR")
            else:
                return False
        if not port_open():
            self.q.put(("sys_err", "O navegador não ficou com a depuração ativa. Rode o instalar.bat ou veja o log acima."))
            return False
        self.q.put(("sys", "Navegador pronto."))
        return True

    def open_browser_clicked(self) -> None:
        def work():
            self.ensure_browser()
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ bot
    def start_bot(self) -> None:
        if self.proc or self.state != "stopped" or self.calibrating:
            return
        if not self.py:
            messagebox.showerror("Farm Helper", "Python não encontrado. Rode o instalar.bat primeiro.")
            return
        if self.external_running():
            self.sys_line("Já existe um bot rodando fora do app. Use Parar para encerrá-lo.", err=True)
            return
        self.set_state("starting")
        threading.Thread(target=self._bot_worker, daemon=True).start()

    def _bot_worker(self) -> None:
        try:
            if not self.ensure_browser():
                self.q.put(("exit", -1))
                return
            try:
                STOP_FILE.unlink()
            except FileNotFoundError:
                pass
            self.q.put(("sys", "Iniciando o bot..."))
            self.proc = subprocess.Popen(self.py + ["-u", "main.py"], cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         env=child_env(), creationflags=NO_WINDOW, text=True, encoding="utf-8", errors="replace", bufsize=1)
            for line in self.proc.stdout:
                self.q.put(("line", line))
            self.q.put(("exit", self.proc.wait()))
        except Exception as e:  # noqa: BLE001
            self.q.put(("sys_err", f"Falha ao iniciar o bot: {type(e).__name__}: {e}"))
            self.q.put(("exit", -1))

    def stop_bot(self) -> None:
        try:
            STOP_FILE.write_text("stop", "utf-8")
        except OSError as e:
            self.sys_line(f"Não consegui criar o arquivo STOP: {e}", err=True)
        self.restart_at = 0
        if self.proc:
            self.stopping = True
            self.stop_deadline = time.time() + 90
            self.set_state("stopping")
            self.sys_line("Parando o bot (ele termina o passo atual)...")
        else:                                              # bot externo: o STOP basta; confere até a porta liberar
            self.stopping = True
            self.stop_deadline = time.time() + 90
            self.set_state("stopping")
            self.sys_line("Pedi para o bot (fora do app) parar...")

    # ------------------------------------------------------------------ calibração
    def start_calibration(self) -> None:
        if self.calibrating:
            return
        if not self.py:
            messagebox.showerror("Farm Helper", "Python não encontrado. Rode o instalar.bat primeiro.")
            return
        was_running = self.state in ("starting", "running", "reconnecting", "external", "restart")
        if was_running and not messagebox.askyesno("Calibrar", "O bot será parado para calibrar e iniciado de novo no fim. Continuar?"):
            return
        self.calibrating = True
        self.cal_start = time.time()
        self.pbar.configure(mode="determinate", value=0)
        self.cal_log.configure(state="normal")
        self.cal_log.delete("1.0", "end")
        self.cal_log.configure(state="disabled")
        self.cal_state.configure(text="Calibrando…", fg=self.pal["info"])
        self.set_state("calibrating")
        threading.Thread(target=self._cal_worker, args=(was_running,), daemon=True).start()

    def _cal_put(self, text: str, kind: str = "") -> None:
        self.q.put(("cal", text, kind))

    def _cal_worker(self, was_running: bool) -> None:
        ok = False
        try:
            if was_running:
                self._cal_put("Parando o bot...", "dim")
                try:
                    STOP_FILE.write_text("stop", "utf-8")
                except OSError:
                    pass
                t0 = time.time()
                while time.time() - t0 < 120 and (self.proc is not None or port_taken(BOT_LOCK_PORT)):
                    time.sleep(1)
                if self.proc is not None:
                    self.proc.terminate()
                    time.sleep(2)
                try:
                    STOP_FILE.unlink()
                except FileNotFoundError:
                    pass
            if not port_open():
                self._cal_put("Abrindo o navegador com a depuração...", "dim")
                if not self.ensure_browser():
                    raise RuntimeError("navegador sem depuração")
            self._cal_put("Iniciando o assistente de calibração (leva cerca de 1 minuto)", "dim")
            p = subprocess.Popen(self.py + ["-u", "calibrate_wizard.py"], cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 env=child_env(), creationflags=NO_WINDOW, text=True, encoding="utf-8", errors="replace")
            err = None
            for line in p.stdout:
                line = line.rstrip()
                if line.startswith("  •"):
                    self._cal_put(line[3:].strip())
                elif line.startswith("OK:"):
                    ok = True
                elif line.startswith("ERRO"):
                    err = line
                elif line.strip():
                    self._cal_put(line.strip(), "dim")
            p.wait()
            if not ok:
                raise RuntimeError(err or f"o assistente terminou com código {p.returncode}")
            self._cal_put("✔ Calibração concluída e salva. Não será refeita nas próximas vezes.", "ok")
        except Exception as e:  # noqa: BLE001
            self._cal_put(f"✖ Falhou: {e}", "err")
        finally:
            self.q.put(("cal_done", ok, was_running))

    def refresh_calibration_info(self) -> None:
        c = self.uc.load_calibration()
        if not c:
            self.cal_info.configure(text="Ainda não calibrado. Deixe o jogo aberto e logado no navegador e clique em “Calibrar agora”: o app lê a sua tela (janela, zoom, tamanho da land e onde estão as construções) e guarda tudo.")
        else:
            sysi = c.get("system", {}) or {}
            t = sysi.get("tela", {}) or {}
            jb = sysi.get("janela_navegador", {}) or {}
            self.cal_info.configure(text=f"Calibrado em {c.get('created', '?')} · land {c.get('grid_size', '?')}x{c.get('grid_size', '?')} · tela {t.get('w', '?')}x{t.get('h', '?')} "
                                         f"(escala {t.get('dpr', '?')}) · janela {jb.get('width', '?')}x{jb.get('height', '?')}.\nA calibração fica salva; só refaça se mudar de PC, monitor ou o tamanho da land.")
        self.prev_btn.configure(state="normal" if (HERE / "calibracao_preview.png").exists() else "disabled")

    def show_preview(self) -> None:
        f = HERE / "calibracao_preview.png"
        if not f.exists():
            return
        win = tk.Toplevel(self.root)
        win.title("Conferência da calibração")
        win.configure(bg=self.pal["bg"])
        img = tk.PhotoImage(file=str(f))
        k = max(1, -(-img.width() // int(900 * self.scale)))
        img = img.subsample(k, k)
        lb = tk.Label(win, image=img, bd=0)
        lb.image = img
        lb.pack(padx=10, pady=10)
        tk.Label(win, text="Círculos vermelhos = construções · pontos azuis = terra de plantio. Se estiver tudo no lugar, está calibrado.",
                 bg=self.pal["bg"], fg=self.pal["muted"], font=self.font(9)).pack(pady=(0, 10))

    # ------------------------------------------------------------------ construções
    def save_module(self, key: str, val: bool) -> None:
        mods = self.uc.load_modules()
        mods[key] = bool(val)
        self.uc.save_modules(mods)
        self.saved_lbl.configure(text="✔ salvo (vale na próxima inicialização do bot)")

    def set_all(self, only_found: bool) -> None:
        calib = self.uc.load_calibration() if only_found else None
        mods = {}
        for cat in self.uc.CATALOG:
            auto = cat.get("auto", True)
            if only_found and calib:
                on = any(calib.get("buildings", {}).get(t) for t in cat["types"]) if cat["types"] else True
            else:
                on = True
            mods[cat["key"]] = bool(on and auto)
        self.uc.save_modules(mods)
        for k, sw in self.switches.items():
            sw.set(mods.get(k, False))
        self.saved_lbl.configure(text="✔ salvo (vale na próxima inicialização do bot)")

    # ------------------------------------------------------------------ laço principal
    def tick(self) -> None:
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "line":
                    self.handle_bot_line(item[1])
                elif kind == "sys":
                    self.sys_line(item[1])
                elif kind == "sys_err":
                    self.sys_line(item[1], err=True)
                elif kind == "ask":
                    item[3]["ans"] = messagebox.askyesno(item[1], item[2])
                    item[3]["ev"].set()
                elif kind == "cal":
                    self._cal_line(item[1], item[2])
                elif kind == "cal_done":
                    self._cal_done(item[1], item[2])
                elif kind == "exit":
                    self._bot_exited(item[1])
        except queue.Empty:
            pass
        now = time.time()
        if self.proc and self.stopping and now > self.stop_deadline:
            self.sys_line("O bot não parou a tempo; encerrando à força.", err=True)
            try:
                self.proc.terminate()
            except OSError:
                pass
            self.stop_deadline = now + 3600
        if self.state == "restart" and now >= self.restart_at:
            self.set_state("stopped")
            self.start_bot()
        if now - self.last_ext_check > 3 and not self.proc and not self.calibrating:
            self.last_ext_check = now
            self._check_external()
        if self.state == "external":
            self._tail_botlog()
        if self.calibrating:
            s = int(now - self.cal_start)
            self.cal_time.configure(text=f"{s // 60}:{s % 60:02d}")
        self.root.after(150 if self.state == "external" else 100, self.tick)

    def _check_external(self) -> None:
        ext = port_taken(BOT_LOCK_PORT)
        if ext and self.state in ("stopped",):
            self.set_state("external")
            self.tail_pos = None
            self.sys_line("Detectei um bot rodando fora do app (janela do rodar_sempre.bat). Mostrando o log dele aqui. Parar cria o arquivo STOP.")
        elif not ext and self.state in ("external", "stopping") and not self.proc:
            if self.stopping:
                try:
                    STOP_FILE.unlink()
                except FileNotFoundError:
                    pass
                self.stopping = False
            self.set_state("stopped")
            self.tail_pos = None

    def _tail_botlog(self) -> None:
        f = HERE / "bot.log"
        try:
            size = f.stat().st_size
            if self.tail_pos is None:
                self.tail_pos = max(0, size - 30000)
                skip_first = self.tail_pos > 0
            else:
                skip_first = False
            if size < self.tail_pos:
                self.tail_pos = 0
            if size == self.tail_pos:
                return
            with open(f, "rb") as fh:
                fh.seek(self.tail_pos)
                data = fh.read(min(size - self.tail_pos, 200000))
            self.tail_pos += len(data)
            lines = data.decode("utf-8", errors="replace").split("\n")
            if skip_first and lines:
                lines = lines[1:]
            for ln in lines:
                self.handle_file_line(ln)
        except OSError:
            pass

    def _bot_exited(self, rc: int) -> None:
        was_stopping = self.stopping
        self.proc = None
        self.stopping = False
        stop_requested = was_stopping or STOP_FILE.exists() or self.closing
        try:
            STOP_FILE.unlink()
        except FileNotFoundError:
            pass
        self.set_state("calibrating" if self.calibrating else "stopped")
        self.foot_lbl.configure(text="")
        if self.closing:
            self.root.destroy()
            return
        if self.calibrating or rc == -1:
            return
        if stop_requested:
            self.sys_line("Bot parado.")
        else:
            self.sys_line(f"O bot terminou (código {rc}).", err=rc != 0)
            if self.auto_restart and rc != -1:
                self.restart_at = time.time() + 30
                self.set_state("restart")
                self.sys_line("Reiniciando sozinho em 30 s (desligue em “Reiniciar sozinho se o bot cair”).")

    def _cal_line(self, text: str, kind: str) -> None:
        m = RE_STEP.match(text)
        if m:
            n, tot = int(m.group(1)), int(m.group(2))
            self.pbar.configure(value=n / tot * 100)
            self._put(self.cal_log, time.strftime("%H:%M:%S"), "", text, "step")
        elif kind in ("ok", "err"):
            self._put(self.cal_log, time.strftime("%H:%M:%S"), "", text, kind)
        elif text.startswith("· ") or text.startswith("! "):
            self._put(self.cal_log, "", "", "      " + text, "warn" if text.startswith("!") else "dim")
        else:
            self._put(self.cal_log, time.strftime("%H:%M:%S"), "", text, "dim" if kind == "dim" else "msg")

    def _cal_done(self, ok: bool, was_running: bool) -> None:
        self.calibrating = False
        self.pbar.configure(value=100 if ok else self.pbar["value"])
        self.cal_state.configure(text="✔ Concluído" if ok else "✖ Falhou", fg=self.pal["accent"] if ok else self.pal["danger"])
        self.set_state("stopped")
        self.refresh_calibration_info()
        if ok:
            self.build_rows()
            if was_running:
                self._cal_line("Reiniciando o bot...", "dim")
                self.start_bot()
        elif was_running:
            self._cal_line("O bot continua parado (clique em Iniciar quando quiser).", "dim")

    # ------------------------------------------------------------------ fechar
    def on_close(self) -> None:
        if self.proc:
            if not messagebox.askyesno("Fechar", "O bot está rodando. Parar o bot e fechar o app?"):
                return
            self.closing = True
            self.stop_bot()
            self.root.after(100000, self.root.destroy)
            return
        if self.calibrating and not messagebox.askyesno("Fechar", "A calibração está em andamento. Fechar mesmo assim?"):
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", APP_LOCK_PORT))
    except OSError:
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("Farm Helper", "O app já está aberto (veja a barra de tarefas).")
        return
    App().run()
    lock.close()


if __name__ == "__main__":
    main()
