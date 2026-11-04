"""Calibração: 3 cliques nos cantos do canteiro (os cliques NÃO chegam ao jogo).

Antes: deixe a câmera como vai usar (roda do mouse = zoom, arrastar = mover) com a fazenda inteira
visível, e não mexa mais nela enquanto o bot roda. Rode de novo se mexer na câmera.
"""
import argparse
import json
import time

import config
from game import CALIB_FILE, open_game, setup_logging
from gamestate import Link
from geometry import Grid, bbox, components

CAPTURE_JS = """
() => {
  window.__cal = null;
  if (window.__calHandler) return;
  window.__calOn = false;
  window.__calHandler = (e) => {
    if (!window.__calOn) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.type === 'pointerdown') window.__cal = [e.clientX, e.clientY];
  };
  for (const t of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click'])
    window.addEventListener(t, window.__calHandler, true);
}
"""


def get_click(frame, prompt: str) -> list[float]:
    frame.evaluate(CAPTURE_JS)
    frame.evaluate("() => { window.__cal = null; window.__calOn = true; }")
    print(f"  -> {prompt}")
    while True:
        pt = frame.evaluate("() => window.__cal")
        if pt:
            frame.evaluate("() => { window.__calOn = false; }")
            time.sleep(0.5)
            print(f"     ok ({pt[0]:.0f}, {pt[1]:.0f})")
            return pt
        time.sleep(0.1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://localhost:9222", help="URL CDP ('' abre navegador próprio)")
    ap.add_argument("--profile", default="profile")
    ap.add_argument("--exe", default=None, help="caminho do brave.exe/chrome.exe (modo próprio)")
    args = ap.parse_args()
    setup_logging()
    _, page = open_game(args.cdp or None, args.profile, args.exe)
    link = Link(page)
    print("Aguardando o jogo carregar e o primeiro estado salvo (até ~1 min)...")
    link.refresh(timeout=120)
    input("Entre na fazenda (PLAY), deixe a câmera como vai usar e pressione Enter...")

    soils = {(o["x"], o["y"]) for o in link.objects("soil")}
    field = max(components(soils), key=len)
    x0, y0, x1, y1 = bbox(field)
    print(f"Canteiro detectado: x {x0}..{x1}, y {y0}..{y1} ({len(field)} tiles de terra)")
    frame = link.frame()
    top = get_click(frame, f"Clique no CENTRO do tile do canto de CIMA do canteiro (x={x0}, y={y0})")
    right = get_click(frame, f"Clique no CENTRO do tile do canto da DIREITA (x={x1}, y={y0})")
    left = get_click(frame, f"Clique no CENTRO do tile do canto da ESQUERDA (x={x0}, y={y1})")
    grid = Grid.from_corners(top, right, left, x0, y0, x1, y1)
    CALIB_FILE.write_text(json.dumps({"grid": grid.to_dict(), "field": [x0, y0, x1, y1],
                                      "viewport": config.VIEWPORT}, indent=2), encoding="utf-8")
    print(f"Calibração salva em {CALIB_FILE}. Meia-largura do tile {grid.hw:.1f}px, meia-altura {grid.hh:.1f}px.")
    cx, cy = grid.p((x0 + x1) / 2, (y0 + y1) / 2)
    print(f"Centro do canteiro na tela: ({cx:.0f}, {cy:.0f}) — confira se bate com o espantalho.")
    print("Dica: não mexa no zoom nem arraste a câmera enquanto o bot roda.")


if __name__ == "__main__":
    main()
