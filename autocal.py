"""Calibração AUTOMÁTICA lendo a tela (sem cliques; ~0,2 s validando, ~0,4 s recalibrando).

A ilha é um losango de GRID x GRID tiles. A borda frontal dela (gramado contra o penhasco marrom) forma um "V":
- o vértice de baixo é o canto de baixo do tile (GRID-1, GRID-1) (o canteiro 9x9 também termina ali);
- a inclinação dos dois braços é meia-altura/meia-largura do tile;
- a largura total da ilha (extremo esquerdo -> direito) vale 2*GRID meias-larguras.
Com isso sai a projeção tile->tela: P(x, y) = (xv + (x-y)*hw, yv - hh + (x+y-2*(GRID-1))*hh).
Funciona com qualquer zoom/posição de câmera, desde que a borda frontal da ilha esteja visível.
Mexeu na câmera (arrastar/zoom)? `ensure()` percebe na hora e recalibra.
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass

import numpy as np

from geometry import Grid

log = logging.getLogger("fh")

LIP = 0.77                        # lábio de grama da borda, em meias-alturas de tile
HW_MIN, HW_MAX = 26.0, 36.0      # meia-largura de tile desejada (px): boa para clicar com folga
SKY = (150, 200, 235)             # cor aproximada do céu (para achar onde arrastar a câmera)


@dataclass
class Calib:
    grid: Grid
    xv: float
    yv: float
    m: float
    hw: float
    frac: float          # fração de pontos de borda que seguem o modelo (para validar depois)
    bias: dict | None = None   # correção medida pelos tiles (tileprobe): {sw, sh, dx, dy} relativa ao ajuste do V


def grab(page) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(page.screenshot())).convert("RGB")).astype(np.int16)


def front_edge_points(arr: np.ndarray, step: int = 2) -> np.ndarray:
    """Pontos (x, y) da borda gramado->penhasco: último pixel de grama com marrom logo abaixo, por coluna."""
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    grass = (g > 170) & (g > r + 30) & (r > 70) & (r < 195) & (b < 125)
    # "marrom" = penhasco OU cerca de madeira da borda (terras maiores/expandidas têm cerca sobre o lábio): tons de terra/madeira
    cliff = (r > 100) & (r < 235) & (g > 50) & (g < 175) & (b < 150) & (r > g + 18) & (g > b + 8)
    h, w = grass.shape
    cs = np.cumsum(cliff, axis=0)
    pts = []
    # A barra de construção (hotbar) fica no centro de baixo e a madeira dela tem a cor do penhasco: não é borda da ilha.
    bar_x0, bar_x1, bar_y = int(w * 0.32), int(w * 0.62), int(h * 0.86)
    for x in range(0, w, step):
        ys = np.nonzero(grass[:, x])[0]
        for y in ys[::-1]:
            if bar_x0 <= x <= bar_x1 and y >= bar_y - 40:
                continue
            if y + 9 < h:
                # o penhasco é ALTO e contínuo: ~8 de 8 px logo abaixo e boa parte dos ~30 px seguintes. Isso descarta a terra
                # marrom do canteiro (tem grama logo abaixo) e cercas/pedras da borda (não chegam a 30 px de marrom).
                far = min(h - 1, y + 32)
                if cs[y + 9, x] - cs[y + 1, x] >= 6 and cs[far, x] - cs[y + 1, x] >= 0.72 * (far - y - 1):
                    pts.append((x, y))
                    break
    return np.array(pts, dtype=float)


def _best(pts, xs, ms, tol):
    x, y = pts[:, 0], pts[:, 1]
    best = (-1, None)
    for xv in xs:
        ax = np.abs(x - xv)
        for m in ms:
            res = y + m * ax
            yv = np.median(res)
            n = int((np.abs(res - yv) < tol).sum())
            if n > best[0]:
                best = (n, (xv, yv, m))
    return best[1]


def fit_v(pts: np.ndarray, width: int):
    """Ajusta y = yv - m*|x - xv| (vértice de baixo = maior y) em dois níveis + RANSAC (ignora ilhas flutuantes)."""
    xv, yv, m = _best(pts, np.arange(width * 0.3, width * 0.7, 8.0), np.arange(0.30, 0.60, 0.02), 4.0)
    xv, yv, m = _best(pts, np.arange(xv - 8, xv + 8.1, 1.0), np.arange(m - 0.02, m + 0.021, 0.002), 3.0)
    x, y = pts[:, 0], pts[:, 1]
    inl = np.abs(y - (yv - m * np.abs(x - xv))) < 3.0
    for _ in range(3):                                   # refino por mínimos quadrados nos inliers
        a = np.column_stack([np.ones(inl.sum()), -np.abs(x[inl] - xv)])
        (yv, m), *_ = np.linalg.lstsq(a, y[inl], rcond=None)
        inl = np.abs(y - (yv - m * np.abs(x - xv))) < 2.5
    return xv, yv, m, inl


def _brown_below(arr: np.ndarray, x: int, y0: float, n: int = 12) -> bool:
    """Há penhasco/cerca (marrom) logo abaixo de (x, y0)? Usado para achar até onde a borda da ilha continua."""
    h, w = arr.shape[:2]
    if x < 0 or x >= w:
        return False
    ya, yb = int(y0) + 4, min(h, int(y0) + 4 + n)
    if yb - ya < 4:
        return False
    r, g, b = arr[ya:yb, x, 0], arr[ya:yb, x, 1], arr[ya:yb, x, 2]
    ok = (r > 100) & (r < 235) & (g > 50) & (g < 175) & (b < 150) & (r > g + 18) & (g > b + 8)
    return float(ok.mean()) >= 0.6


def _tip_extent(arr: np.ndarray, xv: float, yv: float, m: float, x_in: float, direction: int) -> float:
    """Anda sobre o braço do V para fora do último ponto detectado até o penhasco acabar (a ponta da ilha).
    Em terras com cerca na borda os últimos tiles não geram pontos, mas o penhasco continua até a ponta."""
    x, miss = int(x_in), 0
    best = float(x_in)
    for _ in range(400):
        x += direction
        y = yv - m * abs(x - xv)
        if _brown_below(arr, x, y):
            best, miss = float(x), 0
        else:
            miss += 1
            if miss >= 6:
                break
    return best


def calibrate_array(arr: np.ndarray, grid_size: int = 19, bias: dict | None = None) -> Calib:
    pts = front_edge_points(arr)
    if len(pts) < 100:
        raise RuntimeError("não achei a borda da ilha na tela (a fazenda está visível?)")
    xv, yv, m, inl = fit_v(pts, arr.shape[1])
    xs = pts[inl, 0]
    x_left = _tip_extent(arr, xv, yv, m, xs.min(), -1)
    x_right = _tip_extent(arr, xv, yv, m, xs.max(), +1)
    hw = (x_right - x_left) / (2 * grid_size)
    hh = m * hw
    n = grid_size - 1
    ox = xv                                              # (n,n): x-y=0 -> X = xv
    # A borda frontal visível inclui um "lábio" de grama ~0,77 meia-altura abaixo do canto real do chão
    # (medido acertando tiles de canto). Centro de (n,n): Y = yv - hh - LIP*hh.
    oy = (yv - hh - LIP * hh) - 2 * n * hh
    if bias:                                             # correção exata medida nos tiles (cerca/lábio deslocam o V)
        ox += bias["dx"] * hw
        oy += bias["dy"] * hh
        hw, hh = hw * bias["sw"], hh * bias["sh"]
    grid = Grid(ox, oy, hw, hh, -hw, hh)
    return Calib(grid, float(xv), float(yv), float(m), float(hw), float(inl.sum()) / len(pts), bias)


def still_valid(arr: np.ndarray, c: Calib) -> bool:
    """A câmera continua como na calibração? (fração de pontos da borda sobre o mesmo V)"""
    pts = front_edge_points(arr)
    if len(pts) < 100:
        return False
    inl = np.abs(pts[:, 1] - (c.yv - c.m * np.abs(pts[:, 0] - c.xv))) < 3.0
    return inl.mean() >= 0.8 * c.frac


def auto_calibrate(page, grid_size: int = 19, prev: Calib | None = None, bias: dict | None = None) -> Calib:
    """Devolve a calibração atual: reaproveita `prev` se a câmera não mudou, senão recalibra."""
    t0 = time.time()
    arr = grab(page)
    if prev is not None and still_valid(arr, prev):
        return prev
    c = calibrate_array(arr, grid_size, bias)
    log.info("[CALIB] auto: vértice (%.0f, %.0f) hw=%.2f hh=%.2f ajuste=%.0f%% em %.2fs",
             c.xv, c.yv, c.hw, c.grid.hh, 100 * c.frac, time.time() - t0)
    return c


def _sky_point(arr: np.ndarray) -> tuple[int, int] | None:
    """Ponto de céu livre (longe do HUD) para começar o arrasto da câmera. Vale dia (azul claro) e NOITE (azul escuro):
    o céu é a região azulada e lisa (sem desenhos) nas laterais."""
    h, w = arr.shape[:2]
    for y in range(int(h * 0.30), int(h * 0.85), 16):
        for x in list(range(30, 200, 16)) + list(range(w - 200, w - 30, 16)):
            patch = arr[y - 6:y + 7, x - 6:x + 7].reshape(-1, 3)
            r, g, b = patch.mean(axis=0)
            if b > r + 18 and b >= g + 5 and patch.std(axis=0).max() < 14:
                return x, y
    return None


SAFE_YV = 0.87                    # vértice da ilha abaixo disso = ilha atrás da barra do rodapé (abre menus por engano)


def drag_point(arr: np.ndarray, c: Calib, dy: float) -> tuple[int, int] | None:
    """Plano B quando `_sky_point` não acha céu liso (nuvens, noite, ilhas flutuantes): um ponto FORA do losango da ilha
    e longe do HUD (cantos, ícones laterais, barra de baixo), onde o arrasto move a câmera sem clicar em nada."""
    h, w = arr.shape[:2]
    best = None
    for y in range(int(h * 0.20), int(h * 0.80), 20):
        for x in list(range(150, 400, 25)) + list(range(w - 400, w - 150, 25)):
            # dentro do losango da ilha (com folga)? centro do losango ~ (xv, yv - G*hh) ; usa meia-diagonal ~ hw*G
            cy = c.yv - 19 * c.grid.hh                     # centro do losango
            if abs(x - c.xv) / (19 * c.hw + 60) + abs(y - cy) / (19 * c.grid.hh + 40) < 1.0:
                continue
            if y + dy < 30 or y + dy > h - 30:
                continue
            score = abs(x - c.xv)                          # quanto mais longe da ilha, melhor
            if best is None or score > best[0]:
                best = (score, x, y)
    return (best[1], best[2]) if best else None


def find_close_x(arr: np.ndarray) -> tuple[int, int] | None:
    """Botão vermelho de fechar (X) no canto superior direito de qualquer janela do jogo (Friends, loja, mina...).
    O Esc NÃO fecha todas (o Friends ignora). Devolve o centro do botão ou None se não há janela aberta."""
    h, w = arr.shape[:2]
    x0, y1 = int(w * 0.72), int(h * 0.26)
    sub = arr[:y1, x0:]
    r, g, b = sub[..., 0], sub[..., 1], sub[..., 2]
    red = (r > 190) & (g < 90) & (b < 90) & (r - g > 110)
    if red.sum() < 1800:
        return None
    ys, xs = np.nonzero(red)
    if xs.max() - xs.min() < 40 or ys.max() - ys.min() < 40:
        return None
    return int(xs.mean() + x0), int(ys.mean())


def hw_bounds(w: int, h: int, grid_size: int) -> tuple[float, float]:
    """Faixa de meia-largura de tile desejada. Para terrenos maiores (land expandida) o zoom desce para a ilha caber
    na tela: largura 2*G*hw <= 88% da largura; altura ~ 2*G*0.43*hw <= 72% da altura. Para G=19 vale 26..31,9."""
    hi = min(HW_MAX, 0.88 * w / (2 * grid_size), 0.72 * h / (2 * grid_size * 0.43))
    return min(HW_MIN, 0.9 * hi), hi


CENTER_Y = 0.50                   # o tile central da land fica a esta fração da altura (entre o HUD de cima e a barra de baixo)
CENTER_TOL = 18                   # px de tolerância ao centralizar


def island_center(c: Calib, grid_size: int) -> tuple[float, float]:
    """Posição na tela do TILE CENTRAL da land ((G-1)/2, (G-1)/2), pela calibração atual."""
    mid = (grid_size - 1) / 2
    return c.grid.p(mid, mid)


def _free_grass_start(c: Calib, grid_size: int, w: int, h: int, dx: float, dy: float,
                      occupied: set[tuple[int, int]] | None) -> tuple[int, int] | None:
    """Ponto de arrasto num tile de GRAMA livre (sem prédio, planta, árvore ou animal por perto), o mais perto do centro
    da land. Não depende da cor do céu: usa a grade calibrada e os objetos do estado do jogo."""
    if not occupied:
        return None
    mid = (grid_size - 1) / 2
    best = None
    for x in range(1, grid_size - 1):
        for y in range(1, grid_size - 1):
            if any((x + i, y + j) in occupied for i in (-1, 0, 1) for j in (-1, 0, 1)):
                continue
            sx, sy = c.grid.p(x, y)
            ex, ey = sx + dx, sy + dy
            if not (70 <= sx <= w - 70 and 150 <= sy <= h * 0.80) or not (30 <= ex <= w - 30 and 30 <= ey <= h - 30):
                continue
            if 0.30 * w <= sx <= 0.64 * w and sy > 0.80 * h:               # barra de construção
                continue
            score = (x - mid) ** 2 + (y - mid) ** 2
            if best is None or score < best[0]:
                best = (score, int(sx), int(sy))
    return (best[1], best[2]) if best else None


def frame_camera(page, grid_size: int = 19, max_iter: int = 14, occupied: set[tuple[int, int]] | None = None) -> Calib:
    """Enquadra a land: (1) zoom até o tile ficar com bom tamanho para o tamanho da land (lê `grid_size` tiles por lado);
    (2) acha o TILE CENTRAL da land pela calibração e arrasta a câmera até ele ficar no centro da área útil da tela.
    Não depende do céu: o arrasto começa num tile de grama livre (ou, sem objetos conhecidos, fora da ilha)."""
    c = None
    for it in range(max_iter):
        arr = grab(page)
        c = calibrate_array(arr, grid_size)
        if c.frac < 0.30:                                  # leitura da borda pouco confiável: não mexe no zoom às cegas
            raise RuntimeError(f"não reconheci a borda da ilha com segurança (só {c.frac:.0%} dos pontos seguem o modelo); "
                               "feche janelas/menus abertos e deixe a fazenda inteira visível")
        lo, hi = hw_bounds(arr.shape[1], arr.shape[0], grid_size)
        if c.hw < lo:
            delta = -250 if c.hw < lo - 6 else -100
        elif c.hw > hi:
            delta = 250 if c.hw > hi + 6 else 100
        else:
            log.info("[CALIB] zoom ok: tile %.1f px (faixa %.1f–%.1f) para uma land de %dx%d tiles", c.hw, lo, hi, grid_size, grid_size)
            break
        cx, cy = island_center(c, grid_size)
        log.info("[CALIB] ajustando o zoom (%d/%d): tile %.1f px, faixa %.1f–%.1f -> %s", it + 1, max_iter, c.hw, lo, hi,
                 "aproximando" if delta < 0 else "afastando")
        page.mouse.move(min(max(cx, 50), arr.shape[1] - 50), min(max(cy, 160), arr.shape[0] - 120))   # zoom em torno da land
        page.mouse.wheel(0, delta)
        time.sleep(0.7)
    for attempt in range(5):                               # centraliza o tile central
        arr = grab(page)
        c = calibrate_array(arr, grid_size)
        h, w = arr.shape[:2]
        cx, cy = island_center(c, grid_size)
        tx, ty = w / 2, CENTER_Y * h
        dx, dy = tx - cx, ty - cy
        mid = (grid_size - 1) // 2
        if (dx * dx + dy * dy) ** 0.5 <= CENTER_TOL:
            log.info("[CALIB] tile central (%d,%d) centralizado em (%.0f, %.0f)", mid, mid, cx, cy)
            break
        start = _free_grass_start(c, grid_size, w, h, dx, dy, occupied) or _sky_point(arr) or drag_point(arr, c, dy)
        if not start:
            log.warning("[CALIB] nenhum ponto livre para arrastar a câmera (tile central em (%.0f, %.0f), alvo (%.0f, %.0f))", cx, cy, tx, ty)
            break
        sx, sy = start
        ex = min(max(sx + dx, 30), w - 30)
        ey = min(max(sy + dy, 30), h - 30)
        log.info("[CALIB] centralizando a land (%d/5): tile central (%d,%d) está em (%.0f, %.0f), alvo (%.0f, %.0f); arrastando %.0f,%.0f px",
                 attempt + 1, mid, mid, cx, cy, tx, ty, ex - sx, ey - sy)
        page.mouse.move(sx, sy)
        page.mouse.down()
        page.mouse.move((sx + ex) / 2, (sy + ey) / 2, steps=8)
        page.mouse.move(ex, ey, steps=8)
        page.mouse.up()
        time.sleep(0.8)
    arr = grab(page)
    c = calibrate_array(arr, grid_size)
    h = arr.shape[0]
    if c.yv > SAFE_YV * h:
        raise RuntimeError(f"não consegui enquadrar a câmera: vértice da ilha em y={c.yv:.0f} (limite {SAFE_YV * h:.0f}); "
                           "arraste a fazenda para cima na mão")
    cx, cy = island_center(c, grid_size)
    log.info("[CALIB] câmera enquadrada: tile %.1f px, tile central em (%.0f, %.0f), vértice=(%.0f, %.0f)", c.hw, cx, cy, c.xv, c.yv)
    return c