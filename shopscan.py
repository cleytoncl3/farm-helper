"""Leitura da loja de sementes pela tela: acha CADA semente pelo desenho do ícone, onde ela estiver na grade.

A loja lista as sementes em ordem alfabética (4 por linha). Para outro jogador a lista pode ser menor (sementes de nível
mais alto escondidas) e então as posições andam; por isso o bot não confia em posições fixas: compara o ícone de cada
cartão com as assinaturas de `seed_refs.json` e só usa o que reconhece. Semente não encontrada = indisponível para esse
jogador (nível) e é ignorada. Posições no quadro 1600x723 (escalam com a largura).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image

import autocal

log = logging.getLogger("fh")

HERE = Path(__file__).resolve().parent
SLOT_X = [545, 684, 822, 961]            # centro de cada coluna de cartões
SLOT_Y = [258, 416, 574]                 # centro do ÍCONE de cada linha (o cartão inteiro fica ~20 px mais abaixo)
CARD_DY = 20
MATCH_MAX = 11.0                         # distância máxima (RMS) para aceitar o ícone; entre sementes diferentes o mínimo medido é ~20
W0 = 1600.0

_REFS: dict[str, np.ndarray] | None = None


def _refs() -> dict[str, np.ndarray]:
    global _REFS
    if _REFS is None:
        d = json.loads((HERE / "seed_refs.json").read_text("utf-8"))
        _REFS = {n: np.array(v, dtype=float) for n, v in d["refs"].items()}
    return _REFS


def _desc(arr: np.ndarray, cx: float, cy: float, s: float) -> np.ndarray | None:
    x0, x1, y0, y1 = int(cx - 32 * s), int(cx + 32 * s), int(cy - 30 * s), int(cy + 32 * s)
    if x0 < 0 or y0 < 0 or x1 > arr.shape[1] or y1 > arr.shape[0]:
        return None
    box = arr[y0:y1, x0:x1].astype("uint8")
    return np.asarray(Image.fromarray(box).resize((8, 8), Image.BOX)).astype(float).reshape(-1)


def scan(page) -> dict[str, tuple[float, float]]:
    """Com a loja aberta na aba SEEDS: {semente: (x, y) para clicar no cartão}. Só as que reconhece."""
    arr = autocal.grab(page)
    s = arr.shape[1] / W0
    refs = _refs()
    found: dict[str, tuple[float, float]] = {}
    for r, cy in enumerate(SLOT_Y):
        for c, cx in enumerate(SLOT_X):
            d = _desc(arr, cx * s, cy * s, s)
            if d is None:
                continue
            best = min(((float(np.sqrt(np.mean((d - v) ** 2))), n) for n, v in refs.items()))
            if best[0] <= MATCH_MAX and best[1] not in found:
                found[best[1]] = (cx * s, (cy + CARD_DY) * s)
    return found
