"""Task Board (missões) e Pedidos de entrega.

O que faz, a cada ~5 min:
  1) abre o Task Board, lê as missões em aberto ("Deliver 5 cheese"...) e entrega as que já dá;
  2) abre as Entregas, lê os pedidos (3 vagas, 4 caixas cada): quando TODAS as caixas de um pedido já têm item
     suficiente, deposita cada caixa (botão +) e clica em SHIP;
  3) o que falta e é produzível vira DEMANDA (g.demand: item -> quantidade que falta). Moinho, laticínio, padaria e o
     plantio passam a produzir primeiro o que a demanda pede (farinhas, queijo/manteiga/creme, receitas, colheitas).
Itens que o bot não produz (peixes, diamante, relíquia...) deixam o pedido de lado, sem gastar nada com ele.

Aprendido no jogo (consultas /api/task-board e /api/delivery lidas pelo gancho; cliques conferidos pelas chamadas
POST /api/delivery/fill e /api/delivery/ship que o gancho registra). As posições estão no quadro 1600x723.
ATENÇÃO: entregar pedidos/missões dá ouro/XP (é uma venda por encomenda); só roda se o usuário ligar as chaves
"delivery" / "taskboard" na tela de configuração.
"""
from __future__ import annotations

import logging
import time

import config
import crafts
from game import Game
from tasks import _qty_inv

log = logging.getLogger("fh")

INTERVAL = 300
TASK_ICON = (40, 656)
TASK_CLOSE = (998, 79)
TASK_CARD_X0, TASK_CARD_DX, TASK_LABEL_Y = 530, 161.5, 243
DELIV_CLOSE = (1365, 70)
CRATE_X0, CRATE_DX_COL, CRATE_DX_ORDER, CRATE_Y0, CRATE_DY = 380, 171, 386.5, 230, 157.5
SHIP_X0, SHIP_Y = 410, 600
MAX_ORDERS_TARGETED = 2

DAIRY_ITEMS = {"cream", "butter", "cheese"}
ANIMAL_ITEMS = {"milk", "egg", "pork"}
TREE_ITEMS = {"log", "hardwood", "leaves", "sap", "plant_fiber", "ancient_branch", "amber_crystal"}
MINE_ITEMS = {"stone", "clay", "coal", "iron_ore", "copper_ore", "silver_ore", "gold_ore"}
BAKERY_DEFAULT = {"bread", "cookies", "corn_bread", "croissant", "honey_cake", "carrot_cake", "rice_cake", "cassava_chips", "cheese_pizza"}


def _once(g: Game, key: str, msg: str, *args) -> None:
    """Registra a mensagem uma vez só (o bot repete a leitura a cada 5 min)."""
    seen = g.__dict__.setdefault("_orders_seen", set())
    if key not in seen:
        seen.add(key)
        log.info(msg, *args)


def _api_json(g: Game, path: str) -> dict | None:
    r = g.link.api(path)
    return r["j"] if r and isinstance(r.get("j"), dict) else None


def bakery_recipes(g: Game) -> dict[str, dict]:
    j = _api_json(g, "/api/bakery")
    return {r["key"]: r for r in (j or {}).get("recipes", [])}


def producer(g: Game, item: str) -> str | None:
    """Quem produz o item: crop, flour, dairy, bakery, animal, tree, mine; None = o bot não produz."""
    if item.startswith("harvest_"):
        return "crop" if item[len("harvest_"):] in config.SHOP_SEED else None
    if item.endswith("_flour"):
        return "flour"
    if item in DAIRY_ITEMS:
        return "dairy"
    if item in bakery_recipes(g) or item in BAKERY_DEFAULT:
        return "bakery"
    if item in ANIMAL_ITEMS:
        return "animal"
    if item in TREE_ITEMS:
        return "tree"
    if item in MINE_ITEMS:
        return "mine"
    return None


def _queued(g: Game) -> dict[str, int]:
    """Quantas unidades de cada item já estão na fila de laticínio/padaria (ou moendo)."""
    out: dict[str, int] = {}
    for path in ("/api/dairy", "/api/bakery"):
        for q in (_api_json(g, path) or {}).get("queue", []):
            if not q.get("ready"):
                out[q.get("recipe", "")] = out.get(q.get("recipe", ""), 0) + 1
    act = (_api_json(g, "/api/windmill/state") or {}).get("active")
    if isinstance(act, dict):
        for k in ("output_id", "output", "recipe"):
            if isinstance(act.get(k), str):
                out[act[k]] = out.get(act[k], 0) + 1
                break
    return out


def expand(g: Game, base: dict[str, int]) -> dict[str, int]:
    """Soma à demanda os ingredientes que ainda faltam (farinha -> colheitas; receita -> ingredientes; laticínio -> leite)."""
    queued = _queued(g)
    out: dict[str, int] = {}
    baker = bakery_recipes(g)
    wind = {r["output_id"]: r for r in (_api_json(g, "/api/windmill/state") or {}).get("recipes", []) if "output_id" in r}
    dairy = {r["key"]: r for r in (_api_json(g, "/api/dairy") or {}).get("recipes", [])}

    def need(item: str, qty: int, depth: int = 0) -> None:
        missing = qty - _qty_inv(g, item) - queued.get(item, 0)
        if missing <= 0 or depth > 4:
            return
        out[item] = out.get(item, 0) + missing
        kind = producer(g, item)
        if kind == "bakery" and item in baker:
            for inp in baker[item].get("inputs", []):
                need(inp["item"], int(inp["qty"]) * missing, depth + 1)
        elif kind == "flour" and item in wind:
            need(wind[item]["input_id"], int(wind[item]["input_qty"]) * missing, depth + 1)
        elif kind == "dairy" and item in dairy:
            need("milk", int(dairy[item]["milk"]) * missing, depth + 1)

    for item, qty in base.items():
        need(item, qty)
    return out



# ------------------------------------------------------------------ estado em cache
# O Task Board só muda 1x por dia (reinicia em `resets_in_seconds`); um pedido de entrega só muda quando enviamos um
# (a vaga recebe um novo 15 min depois). Por isso o bot NÃO reabre as telas a cada ciclo: guarda o que leu e só abre
# quando (a) ainda não leu, (b) chegou a hora da próxima mudança, ou (c) o inventário já cobre uma missão/pedido.
CHECK_EVERY = 120          # o passo roda a cada tanto, mas só gasta cliques quando precisa
DELIVERY_NEW_AFTER = 15 * 60 + 15      # depois de um envio a vaga ganha pedido novo em 15 min
DELIVERY_REFRESH = 3600                # releitura de segurança


def _st(g: Game) -> dict:
    return g.__dict__.setdefault("ord", {"tb": None, "tb_next": 0.0, "dv": None, "dv_next": 0.0})


def _have(g: Game, item: str, fallback: int) -> int:
    """Quanto há no inventário (lido pelo bot) ou, sem leitura, o que a tela mostrou por último."""
    return _qty_inv(g, item) if g.inv else fallback


# ------------------------------------------------------------------ Task Board
def _tb_missing(o: dict) -> int:
    return int(o["requirement_quantity"]) - int(o.get("progress", 0))


def _tb_coverable(g: Game, orders_: list[dict]) -> bool:
    return any(g.inv and _qty_inv(g, o["requirement_item"]) >= _tb_missing(o) for o in orders_)


def _tb_demand(g: Game, orders_: list[dict]) -> dict[str, int]:
    demand: dict[str, int] = {}
    for o in orders_:
        item = o["requirement_item"]
        if _have(g, item, 0) >= _tb_missing(o):
            continue                                          # já dá para entregar; a demanda não precisa produzir
        if producer(g, item):
            demand[item] = demand.get(item, 0) + _tb_missing(o)
        else:
            _once(g, "t" + o["assignment_id"], "[MISSÕES] fora do alcance do bot: %s (item que não produzimos)", o["title"])
    return demand


def _task_board(g: Game) -> None:
    """Abre o Task Board, lê as missões do dia e entrega as que o inventário já cobre. Atualiza o cache."""
    st = _st(g)
    st["tb_cov"] = time.time() + 600                # se o inventário "cobre" mas a tela discordar, não reabre por 10 min
    t0 = time.time() * 1000
    crafts._click(g, TASK_ICON, "abrir Task Board")
    r = g.link.wait_api("/api/task-board", t0, 5.0)
    if g.dry:
        return
    try:
        if not r or r["t"] <= t0 or not isinstance(r.get("j"), dict):
            log.warning("[MISSÕES] o Task Board não respondeu")
            st["tb_next"] = time.time() + 600
            return
        inv = g.link.inventory(t0, 3.0)
        if inv:
            g.inv.update(inv)
        board = r["j"]
        active = [o for o in board.get("orders", []) if o.get("status") == "active"]
        delivered = 0
        left: list[dict] = []
        for i, o in enumerate(active):
            item, miss = o["requirement_item"], _tb_missing(o)
            if _qty_inv(g, item) < miss:
                left.append(o)
                continue
            ct = time.time() * 1000
            crafts._click(g, (TASK_CARD_X0 + TASK_CARD_DX * i, TASK_LABEL_Y), f"missão: entregar {item}")
            time.sleep(2.0)
            if [c for c in g.link.calls(ct) if c["m"] == "POST" and c["s"] < 400]:
                delivered += 1
                log.info("[MISSÕES] entregue: %s (+%s ouro, +%s XP)", o["title"], o.get("reward_gold", 0), o.get("reward_xp", 0))
                g.inv[item] = max(0, _qty_inv(g, item) - miss)
            else:
                log.warning("[MISSÕES] cliquei para entregar '%s' mas o jogo não registrou a entrega (%s)",
                            o["title"], [c["u"] for c in g.link.calls(ct)] or "nenhuma chamada")
                left.append(o)
        st["tb"] = left
        reset = float(board.get("resets_in_seconds", 3600))
        # missões novas só no próximo reinício diário; depois de entregar relê uma vez em 5 min para ver se veio outra
        st["tb_next"] = time.time() + (300 if delivered else reset + 30)
        log.info("[MISSÕES] %d em aberto (%s); próxima leitura %s", len(left), ", ".join(o["title"] for o in left) or "nenhuma",
                 "em 5 min" if delivered else f"no reinício diário (em {reset / 3600:.1f} h)")
    finally:
        crafts._click(g, TASK_CLOSE, "fechar Task Board")
        g.close_dialogs()


# ------------------------------------------------------------------ Entregas
def _crate_xy(slot: int, idx: int) -> tuple[float, float]:
    return (CRATE_X0 + CRATE_DX_ORDER * slot + CRATE_DX_COL * (idx % 2), CRATE_Y0 + CRATE_DY * (idx // 2))


def _plan(g: Game, data: dict, fresh: bool) -> tuple[list[tuple[int, list, dict]], list[tuple[int, int, dict]]]:
    """Separa os pedidos em (prontos para enviar, candidatos a produzir). `fresh`: usa o `have` da tela, não o inventário."""
    cap_left = int(data.get("daily_cap", 12)) - int(data.get("shipped_today", 0))
    ready: list[tuple[int, list, dict]] = []
    cand: list[tuple[int, int, dict]] = []
    for s in data.get("slots", []):
        order = s.get("order")
        if not order:
            continue
        slot = int(s["slot"])
        todo = [(i, c) for i, c in enumerate(order["crates"]) if not c["filled"]]
        hv = (lambda c: int(c["have"])) if fresh else (lambda c: _have(g, c["item"], int(c["have"])))
        short = [(i, c) for i, c in todo if hv(c) < int(c["qty"])]
        if not short:
            if cap_left > 0:
                ready.append((slot, todo, order))
                cap_left -= 1
            else:
                _once(g, f"cap{data.get('shipped_today')}", "[ENTREGA] limite diário atingido (%s envios); os pedidos esperam o dia seguinte", data.get("daily_cap"))
            continue
        impossible = [c["item"] for _, c in short if not producer(g, c["item"])]
        if impossible:
            _once(g, f"d{slot}{order['id']}", "[ENTREGA] pedido %d fora do alcance do bot (falta %s)", slot + 1, ", ".join(sorted(set(impossible))))
            continue
        cand.append((sum(int(c["qty"]) - hv(c) for _, c in short), slot, order))
    cand.sort(key=lambda t: t[0])
    return ready, cand


def _dv_demand(g: Game, data: dict, fresh: bool) -> dict[str, int]:
    _, cand = _plan(g, data, fresh)
    demand: dict[str, int] = {}
    for units, slot, order in cand[:MAX_ORDERS_TARGETED]:
        for c in order["crates"]:
            have = int(c["have"]) if fresh else _have(g, c["item"], int(c["have"]))
            if not c["filled"] and have < int(c["qty"]):
                demand[c["item"]] = demand.get(c["item"], 0) + int(c["qty"]) - have
        if fresh:
            log.info("[ENTREGA] pedido %d: vou produzir o que falta (%d unidades)", slot + 1, units)
    return demand


def _dv_coverable(g: Game, data: dict) -> bool:
    ready, _ = _plan(g, data, fresh=False)
    return bool(ready) and bool(g.inv)


def _delivery(g: Game) -> None:
    """Abre as Entregas, envia os pedidos completos e guarda o que leu (e quando vem pedido novo)."""
    st = _st(g)
    st["dv_cov"] = time.time() + 600
    data = crafts._open(g, "delivery", "/api/delivery")
    if data is None or g.dry:
        return
    try:
        if not data.get("enabled", True) or not data.get("owned", True):
            st["dv_next"] = time.time() + DELIVERY_REFRESH
            return
        ready, _ = _plan(g, data, fresh=True)
        shipped = 0
        for slot, todo, order in ready:
            if _ship(g, slot, todo, order):
                shipped += 1
                for s in data["slots"]:
                    if int(s["slot"]) == slot:
                        s["order"], s["next_in"] = None, DELIVERY_NEW_AFTER      # vaga vazia até o pedido novo chegar
        st["dv"] = data
        waits = [int(s.get("next_in") or 0) for s in data.get("slots", []) if not s.get("order")]
        st["dv_next"] = time.time() + (min(w for w in waits if w > 0) + 15 if any(w > 0 for w in waits) else DELIVERY_REFRESH)
        if shipped or waits:
            log.info("[ENTREGA] próximo pedido novo em ~%d min", (st["dv_next"] - time.time()) // 60)
    finally:
        crafts._click(g, DELIV_CLOSE, "fechar Entregas")
        g.close_dialogs()


def _click_crate(g: Game, slot: int, idx: int, item: str) -> bool:
    """Clica no check/+ da caixa e confere o POST /fill do jogo; tenta pontos vizinhos se não registrar."""
    x, y = _crate_xy(slot, idx)
    for dx, dy in ((0, 0), (5, 4), (-5, 6)):
        t0 = time.time() * 1000
        crafts._click(g, (x + dx, y + dy), f"entrega: depositar {item}")
        time.sleep(1.0)
        if [c for c in g.link.calls(t0) if c["u"].endswith("/fill") and c["s"] < 400]:
            return True
    return False


def _ship(g: Game, slot: int, todo: list[tuple[int, dict]], order: dict) -> bool:
    for i, c in todo:                                           # 1) cada caixa: clica no + / check até ficar verde
        if not _click_crate(g, slot, i, c["item"]):
            log.warning("[ENTREGA] pedido %d: a caixa de %s não aceitou o depósito; não vou enviar", slot + 1, c["item"])
            return False
    t1 = time.time() * 1000
    crafts._click(g, (SHIP_X0 + CRATE_DX_ORDER * slot, SHIP_Y), f"entrega: SHIP pedido {slot + 1}")   # 2) depois entrega
    time.sleep(2.5)
    if [c for c in g.link.calls(t1) if c["m"] == "POST" and c["s"] < 400 and not c["u"].endswith("/fill")]:
        log.info("[ENTREGA] pedido %d enviado (+%s ouro, +%s XP)", slot + 1, order.get("gold", 0), order.get("xp", 0))
        for i, c in todo:
            g.inv[c["item"]] = max(0, _qty_inv(g, c["item"]) - int(c["qty"]))
        return True
    log.warning("[ENTREGA] pedido %d: cliquei em SHIP mas o jogo não registrou o envio (%s)", slot + 1,
                [c["u"] for c in g.link.calls(t1)] or "nenhuma chamada")
    return False


# ------------------------------------------------------------------ passo do laço
def orders(g: Game) -> None:
    use_tasks = "taskboard" not in g.disabled
    use_deliv = "delivery" not in g.disabled and bool(g.link.objects("delivery"))
    now = time.time()
    if not (use_tasks or use_deliv) or now < g.craft_next.get("orders", 0):
        return
    g.craft_next["orders"] = now + CHECK_EVERY
    st = _st(g)
    base: dict[str, int] = {}
    if use_tasks:
        if st["tb"] is None or now >= st["tb_next"] or (now >= st.get("tb_cov", 0) and _tb_coverable(g, st["tb"])):
            g.hand()
            _task_board(g)
        base.update(_tb_demand(g, st["tb"] or []))
    if use_deliv:
        if st["dv"] is None or now >= st["dv_next"] or (now >= st.get("dv_cov", 0) and _dv_coverable(g, st["dv"])):
            g.hand()
            _delivery(g)
        if st["dv"]:
            for k, v in _dv_demand(g, st["dv"], fresh=False).items():
                base[k] = max(base.get(k, 0), v)
    total = expand(g, base)
    old = getattr(g, "demand", {})
    g.demand = total
    if total != old:
        shown = ", ".join(f"{k.replace('harvest_', '')} {v}" for k, v in sorted(total.items())
                          if producer(g, k) in ("crop", "flour", "dairy", "bakery", "animal"))
        log.info("[PEDIDOS] preciso produzir: %s", shown or "nada")