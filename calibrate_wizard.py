"""Assistente de calibração: lê a SUA tela de jogo e salva calibracao.json (feito uma vez).

Considera: tamanho/posição da janela do navegador, tela e escala (DPI), zoom e posição da câmera, tamanho da land e
onde estão as construções. Também desenha uma imagem de conferência (calibracao_preview.png) com um ponto em cada
construção para você ver se tudo foi encontrado no lugar certo.

Uso:  python calibrate_wizard.py            (o navegador já aberto com a porta de depuração e o jogo logado)
      (a tela de configuração, setup_ui.py, chama este assistente pelo botão "Calibrar")
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.request
from pathlib import Path

import config
import userconfig
from userconfig import CATALOG

HERE = Path(__file__).resolve().parent
PREVIEW = HERE / "calibracao_preview.png"
log = logging.getLogger("fh")


def port_open(port: int = 9222) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=4) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _preview(g, objs: list[dict]) -> None:
    from io import BytesIO

    from PIL import Image, ImageDraw

    im = Image.open(BytesIO(g.page.screenshot())).convert("RGB")
    d = ImageDraw.Draw(im)
    labels = {t: c["label"] for c in CATALOG for t in c["types"]}
    seen = set()
    for o in objs:
        if o["type"] in ("soil", "tree", "seed_sack"):
            continue
        x, y = g.grid.anchor(o)
        d.ellipse((x - 7, y - 7, x + 7, y + 7), outline=(255, 60, 60), width=3)
        name = labels.get(o["type"], o["type"])
        if (o["type"], o["x"], o["y"]) not in seen:
            seen.add((o["type"], o["x"], o["y"]))
            d.text((x + 10, y - 6), name, fill=(255, 255, 0))
    G = g._grid_size()                                     # contorno da land reconhecida + tile central
    pts = [g.grid.p(-0.5, -0.5), g.grid.p(G - 0.5, -0.5), g.grid.p(G - 0.5, G - 0.5), g.grid.p(-0.5, G - 0.5)]
    d.line(pts + [pts[0]], fill=(255, 255, 255), width=2)
    mid = (G - 1) / 2
    mx, my = g.grid.p(mid, mid)
    d.line((mx - 14, my, mx + 14, my), fill=(255, 0, 255), width=3)
    d.line((mx, my - 14, mx, my + 14), fill=(255, 0, 255), width=3)
    d.text((mx + 16, my - 6), f"centro ({int(mid)},{int(mid)}) · land {G}x{G}", fill=(255, 0, 255))
    soils = [o for o in objs if o["type"] == "soil"]
    for o in soils:
        x, y = g.grid.p(o["x"], o["y"])
        d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(80, 200, 255))
    im.save(PREVIEW)


def run(progress=print, port: int = 9222) -> dict:
    """Calibra e salva. `progress(texto)` recebe mensagens para mostrar ao usuário. Devolve um resumo."""
    from game import Game, enter_game, open_game
    from gamestate import Link

    total = 8
    n = [0]

    def step(text: str) -> None:
        n[0] += 1
        progress(f"[{n[0]}/{total}] {text}")

    class _Fwd(logging.Handler):                       # mensagens internas (câmera, PLAY...) viram sub-linhas do log
        def emit(self, record: logging.LogRecord) -> None:
            msg = record.getMessage()
            tag = msg[msg.find("]") + 1:].strip() if msg.startswith("[") else msg
            progress(("    ! " if record.levelno >= logging.WARNING else "    · ") + tag)

    fhlog = logging.getLogger("fh")
    fwd = _Fwd()
    fhlog.addHandler(fwd)
    fhlog.setLevel(logging.INFO)
    fhlog.propagate = False
    pw = None
    try:
        step("Procurando o navegador com a depuração ativa")
        v = port_open(port)
        if not v:
            raise RuntimeError(f"O navegador não está com a depuração ativa na porta {port}. Use 'Abrir navegador' no app ou o instalar.bat.")
        progress(f"    · navegador: {v.get('Browser', '?')}")
        step("Conectando ao navegador")
        pw, page = open_game(f"http://127.0.0.1:{port}", None, None)
        progress(f"    · janela do jogo: {page.viewport_size or 'tamanho padrão'}")
        link = Link(page)
        step("Abrindo o jogo e esperando o primeiro estado (se pedir, faça login/conecte a carteira na janela do navegador)")
        enter_game(page, link)
        progress(f"    · estado recebido; energia {link.energy:.0f}")
        g = Game(page, link, None, auto_energy=False, dry=False)      # dry=False: seleciona a ferramenta mão e fecha janelas abertas
        step("Ajustando a câmera (zoom e posição) para enquadrar a land")
        g.frame_camera()
        c = g.calib
        step("Medindo a grade do terreno")
        G = g._grid_size()
        cx, cy = g.grid.p((G - 1) / 2, (G - 1) / 2)
        progress(f"    · land {G}x{G} tiles; tile ≈ {c.hw:.1f}×{c.grid.hh:.1f} px; tile central ({(G - 1) // 2},{(G - 1) // 2}) na tela em ({cx:.0f}, {cy:.0f}); vértice ({c.xv:.0f}, {c.yv:.0f})")
        step("Procurando suas construções")
        objs = link.objects()
        counts: dict[str, int] = {}
        for o in objs:
            counts[o["type"]] = counts.get(o["type"], 0) + 1
        found = {cat["key"]: (any(counts.get(t) for t in cat["types"]) if cat["types"] else True) for cat in CATALOG}
        for cat in CATALOG:
            ok = found[cat["key"]]
            qtd = sum(counts.get(t, 0) for t in cat["types"])
            progress(f"    · {'✔' if ok else '✖'} {cat['label']}" + ((f" ({qtd})" if cat["types"] else " (não precisa de construção)") if ok else " — não achei na sua land"))
        step("Salvando a calibração e marcando as construções encontradas")
        g.save_calibration()
        userconfig.save_modules({k: bool(v) for k, v in found.items()})        # marca sozinho o que existe; desmarca o que não existe
        on = [cat["label"] for cat in CATALOG if found[cat["key"]]]
        off = [cat["label"] for cat in CATALOG if not found[cat["key"]]]
        progress("    · ligadas: " + (", ".join(on) or "nenhuma"))
        if off:
            progress("    · desligadas (não existem na sua land): " + ", ".join(off))
        step("Gerando a imagem de conferência")
        _preview(g, objs)
        info = g.system_info()
        t = info.get("tela") or {}
        jb = info.get("janela_navegador") or {}
        progress(f"    · sistema: {info.get('os', '?')}")
        progress(f"    · tela {t.get('w', '?')}x{t.get('h', '?')} (escala {t.get('dpr', '?')}); janela do navegador {jb.get('width', '?')}x{jb.get('height', '?')}")
        return {"ok": True, "grid_size": g._grid_size(), "buildings": counts, "found": found, "system": info,
                "tile": {"hw": c.hw, "hh": c.grid.hh}}
    finally:
        fhlog.removeHandler(fwd)
        try:
            if pw:
                pw.stop()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    try:
        r = run(lambda t: print("  •", t, flush=True))
    except Exception as e:  # noqa: BLE001
        print(f"ERRO: {e}", flush=True)
        return 1
    print("OK: calibração salva em calibracao.json; conferência: calibracao_preview.png", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
