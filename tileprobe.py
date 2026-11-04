"""Leitura dos TILES da land pelo destaque que o jogo desenha sob o mouse.

Quando o mouse passa sobre um tile da land, o jogo desenha 4 tracinhos BRANCOS em volta do centro do tile. Fora da land
não aparece nada. Isso dá medidas exatas, sem depender de cor do céu, cerca ou penhasco:
  - o centro (px) do tile sob o mouse;
  - se um tile existe ou não (para contar quantos tiles a land tem por lado);
  - o tamanho do tile (distância entre centros de tiles distantes).
"""
from __future__ import annotations

import logging
import time

import numpy as np

import autocal

log = logging.getLogger("fh")


def _white(arr: np.ndarray) -> np.ndarray:
    return (arr[..., 0] >= 244) & (arr[..., 1] >= 244) & (arr[..., 2] >= 244)


def free_tiles(g: int, objs: list[dict], margin: int = 1) -> set[tuple[int, int]]:
    """Tiles da land sem prédio/planta/árvore/animal (e com folga): só neles o jogo mostra o destaque do mouse."""
    occ: set[tuple[int, int]] = set()
    for o in objs:
        sx, sy = int(o.get("sx", 1) or 1), int(o.get("sy", 1) or 1)
        for i in range(-sx - margin + 1, sx + margin):
            for j in range(-sy - margin + 1, sy + margin):
                occ.add((o["x"] + i, o["y"] + j))
    return {(x, y) for x in range(-2, g + 3) for y in range(-2, g + 3) if (x, y) not in occ}


def _in_view(px: float, py: float, w: int, h: int) -> bool:
    return 60 <= px <= w - 60 and 150 <= py <= h * 0.80 and not (0.30 * w <= px <= 0.64 * w and py > 0.78 * h)


def measure_land(page, g0: int, objs: list[dict]) -> dict | None:
    """Lê a land pelos tiles: (1) confirma/corrige quantos tiles ela tem por lado (g); (2) mede os centros reais de tiles
    distantes e ajusta a grade exata. Devolve {'g', 'bias', 'rms', 'n'} ou None se o jogo não mostrou destaque (sem leitura)."""
    w, h = page.viewport_size["width"], page.viewport_size["height"]
    P = Prober(page)
    arr = autocal.grab(page)
    # ---- 1) tamanho da land: tile livre na borda da frente EXISTE e o vizinho para fora NÃO existe (nos dois eixos)
    seen_any = False
    g_ok = None
    for g in (g0, g0 + 1, g0 - 1, g0 + 2, g0 - 2, g0 + 3):
        if g < 4:
            continue
        c = autocal.calibrate_array(arr, g)
        free = free_tiles(g + 2, objs)
        verdicts = []
        for axis in ("x", "y"):
            cand = [(g - 1, k) if axis == "x" else (k, g - 1) for k in range(1, g - 1)]
            cand = [t for t in cand if t in free and _in_view(*c.grid.p(*t), w, h)]
            if not cand:
                continue
            t_in = max(cand, key=lambda t: t[1] if axis == "x" else t[0])          # o mais perto do canto da frente
            t_out = (g, t_in[1]) if axis == "x" else (t_in[0], g)
            p_in = P.probe(*c.grid.p(*t_in), c.hw, c.grid.hh)
            p_out = P.probe(*c.grid.p(*t_out), c.hw, c.grid.hh)
            seen_any = seen_any or p_in is not None
            verdicts.append((p_in is not None, p_out is None))
            log.info("[CALIB] land %dx%d? borda %s: tile (%d,%d) %s; vizinho de fora (%d,%d) %s", g, g, axis, *t_in,
                     "existe" if p_in else "SEM destaque", *t_out, "não existe" if p_out is None else "EXISTE")
        if verdicts and all(a and b for a, b in verdicts):
            g_ok = g
            break
    if g_ok is None:
        return None
    g = g_ok
    c = autocal.calibrate_array(arr, g)
    # ---- 2) grade exata: centros reais de tiles livres bem espalhados
    free = free_tiles(g, objs)
    inland = [t for t in free if 1 <= t[0] <= g - 2 and 1 <= t[1] <= g - 2 and _in_view(*c.grid.p(*t), w, h)]
    if len(inland) < 4:
        return {"g": g, "bias": None, "rms": None, "n": 0}
    picks = []
    for key in (lambda t: t[0] - t[1], lambda t: -(t[0] - t[1]), lambda t: t[0] + t[1], lambda t: -(t[0] + t[1])):
        t = max(inland, key=key)
        if t not in picks:
            picks.append(t)
    meas = []
    for t in picks:
        pr = P.probe(*c.grid.p(*t), c.hw, c.grid.hh)
        if pr:
            meas.append((t, pr))
    if len(meas) < 3:
        return {"g": g, "bias": None, "rms": None, "n": len(meas)}
    tx = np.array([[1.0, t[0] - t[1]] for t, _ in meas])
    ty = np.array([[1.0, t[0] + t[1]] for t, _ in meas])
    (ox, hw), *_ = np.linalg.lstsq(tx, np.array([p[0] for _, p in meas]), rcond=None)
    (oy, hh), *_ = np.linalg.lstsq(ty, np.array([p[1] for _, p in meas]), rcond=None)
    res = [((ox + (t[0] - t[1]) * hw) - p[0]) ** 2 + ((oy + (t[0] + t[1]) * hh) - p[1]) ** 2 for t, p in meas]
    rms = float(np.sqrt(np.mean(res)))
    # bias relativo ao ajuste do V (mesma fórmula de calibrate_array): grade_exata = V + correção
    bias = {"sw": float(hw / c.grid.ax), "sh": float(hh / c.grid.ay),
            "dx": float((ox - c.grid.ox) / c.grid.ax), "dy": float((oy - c.grid.oy) / c.grid.ay)}
    return {"g": g, "bias": bias, "rms": rms, "n": len(meas), "hw": float(hw), "hh": float(hh),
            "dxpx": float(ox - c.grid.ox), "dypx": float(oy - c.grid.oy)}


class Prober:
    """Passa o mouse num ponto da tela e devolve o centro do tile destacado (ou None)."""

    def __init__(self, page, park=(24, 330)):
        self.page = page
        self.park = park
        self.n = 0

    def _shot(self) -> np.ndarray:
        return autocal.grab(self.page)

    def present(self, x: float, y: float, hw: float = 26.0, hh: float = 11.0, min_px: int = 14) -> bool:
        """O destaque do mouse aparece neste tile? Conta pixels brancos NOVOS num anel em volta do centro previsto (onde ficam
        os 4 tracinhos). Aguenta personagens passando por cima ou o ponteiro tapando parte do desenho (o `probe` exige a forma inteira)."""
        w, h = self.page.viewport_size["width"], self.page.viewport_size["height"]
        if not (5 <= x < w - 5 and 5 <= y < h - 5):
            return False
        self.n += 1
        self.page.mouse.move(x, y, steps=3)
        time.sleep(0.32)
        hov = self._shot()
        self.page.mouse.move(*self.park, steps=3)
        time.sleep(0.32)
        b1 = self._shot()
        time.sleep(0.12)
        b2 = self._shot()
        x0, x1 = int(max(0, x - 2.0 * hw)), int(min(w, x + 2.0 * hw))
        y0, y1 = int(max(0, y - 3.0 * hh)), int(min(h, y + 3.0 * hh))
        new = _white(hov[y0:y1, x0:x1]) & ~_white(b1[y0:y1, x0:x1]) & ~_white(b2[y0:y1, x0:x1])
        ys, xs = np.nonzero(new)
        if len(xs) == 0:
            return False
        dx, dy = (xs + x0 - x) / (0.85 * hw), (ys + y0 - y) / (0.85 * hh)
        ring = np.abs(np.sqrt(dx * dx + dy * dy) - 1.0) < 0.45
        return int(ring.sum()) >= min_px

    def probe(self, x: float, y: float, hw: float = 26.0, hh: float = 11.0) -> tuple[float, float] | None:
        """Centro (px) do tile sob (x, y), ou None se não há tile destacado ali (fora da land / sem destaque)."""
        w, h = self.page.viewport_size["width"], self.page.viewport_size["height"]
        if not (5 <= x < w - 5 and 5 <= y < h - 5):
            return None
        self.n += 1
        self.page.mouse.move(x, y, steps=3)
        time.sleep(0.32)
        hov = self._shot()
        self.page.mouse.move(*self.park, steps=3)
        time.sleep(0.32)
        base = self._shot()
        x0, x1 = int(max(0, x - 2.2 * hw)), int(min(w, x + 2.2 * hw))
        y0, y1 = int(max(0, y - 3.2 * hh)), int(min(h, y + 3.2 * hh))
        new = _white(hov[y0:y1, x0:x1]) & ~_white(base[y0:y1, x0:x1])
        ys, xs = np.nonzero(new)
        if len(xs) < 10:
            return None
        # os 4 tracinhos formam uma elipse: largura ~1,6*hw e altura ~1,6*hh; aceita só formas desse tamanho
        # (tira pixels isolados longe do grupo principal usando a mediana)
        mx, my = np.median(xs), np.median(ys)
        keep = (np.abs(xs - mx) <= 1.3 * hw) & (np.abs(ys - my) <= 1.6 * hh)
        xs, ys = xs[keep], ys[keep]
        if len(xs) < 10:
            return None
        bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if not (0.8 * hw <= bw <= 2.0 * hw and 0.5 * hh <= bh <= 2.0 * hh):
            return None
        return (x0 + (xs.max() + xs.min()) / 2.0, y0 + (ys.max() + ys.min()) / 2.0)
