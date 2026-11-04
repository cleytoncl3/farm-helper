"""Mapeamento entre coordenadas de tile do jogo (x, y) e pixels da tela.

O mapa é isométrico: tela = O + x*A + y*B. A calibração usa os centros de três tiles de canto
do canteiro (topo = (x0,y0), direita = (x1,y0), esquerda = (x0,y1)), medidos com cliques.
"""
from __future__ import annotations

import config


class Grid:
    def __init__(self, ox: float, oy: float, ax: float, ay: float, bx: float, by: float):
        self.ox, self.oy = ox, oy
        self.ax, self.ay = ax, ay      # passo de +1 em x (direita/baixo)
        self.bx, self.by = bx, by      # passo de +1 em y (esquerda/baixo)

    @classmethod
    def from_corners(cls, top, right, left, x0: int, y0: int, x1: int, y1: int) -> "Grid":
        """top=(x0,y0), right=(x1,y0), left=(x0,y1): centros dos tiles na tela."""
        ax = (right[0] - top[0]) / (x1 - x0)
        ay = (right[1] - top[1]) / (x1 - x0)
        bx = (left[0] - top[0]) / (y1 - y0)
        by = (left[1] - top[1]) / (y1 - y0)
        ox = top[0] - x0 * ax - y0 * bx
        oy = top[1] - x0 * ay - y0 * by
        return cls(ox, oy, ax, ay, bx, by)

    # -- tamanhos
    @property
    def hw(self) -> float:
        return abs(self.ax)

    @property
    def hh(self) -> float:
        return abs(self.ay)

    @property
    def scale(self) -> float:
        """Fator em relação à calibração de referência (meia-altura de tile = 16 px)."""
        return self.hh / 16.0

    # -- posições
    def p(self, x: float, y: float) -> tuple[float, float]:
        """Centro do tile (x, y)."""
        return self.ox + x * self.ax + y * self.bx, self.oy + x * self.ay + y * self.by

    def tile_click(self, x: float, y: float) -> tuple[float, float]:
        """Ponto de clique para tiles/árvores: centro deslocado p/ esquerda-baixo."""
        px, py = self.p(x, y)
        return px + config.TILE_DX * self.hw, py + config.TILE_DY * self.hh

    def offset_click(self, x: float, y: float, off: tuple[float, float]) -> tuple[float, float]:
        """Centro do tile deslocado por (dx*meia-largura, dy*meia-altura)."""
        px, py = self.p(x, y)
        return px + off[0] * self.hw, py + off[1] * self.hh

    def anchor(self, obj: dict) -> tuple[float, float]:
        """Tile central do objeto (objetos 3x3 etc.)."""
        return self.p(obj["x"] + (obj.get("sx", 1) - 1) / 2, obj["y"] + (obj.get("sy", 1) - 1) / 2)

    def obj_click(self, obj: dict, table: dict) -> tuple[float, float]:
        ax, ay = self.anchor(obj)
        dx, dy = table.get(obj["type"], (0, 0))
        return ax + dx * self.scale, ay + dy * self.scale

    def to_dict(self) -> dict:
        return {"o": [self.ox, self.oy], "a": [self.ax, self.ay], "b": [self.bx, self.by]}

    @classmethod
    def from_dict(cls, d: dict) -> "Grid":
        return cls(d["o"][0], d["o"][1], d["a"][0], d["a"][1], d["b"][0], d["b"][1])


def components(tiles: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """Componentes conexos (4 vizinhos) de um conjunto de tiles."""
    left = set(tiles)
    out = []
    while left:
        start = left.pop()
        comp, stack = {start}, [start]
        while stack:
            x, y = stack.pop()
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in left:
                    left.remove(n)
                    comp.add(n)
                    stack.append(n)
        out.append(comp)
    return out


def bbox(comp: set[tuple[int, int]]) -> tuple[int, int, int, int]:
    xs = [t[0] for t in comp]
    ys = [t[1] for t in comp]
    return min(xs), min(ys), max(xs), max(ys)


def water_blocks(tiles: set[tuple[int, int]], dry: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Centros de blocos 3x3 (alinhados ao canto de cada componente) que cobrem os tiles secos."""
    centers: set[tuple[int, int]] = set()
    for comp in components(tiles):
        x0, y0, x1, y1 = bbox(comp)
        for cx in range(x0 + 1, x1 + 2, 3):
            for cy in range(y0 + 1, y1 + 2, 3):
                cx2, cy2 = min(cx, x1), min(cy, y1)
                block = {(cx2 + i, cy2 + j) for i in (-1, 0, 1) for j in (-1, 0, 1)}
                if block & dry & comp:
                    centers.add((cx2, cy2))
    # de baixo para cima na tela: maior x+y primeiro
    return sorted(centers, key=lambda t: (-(t[0] + t[1]), -t[0]))
