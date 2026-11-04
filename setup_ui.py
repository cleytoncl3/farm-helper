"""Tela de configuração do bot (abre no seu navegador padrão; funciona offline, só em 127.0.0.1).

  python setup_ui.py            abre a tela
  python setup_ui.py --no-open  só sobe o servidor

Escolha quais construções/rotinas o bot deve rodar, calibre pela SUA tela de jogo e salve.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import userconfig
from userconfig import CATALOG

HERE = Path(__file__).resolve().parent
UI = HERE / "ui" / "index.html"
PREVIEW = HERE / "calibracao_preview.png"
STATE = {"calibrating": False, "log": [], "result": None, "error": None}
LOCK = threading.Lock()


def _browser_cfg() -> dict:
    f = HERE / "browser.json"
    try:
        return json.loads(f.read_text("utf-8-sig")) if f.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _payload() -> dict:
    calib = userconfig.load_calibration()
    mods = userconfig.load_modules()
    found = {}
    if calib:
        for cat in CATALOG:
            found[cat["key"]] = any(calib.get("buildings", {}).get(t) for t in cat["types"]) if cat["types"] else True
    return {
        "catalog": CATALOG, "modules": mods, "found": found,
        "calibration": None if not calib else {
            "created": calib.get("created"), "grid_size": calib.get("grid_size"),
            "system": calib.get("system", {}), "buildings": calib.get("buildings", {}),
            "hw": calib["camera"].get("hw"), "has_preview": PREVIEW.exists()},
        "browser": _browser_cfg(), "has_user_config": userconfig.USER_FILE.exists(),
    }


def _calibrate_thread() -> None:
    """Roda o assistente num PROCESSO separado (o Playwright é mais estável no thread principal) e repassa as linhas."""
    try:
        p = subprocess.Popen([sys.executable, "-u", str(HERE / "calibrate_wizard.py")], cwd=str(HERE),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = False
        for line in p.stdout:
            line = line.rstrip()
            with LOCK:
                if line.startswith("  •"):
                    STATE["log"].append(line[3:].strip())
                elif line.startswith("ERRO"):
                    STATE["error"] = line
                elif line.startswith("OK:"):
                    ok = True
        p.wait()
        with LOCK:
            if ok:
                STATE["result"] = {"ok": True}
            elif not STATE["error"]:
                STATE["error"] = f"o assistente terminou com código {p.returncode}"
    except Exception as e:  # noqa: BLE001
        with LOCK:
            STATE["error"] = f"{type(e).__name__}: {e}"
    finally:
        with LOCK:
            STATE["calibrating"] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a) -> None:  # silencia o log HTTP
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, UI.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/config":
            self._json(_payload())
        elif path == "/api/status":
            with LOCK:
                self._json({k: STATE[k] for k in ("calibrating", "log", "result", "error")})
        elif path == "/preview.png" and PREVIEW.exists():
            self._send(200, PREVIEW.read_bytes(), "image/png")
        else:
            self._send(404, b"{}")

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            body = {}
        if self.path == "/api/config":
            userconfig.save_modules(body.get("modules", {}))
            self._json({"ok": True})
        elif self.path == "/api/calibrate":
            with LOCK:
                if STATE["calibrating"]:
                    return self._json({"ok": False, "error": "já calibrando"})
                STATE.update(calibrating=True, log=[], result=None, error=None)
            threading.Thread(target=_calibrate_thread, daemon=True).start()
            self._json({"ok": True})
        elif self.path == "/api/open_browser":
            ps = HERE / "start_browser.ps1"
            subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps)] + (["-Restart"] if body.get("restart") else []),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._json({"ok": True})
        elif self.path == "/api/close":
            self._json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.5), self.server.shutdown()), daemon=True).start()
        else:
            self._send(404, b"{}")


def _free_port(start: int = 8765) -> int:
    for p in range(start, start + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("nenhuma porta livre para a tela de configuração")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    port = args.port or _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Tela de configuração: {url}  (feche esta janela ou use o botão 'Concluir' para encerrar)")
    if not args.no_open:
        threading.Thread(target=lambda: (time.sleep(0.6), webbrowser.open(url)), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    print("Configuração encerrada.")


if __name__ == "__main__":
    sys.exit(main())
