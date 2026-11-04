"""Casas de craft além da fábrica de ração: Moinho, Laticínio e Padaria.

Aprendido no jogo (consultas /api/windmill/state, /api/dairy, /api/bakery lidas pelo gancho; nada é vendido):
- Moinho: 5 colheitas -> 1 farinha (trigo 1:00, milho 1:30, cenoura 2:00, arroz 2:30, mandioca 3:00, abóbora 3:30,
  batata 4:00, soja 5:00). 1 moenda por vez (`active`), 3 de energia. Botões GRIND / COLLECT.
- Laticínio: leite -> creme (2, 5m36), manteiga (3, 10m30), queijo (4, 17m30). Fila de 4, 3 de energia. MAKE / COLLECT ALL.
- Padaria: receitas com farinhas (do moinho), ovos, mel, manteiga, creme, queijo, cana... Fila de 4 fornos. BAKE / COLLECT ALL.
- Entregas (delivery): troca itens por ouro/XP = venda; NÃO é automatizada.
Posições dos botões em pixels do quadro 1600x723 (viewport do bot); escalam com a largura.
"""
from __future__ import annotations

import logging
import time

import config
from game import EnergyWait, Game
from tasks import _qty_inv

log = logging.getLogger("fh")

W0 = 1600.0                       # largura do quadro em que as posições abaixo foram medidas

WINDMILL = {"recipe_x": 400, "recipe_y0": 200, "recipe_dy": 61.5, "grind": (808, 549), "collect": (1208, 587)}
DAIRY = {"recipe_x": 345, "recipe_y0": 230, "recipe_dy": 100, "make": (965, 381), "collect": (1290, 665)}
BAKERY = {"col_x0": 283, "col_dx": 135, "row_y0": 235, "row_dy": 135, "bake": (862, 538), "collect": (1243, 654)}

FLOUR_RESERVE = 15                # colheitas que ficam guardadas (ração de animais usa as mesmas)
FLOUR_TARGET = 10                 # não mói mais uma farinha com este estoque ou mais
MAX_PER_ITEM = 3                  # no máximo isto de cada produto na fila ao mesmo tempo
INTERVAL = {"windmill": 240, "dairy": 300, "bakery": 300}
OPEN_OFFSETS = [(0, 0), (0, -10), (0, 10), (14, -6), (-12, 6)]   # pontos de clique para abrir a casa (px a meia-altura 16)


def _pt(g: Game, xy):
    s = g.page.viewport_size["width"] / W0 if g.page.viewport_size else 1.0
    return xy[0] * s, xy[1] * s


def _click(g: Game, xy, note: str) -> None:
    g.click(_pt(g, xy), jitter=2.0, note=note, ui=True)


def _open(g: Game, typ: str, api_path: str):
    """Abre a casa clicando nela e espera a consulta do jogo; tenta pontos alternativos. Devolve o JSON."""
    objs = g.link.objects(typ)
    if not objs:
        return None
    g.hand()
    ax, ay = g.grid.anchor(objs[0])
    for i, (dx, dy) in enumerate(OPEN_OFFSETS):
        t0 = time.time() * 1000
        g.click((ax + dx * g.grid.scale, ay + dy * g.grid.scale), note=f"abrir {typ} ponto {i}")
        if g.dry:
            return {}
        r = g.link.wait_api(api_path, t0, 4.0)
        if r and r["t"] > t0 and isinstance(r.get("j"), dict):
            return r["j"]
        g.close_dialogs()
    raise RuntimeError(f"não consegui abrir {typ}")


def _reread(g: Game, api_path: str, click_xy, note: str):
    """Clica (ex.: COLLECT) e relê a consulta (o jogo atualiza o estado depois)."""
    t0 = time.time() * 1000
    _click(g, click_xy, note)
    r = g.link.wait_api(api_path, t0, 4.0)
    return r["j"] if r and r["t"] > t0 and isinstance(r.get("j"), dict) else None


def _energy(g: Game, typ: str, need: float) -> None:
    """Energia para a próxima receita. Se precisa COMPRAR, fecha antes a janela da casa (com ela aberta o clique no '+' da energia cai na janela e a compra falha),
    compra e cancela esta rodada (a casa reabre em seguida). Só compra com energia <= 6 (regra do ensure_energy)."""
    g.link.snapshot()
    if g.energy() >= need:
        return
    _close(g, typ)
    g.dialog_closed = typ
    g.craft_next[typ] = time.time() + 90                # sem compra (energia > 6): espera a recarga natural antes de reabrir
    g.ensure_energy(need)                               # compra (ou levanta EnergyWait se a energia está acima de 6)
    g.craft_next[typ] = time.time() + 8                 # comprou: reabre logo
    raise EnergyWait("energia comprada; a casa reabre em seguida")


CLOSE_X = {"windmill": (1363, 58), "dairy": (1376, 66), "bakery": (1374, 70)}


def _close(g: Game, typ: str = "") -> None:
    """Fecha a janela da casa: primeiro no X conhecido dela, depois confere (e fecha) qualquer janela que sobrou."""
    if getattr(g, "dialog_closed", None) == typ:       # já foi fechada (compra de energia): não clica no X às cegas
        g.dialog_closed = None
        return
    if typ in CLOSE_X and not g.dry:
        _click(g, CLOSE_X[typ], f"fechar {typ}")
    g.close_dialogs()


# ------------------------------------------------------------------ Moinho
def windmill(g: Game) -> None:
    if time.time() < g.craft_next.get("windmill", 0):
        return
    g.craft_next["windmill"] = time.time() + INTERVAL["windmill"]
    data = _open(g, "windmill", "/api/windmill/state")
    if data is None:
        return
    try:
        fresh = _reread(g, "/api/windmill/state", WINDMILL["collect"], "moinho: coletar")
        data = fresh or data
        recipes = data.get("recipes", [])
        if data.get("active"):
            act = data["active"]
            left = None
            for k in ("seconds_left", "secs_left", "remaining"):
                if isinstance(act, dict) and isinstance(act.get(k), (int, float)):
                    left = float(act[k])
            g.craft_next["windmill"] = time.time() + (min(max(left + 3, 20), INTERVAL["windmill"]) if left is not None else 90)
            log.debug("[MOINHO] moenda ocupada")
            return
        energy = int(data.get("craft_energy", 3))
        order = sorted(range(len(recipes)), key=lambda k: -g.demand.get(recipes[k].get("output_id", ""), 0))   # o que missões/pedidos pedem vem primeiro
        for i in order:
            r = recipes[i]
            if not r.get("unlocked", True):
                continue
            crop_qty = _qty_inv(g, r["input_id"])
            flour = _qty_inv(g, r["output_id"])
            wanted = g.demand.get(r["output_id"], 0) > 0
            if wanted:
                if crop_qty < int(r["input_qty"]):
                    continue
            elif crop_qty - int(r["input_qty"]) < FLOUR_RESERVE or flour >= FLOUR_TARGET:
                continue
            _energy(g, "windmill", energy)
            _click(g, (WINDMILL["recipe_x"], WINDMILL["recipe_y0"] + WINDMILL["recipe_dy"] * i), f"moinho: {r['key']}")
            _click(g, WINDMILL["grind"], "moinho: moer")
            g.spend(energy)
            if r["input_id"] in g.inv:
                g.inv[r["input_id"]] -= int(r["input_qty"])
            g.craft_next["windmill"] = time.time() + int(r.get("seconds", 120)) + 5
            log.info("[MOINHO] moendo %s (%d %s -> 1 farinha, %d s)", r["key"], r["input_qty"], r["input_id"].replace("harvest_", ""), r.get("seconds", 0))
            break
        else:
            log.debug("[MOINHO] nada a moer (falta excedente de colheitas ou já há farinha)")
    finally:
        _close(g, "windmill")


# ------------------------------------------------------------------ Laticínio
def dairy(g: Game) -> None:
    if time.time() < g.craft_next.get("dairy", 0):
        return
    g.craft_next["dairy"] = time.time() + INTERVAL["dairy"]
    data = _open(g, "dairy", "/api/dairy")
    if data is None:
        return
    try:
        data = _reread(g, "/api/dairy", DAIRY["collect"], "laticínio: coletar tudo") or data
        recipes = data.get("recipes", [])
        queue = [q for q in data.get("queue", []) if not q.get("ready")]
        free = int(data.get("slots", 4)) - len(queue)
        milk = int(data.get("milk", 0))
        energy = int(data.get("craft_energy", 3))
        queued: dict[str, int] = {}
        for q in queue:
            queued[q.get("recipe", "")] = queued.get(q.get("recipe", ""), 0) + 1
        # do mais valioso por leite ao mais barato: queijo, manteiga, creme
        order = sorted(range(len(recipes)), key=lambda i: -(recipes[i].get("value", 0) / max(recipes[i].get("milk", 1), 1)))
        made = 0
        want = {k: g.demand.get(k, 0) - queued.get(k, 0) for k in ("cream", "butter", "cheese")}   # missões/pedidos
        while free > 0:
            pick = next((i for i in order if recipes[i].get("unlocked", True) and milk >= recipes[i]["milk"]
                         and want.get(recipes[i]["key"], 0) > 0), None)
            if pick is None and not any(v > 0 for v in want.values()):          # sem demanda: guarda leite só se algo falta
                pick = next((i for i in order if recipes[i].get("unlocked", True) and milk >= recipes[i]["milk"]
                             and queued.get(recipes[i]["key"], 0) < MAX_PER_ITEM), None)
            if pick is None:
                break
            want[recipes[pick]["key"]] = want.get(recipes[pick]["key"], 0) - 1
            r = recipes[pick]
            _energy(g, "dairy", energy)
            _click(g, (DAIRY["recipe_x"], DAIRY["recipe_y0"] + DAIRY["recipe_dy"] * pick), f"laticínio: {r['key']}")
            _click(g, DAIRY["make"], "laticínio: fazer")
            g.spend(energy)
            milk -= r["milk"]
            free -= 1
            queued[r["key"]] = queued.get(r["key"], 0) + 1
            made += 1
            log.info("[LATICÍNIO] fazendo %s (leite restante: %d)", r["name"], milk)
        if free > 0 or made == 0:
            wait = [int(q.get("secs_left", 0)) for q in queue if int(q.get("secs_left", 0)) > 0]
            g.craft_next["dairy"] = time.time() + (min(wait) + 5 if wait else INTERVAL["dairy"])
        else:
            wait = [int(q.get("secs_left", 0)) for q in queue if int(q.get("secs_left", 0)) > 0]
            g.craft_next["dairy"] = time.time() + (min(wait) + 5 if wait else 60)
    finally:
        _close(g, "dairy")


# ------------------------------------------------------------------ Padaria
def bakery(g: Game) -> None:
    if time.time() < g.craft_next.get("bakery", 0):
        return
    g.craft_next["bakery"] = time.time() + INTERVAL["bakery"]
    data = _open(g, "bakery", "/api/bakery")
    if data is None:
        return
    try:
        data = _reread(g, "/api/bakery", BAKERY["collect"], "padaria: coletar tudo") or data
        recipes = data.get("recipes", [])
        queue = [q for q in data.get("queue", []) if not q.get("ready")]
        free = int(data.get("slots", 4)) - len(queue)
        energy = int(data.get("craft_energy", 3))
        queued: dict[str, int] = {}
        for q in queue:
            queued[q.get("recipe", "")] = queued.get(q.get("recipe", ""), 0) + 1
        have = {}
        for r in recipes:
            for inp in r.get("inputs", []):
                have[inp["item"]] = int(inp.get("have", 0))
        made = 0
        want = {r["key"]: g.demand.get(r["key"], 0) - queued.get(r["key"], 0) for r in recipes}   # missões/pedidos
        while free > 0:
            cands = []
            for i, r in enumerate(recipes[:9]):                      # a grade da tela mostra 9 receitas
                if not r.get("unlocked", True):
                    continue
                wanted = want.get(r["key"], 0) > 0
                if not wanted and queued.get(r["key"], 0) >= MAX_PER_ITEM:
                    continue
                if not wanted and any(g.demand.get(inp["item"], 0) > 0 for inp in r.get("inputs", [])):
                    continue                                          # não gasta ingrediente que missão/pedido está esperando
                if all(have.get(inp["item"], 0) >= int(inp["qty"]) for inp in r.get("inputs", [])):
                    cands.append((1 if wanted else 0, r.get("value", 0), i))
            if not cands:
                break
            _, _, i = max(cands)
            want[recipes[i]["key"]] = want.get(recipes[i]["key"], 0) - 1
            r = recipes[i]
            _energy(g, "bakery", energy)
            _click(g, (BAKERY["col_x0"] + BAKERY["col_dx"] * (i % 3), BAKERY["row_y0"] + BAKERY["row_dy"] * (i // 3)), f"padaria: {r['key']}")
            _click(g, BAKERY["bake"], "padaria: assar")
            g.spend(energy)
            for inp in r.get("inputs", []):
                have[inp["item"]] = have.get(inp["item"], 0) - int(inp["qty"])
            free -= 1
            queued[r["key"]] = queued.get(r["key"], 0) + 1
            made += 1
            log.info("[PADARIA] assando %s", r["name"])
        wait = [int(q.get("secs_left", 0)) for q in queue if int(q.get("secs_left", 0)) > 0]
        g.craft_next["bakery"] = time.time() + (min(wait) + 5 if wait else INTERVAL["bakery"])
        if made == 0 and free > 0:
            log.debug("[PADARIA] nada a assar (faltam ingredientes)")
    finally:
        _close(g, "bakery")
