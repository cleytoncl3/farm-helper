"""Tarefas da rotina. Ordem pedida pelo usuário: ração -> animais -> mina -> mel -> árvores -> plantas.

Todas decidem pelo ESTADO REAL do jogo (gamestate.Link) e clicam com a calibração (geometry.Grid).
Nunca vendem nada nem usam o Farmers' Market. Única exceção, só se o usuário ligar: orders.py entrega missões do
Task Board e pedidos de entrega (troca de itens por ouro/XP).
"""
from __future__ import annotations

import logging
import time
from collections import Counter

import config
import shopscan
import tileprobe
from game import NOMES_ANIMAIS, Game
from geometry import bbox, components

log = logging.getLogger("fh")


def _qty_inv(g: Game, item: str) -> int:
    """Quantidade de um item (colheita, farinha, ...) no inventário lido; 0 se ausente (o jogo omite itens com 0)."""
    if item in g.inv:
        return g.inv[item]
    return g.stock.get(item, (0, 0))[0]


def _bottom_up(items, key=lambda o: (o["x"], o["y"])):
    """De baixo para cima na tela (maior x+y primeiro), como o usuário pediu."""
    return sorted(items, key=lambda o: (-(key(o)[0] + key(o)[1]), -key(o)[0]))


# ------------------------------------------------------------------ ração
def _crop_of(item_id: str) -> str | None:
    """'harvest_wheat' -> 'wheat' (planta cultivável); 'leaves' etc. -> None (vem de árvores)."""
    return item_id[len("harvest_"):] if item_id.startswith("harvest_") else None


def read_inventory(g: Game) -> None:
    """Abre o celeiro (Inventário) para o jogo informar a quantidade de cada item; guarda em g.inv."""
    barn = g.link.objects("barn")
    if not barn:
        return
    t0 = time.time() * 1000
    g.click(g.grid.obj_click(barn[0], config.OBJ_CLICK), note="celeiro (inventário)")
    g.pause(1.0, 1.5)
    r = g.link.wait_api("/api/player/inventory", t0, 6.0)
    g.click_ui("inventory_close")
    if g.dry:
        return
    if not r or r["t"] <= t0 or not isinstance(r.get("j"), dict):
        log.warning("[INVENTÁRIO] o jogo não enviou o inventário (o celeiro abriu?); uso os dados anteriores")
        return
    g.inv = {it["item_id"]: int(it.get("quantity", 0)) for it in r["j"].get("items", [])}
    g.inv_t = time.time()
    feeds = ", ".join(f"{k.replace('feed_', '')}={g.inv.get(k, 0)}" for k in config.FEED_OF.values())
    log.info("[INVENTÁRIO] ração: %s", feeds)


def feed_factory(g: Game) -> None:
    """Lê o inventário, coleta a ração pronta, fabrica (a de MENOS estoque primeiro) e anota o que falta plantar."""
    fac = g.link.objects("feed_factory")
    if not fac:
        return
    g.hand()
    g.ensure_energy(3)
    read_inventory(g)
    log.debug("[FÁBRICA] abrindo para coletar, fabricar e checar ingredientes")
    t0 = time.time() * 1000
    g.click(g.grid.obj_click(fac[0], config.OBJ_CLICK), note="feed_factory")
    g.pause(1.0, 1.5)
    info = g.link.wait_api("/api/factory/state", t0, 6.0)     # receitas + estoque (have/need) do próprio jogo
    g.click_ui("factory_collect_all")
    if info and info.get("j"):
        _factory_plan(g, info["j"])
    elif not g.dry:
        log.warning("[FÁBRICA] não recebi os dados da fábrica (o diálogo abriu?)")
    g.click_ui("factory_close")


def _factory_plan(g: Game, data: dict) -> None:
    recipes = data.get("recipes", [])
    max_per = int(data.get("max_per_craft", 5))
    busy = [q for q in data.get("queue", []) if not q.get("is_ready")]
    free = max(0, int(data.get("queue_slots", 3)) - len(busy))
    queued: dict[str, int] = {}                                # unidades de cada ração ainda em produção na fila
    for q in busy:
        queued[q.get("recipe", "")] = queued.get(q.get("recipe", ""), 0) + max(0, int(q.get("count", 1)) - int(q.get("ready_count", 0)))
    stock: dict[str, tuple[int, int]] = {}
    g.recipes = []
    for r in recipes:
        for inp in r.get("inputs", []):
            have, need = int(inp["have"]), int(inp["need"])
            old = stock.get(inp["item_id"])
            stock[inp["item_id"]] = (have, max(need, old[1]) if old else need)
        g.recipes.append({"output": r.get("output_item", ""), "name": r.get("display_name", r.get("slug", "?")),
                          "unlocked": r.get("unlocked", True),
                          "inputs": [(i["item_id"], int(i["have"]), int(i["need"])) for i in r.get("inputs", [])]})
    # a ração com MENOS sacos no inventário é fabricada primeiro (e em maior quantidade)
    sacks = {r["output"]: g.inv.get(r["output"], 0) for r in g.recipes}
    order = sorted(range(len(recipes)), key=lambda k: sacks.get(g.recipes[k]["output"], 0))
    log.debug("[FÁBRICA] sacos de ração: %s", ", ".join(f"{g.recipes[k]['name']}={sacks.get(g.recipes[k]['output'], 0)}" for k in order))
    wait_s = [int(q.get("seconds_left", 0)) for q in busy if int(q.get("seconds_left", 0)) > 0]   # fila já em produção
    for i in order:
        r = recipes[i]
        inputs = r.get("inputs", [])
        if not r.get("unlocked", True) or not inputs:
            continue
        name = r.get("display_name", r.get("slug", f"receita {i}"))
        units = min(int(inp["have"]) // max(int(inp["need"]), 1) for inp in inputs)
        missing = [inp for inp in inputs if int(inp["have"]) < int(inp["need"])]
        room = config.FACTORY_MAX_PER_FOOD - queued.get(r.get("slug", ""), 0)     # nunca mais que o limite por ração
        if units >= 1 and free > 0 and room >= 1:
            units = min(units, room, max_per, config.FACTORY_MAX_PER_FOOD)
            units = g.affordable(int(data.get("craft_energy", 3)), units)      # só o que a energia paga (compra só com <= 6)
            if units < 1:
                log.info("[ENERGIA] sem energia para fabricar %s agora; volto no próximo ciclo", name)
                continue
            g.click((config.UI["factory_recipe_x"] * g.ui_scale,
                     (config.UI["factory_recipe_y0"] + config.UI["factory_recipe_dy"] * i) * g.ui_scale),
                    note=f"receita {name}", ui=True)
            for _ in range(units - 1):
                g.click(g.ui("factory_qty_plus"), wait=False, ui=True)
                g.pause(0.4, 0.8)
            g.click_ui("factory_craft")
            g.spend(int(data.get("craft_energy", 3)) * units)
            free -= 1
            wait_s.append(int(r.get("craft_seconds", 300)))
            log.info("[FÁBRICA] Fabricando %d× %s", units, name)
        if missing:
            txt = ", ".join(f"{m['item_id']} {m['have']}/{m['need']}" for m in missing)
            crops = [c for c in (_crop_of(m["item_id"]) for m in missing) if c]
            msg = f"{name}: faltam {txt}" + (f" -> vou plantar: {', '.join(crops)}" if crops else " (vem das árvores)")
            if g.last_missing.get(name) != msg:                       # só repete no log quando mudar
                g.last_missing[name] = msg
                log.info("[FÁBRICA] %s", msg)
        else:
            g.last_missing.pop(name, None)
    g.stock = stock
    g.factory_next = (min(wait_s) + 3) if wait_s else 0     # volta quando a 1ª ração da fila terminar (para coletar)


# ------------------------------------------------------------------ animais
def _animal_status(g: Game, o: dict, now: float) -> tuple[bool, bool, bool]:
    """(produto_pronto, precisa_ração, tem_ração) de um animal, pelo estado salvo e pelo timer ready_at."""
    d = o.get("data", {})
    st = d.get("state")
    product_ready = st == 2 or (st == 1 and d.get("ready_at", 1e18) <= now)      # timer venceu antes do próximo salvamento
    needs_feed = st != 1 or d.get("ready_at", 1e18) <= now
    feed = config.FEED_OF.get(o["type"], "")
    have = g.inv.get(feed, 0) if g.inv else None            # o jogo omite itens com 0; None = inventário não lido
    return product_ready, needs_feed, (have is None or have > 0)


def animal_due(g: Game, o: dict, now: float | None = None) -> bool:
    """Vale interromper o que está fazendo para atender este animal? (produto pronto, ou com fome E há ração)."""
    now = now or time.time()
    if g.cooling(f"{o['type']}:{o['x']},{o['y']}", 90):
        return False
    product_ready, needs_feed, has_feed = _animal_status(g, o, now)
    stale = needs_feed and not has_feed and now - getattr(g, "inv_t", 0) > 120    # estoque lido pode estar velho
    return product_ready or (needs_feed and has_feed) or stale


def animals(g: Game) -> None:
    now = time.time()
    g.hand()
    # Inventário velho (ex.: a fábrica acabou de entregar ração): se algum animal está com fome e o estoque lido
    # diz "sem ração", reler o celeiro antes de desistir.
    if time.time() - getattr(g, "inv_t", 0) > 45 and any(
            _animal_status(g, o, now)[1] and not _animal_status(g, o, now)[2] and not g.cooling(f"{o['type']}:{o['x']},{o['y']}", 90)
            for t in config.ANIMALS for o in g.link.objects(t)):
        log.info("[INVENTÁRIO] animal com fome e sem ração no estoque lido; reconferindo o celeiro")
        read_inventory(g)
    for t in config.ANIMALS:
        for o in g.link.objects(t):
            key = f"{t}:{o['x']},{o['y']}"
            d = o.get("data", {})
            if g.cooling(key, 90):
                continue
            feed = config.FEED_OF.get(t, "")
            have_feed = g.inv.get(feed, 0) if g.inv else None       # o jogo omite itens com 0; None = inventário não lido
            product_ready, needs_feed, has_feed = _animal_status(g, o, now)
            if not product_ready and not needs_feed:
                continue                       # alimentado e produzindo
            if not product_ready and not has_feed:
                if not g.cooling(f"nofeed:{key}", 300):             # com fome e sem ração: avisa de tempos em tempos
                    log.warning("[ANIMAL] %s está com fome mas não há ração (%s=0); aguardo a fábrica", t, feed)
                    g.mark(f"nofeed:{key}")
                continue
            g.ensure_energy(config.ENERGY_FEED)
            acao = (["coletando o produto"] if product_ready else []) + (["alimentando"] if has_feed else [])
            log.info("[ANIMAL] %s: %s (ração: %s)", NOMES_ANIMAIS.get(t, t), " e ".join(acao) or "verificando",
                     "?" if have_feed is None else have_feed)
            bkey = f"bubble:{key}"
            if product_ready:                                        # produto pronto: clica na bolha
                i = g.tries.get(bkey, 0)                             # a cada nova tentativa varia o ponto de clique
                g.tries[bkey] = i + 1
                dx, dy = config.BUBBLE_TRY[i % len(config.BUBBLE_TRY)]
                bx, by = g.grid.obj_click(o, config.BUBBLE_CLICK)
                g.click((bx + dx * g.grid.scale, by + dy * g.grid.scale), note=f"{t} bolha ponto {i % len(config.BUBBLE_TRY)}")
            else:
                g.tries[bkey] = 0
            if have_feed is not None and have_feed <= 0:
                log.warning("[ANIMAL] %s: sem ração (%s=0) no inventário; só coletei o produto", t, feed)
            else:
                g.click(g.grid.obj_click(o, config.OBJ_CLICK), note=f"{t} alimentar")
                g.spend(config.ENERGY_FEED)
                if have_feed:
                    g.inv[feed] = have_feed - 1
            g.mark(key)


# ------------------------------------------------------------------ mina
def _mine_remaining(j) -> float | None:
    """Segundos até a corrida da mina terminar, lidos da consulta /api/mining/state do jogo (leitura tolerante).
    0 = pronta; None = formato não reconhecido (aí vale o timer próprio)."""
    from datetime import datetime, timezone

    left: list[float] = []
    state = {"ready": False, "seen": False}
    num_keys = ("seconds_left", "remaining_seconds", "time_left", "secs_left", "seconds_remaining", "ready_in")
    time_keys = ("ready_at", "ends_at", "end_at", "finish_at", "finishes_at", "completes_at", "done_at")
    flag_keys = ("is_ready", "ready", "done", "collectable", "can_collect", "finished")

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                kl = k.lower()
                if kl in num_keys and isinstance(v, (int, float)) and not isinstance(v, bool):
                    left.append(float(v)); state["seen"] = True
                elif kl in time_keys and isinstance(v, str):
                    try:
                        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                        left.append((dt - datetime.now(timezone.utc)).total_seconds()); state["seen"] = True
                    except ValueError:
                        pass
                elif kl in flag_keys and v is True:
                    state["ready"] = True; state["seen"] = True
                walk(v)
        elif isinstance(x, list):
            for i in x:
                walk(i)

    walk(j)
    if not state["seen"]:
        return None
    if state["ready"]:
        return 0.0
    return max(0.0, min(left)) if left else None


def mine_due(g: Game) -> bool:
    """A corrida da mina terminou? Usa o estado do jogo (se reconhecido) ou o timer próprio de 20 min."""
    if "mining_station" in getattr(g, "disabled", ()) or not g.link.objects("mining_station") or time.time() < g.mine_next_try:
        return False
    r = g.link.api("/api/mining/state") if not g.dry else None
    if r and r.get("j") is not None:
        rem = _mine_remaining(r["j"])
        if rem is not None:
            return rem - (time.time() - r["t"] / 1000.0) <= 0
    return time.time() >= g.mine_ready_at


def mine(g: Game, force: bool = False) -> None:
    m = g.link.objects("mining_station")
    if not m:
        return
    if not force and not mine_due(g):
        return
    g.mine_next_try = time.time() + 90          # não insiste a cada verificação se algo der errado
    g.hand()
    g.ensure_energy(5)
    log.info("[MINA] Coletar e iniciar nova corrida")
    ax, ay = g.grid.anchor(m[0])
    for i, (dx, dy) in enumerate(config.MINE_OFFSETS):        # abre o diálogo certo (o vizinho é a Unit Factory)
        g.click((ax + dx * g.grid.scale, ay + dy * g.grid.scale), note=f"abrir mina ponto {i}")
        g.pause(1.6, 2.0)                       # o diálogo leva ~1,5 s para aparecer por completo
        if g.mine_dialog_open():
            break
        log.info("[MINA] diálogo errado/fechado no ponto %d; fechando e tentando outro", i)
        g.escape()
    else:
        raise RuntimeError("não consegui abrir o diálogo da mina")
    g.click_ui("mine_main")        # COLLECT (se pronta)
    g.click_ui("mine_ok")          # fecha "You mined!"
    g.click_ui("mine_main")        # START RUN (se ocioso) / DIGGING (sem efeito)
    g.spend(5)
    g.click_ui("mine_close")
    g.mine_ready_at = time.time() + config.INTERVAL_MINE   # timer próprio de reserva (corrida de 20 min)


# ---- interrupção: casas com timer vencido ------------------------------------------------------
def run_services(g: Game) -> list[str]:
    """Chamado de dentro de tarefas longas (via g.checkpoint): atende vaca/galinheiro/porco/mina se algum timer venceu.
    O chamador retoma a ferramenta; o saco de sementes é reprendido pelo ensure_sack."""
    now = time.time()
    due_animals = [o for t in config.ANIMALS for o in g.link.objects(t) if animal_due(g, o, now)]
    mine_ready = mine_due(g)
    honey_ready = honey_due(g)
    if not due_animals and not mine_ready and not honey_ready:
        return []
    names = [f"{o['type']}" for o in due_animals] + (["mina"] if mine_ready else []) + (["mel"] if honey_ready else [])
    log.info("[INTERRUPÇÃO] timers vencidos: %s; pausando o que estava fazendo", ", ".join(names))
    g.escape()                         # solta o saco de sementes (clicar num animal com o saco no cursor seria errado)
    ran: list[str] = []
    if due_animals:
        animals(g)
        ran.append("animais")
    if mine_ready:
        mine(g)
        ran.append("mina")
    if honey_ready:
        honey(g)
        ran.append("mel")
    return ran


# ------------------------------------------------------------------ mel
def honey_due(g: Game) -> bool:
    return bool(g.link.objects("beehouse")) and time.time() >= g.honey_next


def honey(g: Game) -> None:
    g.honey_next = time.time() + config.INTERVAL_HONEY     # próxima ronda (o mel não tem estado legível)
    g.hand()
    for o in _bottom_up(g.link.objects("beehouse")):
        log.info("[MEL] Colmeia %d,%d", o["x"], o["y"])
        # A interação é na BASE da casa (o pote de mel aparece na frente, mas clicar nele não coleta).
        for off in config.HIVE_OFFSETS:
            g.click(g.grid.offset_click(o["x"], o["y"], off), note=f"colmeia {o['x']},{o['y']} base")


# ------------------------------------------------------------------ árvores
def trees(g: Game) -> None:
    now = time.time()
    ready = []
    for o in g.link.objects("tree"):
        d = o.get("data", {})
        if g.cooling(f"tree:{o['x']},{o['y']}", 150):
            continue
        if d.get("ready") or d.get("ready_at", 1e18) < now:
            ready.append(o)
    if not ready:
        return
    g.hand()
    log.info("[ÁRVORES] %d prontas", len(ready))
    for o in _bottom_up(ready):
        key = f"tree:{o['x']},{o['y']}"
        g.checkpoint()                          # timers vencidos (animais/mina)? atende e volta
        g.ensure_energy(config.ENERGY_TREE)
        i = g.tries.get(key, 0)                 # cada nova tentativa neste tronco usa outro ponto de clique
        g.tries[key] = i + 1
        off = config.TREE_OFFSETS[i % len(config.TREE_OFFSETS)]
        g.click(g.grid.offset_click(o["x"], o["y"], off), note=f"árvore {o['x']},{o['y']} ponto {i % len(config.TREE_OFFSETS)}")
        g.spend(config.ENERGY_TREE)
        g.mark(key)


# ------------------------------------------------------------------ plantas
def _soils(g: Game) -> list[dict]:
    return g.link.objects("soil")


def _free_grass(g: Game, near: tuple[int, int]) -> tuple[int, int]:
    """Tile livre (e sem vizinhos ocupados) perto de `near`, para soltar o saco de sementes."""
    occ = set()
    for o in g.link.objects():
        for i in range(o.get("sx", 1)):
            for j in range(o.get("sy", 1)):
                occ.add((o["x"] + i, o["y"] + j))
    size = int((g.link.state or {}).get("grid_data", {}).get("grid_size", 19))
    best = None
    for x in range(1, size - 1):
        for y in range(1, size - 1):
            cells = [(x + i, y + j) for i in (-1, 0, 1) for j in (-1, 0, 1)]
            if any(c in occ for c in cells):
                continue
            d = abs(x - near[0]) + abs(y - near[1])
            if best is None or d < best[0]:
                best = (d, (x, y))
    if best is None:
        raise RuntimeError("sem tile livre para o saco de sementes")
    return best[1]


def _sack_tile(g: Game, near: tuple[int, int]) -> tuple[int, int]:
    """Onde soltar o próximo saco: na LINHA DAS COLMEIAS, enfileirando para a esquerda (x decrescente) no primeiro
    tile livre. Sem colmeias, cai no tile livre mais próximo do canteiro."""
    hives = g.link.objects("beehouse")
    if config.SACK_ROW_FROM_BEEHIVES and hives:
        occ = set()
        for o in g.link.objects():
            for i in range(o.get("sx", 1)):
                for j in range(o.get("sy", 1)):
                    occ.add((o["x"] + i, o["y"] + j))
        row = Counter(h["y"] for h in hives).most_common(1)[0][0]
        x = min(h["x"] for h in hives) - 1
        while x >= 0:
            if (x, row) not in occ:
                return x, row
            x -= 1
    return _free_grass(g, near)


def _sack_points(g: Game, x: int, y: int) -> list[tuple[float, float]]:
    return [g.grid.offset_click(x, y, off) for off in config.SACK_OFFSETS]


class SeedUnavailable(RuntimeError):
    """A semente não está disponível para este jogador (nível) ou não foi achada na loja."""


def refresh_available_crops(g: Game) -> None:
    """Sementes que o nível do jogador libera: catálogo do jogo (unlock_level de cada semente) x nível atual."""
    cat = (g.link.api("/api/catalog/items") or {}).get("j") or {}
    xp = (g.link.api("/api/xp/state") or {}).get("j") or {}
    level = xp.get("level")
    if not cat or level is None:
        return
    ok = set()
    for it in cat.get("items", []):
        st = (it.get("gameplay_stats") or {}).get("seed_type")
        if it.get("category") == "crop" and st and int(it.get("unlock_level", 0) or 0) <= int(level):
            ok.add(st)
    if ok:
        if g.avail_crops != ok:
            log.info("[PLANTIO] sementes liberadas no seu nível (%s): %s", level, ", ".join(sorted(ok)))
        g.avail_crops = ok


def _crop_usable(g: Game, c: str) -> bool:
    return c in config.SHOP_SEED and (g.avail_crops is None or c in g.avail_crops) and g.bad_crops.get(c, 0) < time.time()


def _occupied(g: Game, margin: int) -> set[tuple[int, int]]:
    """Tiles ocupados (prédios, plantas, árvores, sacos) com folga; vale para qualquer âncora de objeto."""
    occ: set[tuple[int, int]] = set()
    for o in g.link.objects():
        sx, sy = int(o.get("sx", 1) or 1), int(o.get("sy", 1) or 1)
        for i in range(-sx - margin + 1, sx + margin):
            for j in range(-sy - margin + 1, sy + margin):
                occ.add((o["x"] + i, o["y"] + j))
    return occ


def _sack_candidates(g: Game, near: tuple[int, int]) -> list[tuple[int, int]]:
    """Tiles para soltar o saco, em ordem de preferência: fila à esquerda da linha das colmeias; depois os mais livres perto do canteiro."""
    size = g._grid_size()
    out: list[tuple[int, int]] = []
    hives = g.link.objects("beehouse")
    if config.SACK_ROW_FROM_BEEHIVES and hives:
        occ0 = _occupied(g, 0)
        row = Counter(h["y"] for h in hives).most_common(1)[0][0]
        x = min(h["x"] for h in hives) - 1
        while x >= 0 and len(out) < 6:
            if (x, row) not in occ0:
                out.append((x, row))
            x -= 1
    occ1 = _occupied(g, 1)
    rest = sorted(((abs(x - near[0]) + abs(y - near[1]), (x, y)) for x in range(1, size - 1) for y in range(1, size - 1)
                   if (x, y) not in occ1))
    out += [t for _, t in rest[:14] if t not in out]
    return out


def _tile_is_free(g: Game, t: tuple[int, int]) -> bool:
    """Confere NA TELA se o tile existe e está livre: o jogo só destaca (tracinhos brancos) tiles livres sob o mouse."""
    if g.dry:
        return True
    x, y = g.grid.p(*t)
    return tileprobe.Prober(g.page).present(x, y, g.grid.ax, g.grid.ay)


def _wait_sack(g: Game, crop: str, n: int, drop: tuple[int, int], timeout: float = 70.0) -> bool:
    """O saco comprado realmente apareceu na grama (estado salvo do jogo)? Só então dá para plantar com ele."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            g.link.refresh(timeout=30)
        except TimeoutError:
            continue
        for s in g.link.objects("seed_sack"):
            d = s.get("data", {})
            if d.get("seed_type") == crop and d.get("count", 0) >= n and abs(s["x"] - drop[0]) <= 1 and abs(s["y"] - drop[1]) <= 1:
                return True
    return False


def _buy_and_place(g: Game, crop: str, n: int) -> list[tuple[float, float]]:
    """Compra `n` sementes de `crop` (o saco vem no cursor), solta o saco num tile LIVRE (conferido na tela) e CONFERE que o saco
    existe antes de devolver os pontos para pegá-lo. Semente que não existe na loja deste jogador: SeedUnavailable (sem comprar)."""
    if not _crop_usable(g, crop):
        raise SeedUnavailable(f"{crop}: indisponível para este jogador")
    field = [(o["x"], o["y"]) for o in _soils(g)]
    near = (min(x for x, _ in field) - 2, min(y for _, y in field)) if field else (8, 12)
    g.hand()
    drop = None
    for cand in _sack_candidates(g, near):               # 1) tile livre confirmado ANTES de abrir a loja
        if _tile_is_free(g, cand):
            drop = cand
            break
        log.info("[SACO] o tile (%d,%d) não aceita o saco (ocupado ou fora da land); tentando outro", *cand)
    if drop is None:
        raise RuntimeError("não achei um tile livre (confirmado na tela) para soltar o saco de sementes")
    log.info("[PLANTIO] comprando %d sementes de %s (saco no tile %d,%d)", n, crop, *drop)
    g.click_ui("shop_button")
    g.pause(1.0, 1.5)
    g.click_ui("shop_tab_seeds")                         # a loja lembra a última aba (árvores, animais...): volta para SEEDS
    g.pause(0.6, 0.9)
    cards = {} if g.dry else shopscan.scan(g.page)       # 2) acha a semente pelo ícone, onde ela estiver na grade
    if not g.dry and crop not in cards:
        g.click_ui("shop_close")
        g.escape()
        g.bad_crops[crop] = time.time() + 3600
        log.warning("[PLANTIO] a semente %s não está na loja deste jogador (nível mais baixo?); não comprei nada e vou ignorá-la por 1 h. "
                    "Sementes que achei: %s", crop, ", ".join(sorted(cards)) or "nenhuma")
        raise SeedUnavailable(crop)
    if not g.dry:
        g.click(cards[crop], note=f"semente {crop}", ui=True)
    qty = g.ui("shop_qty")
    if not g.dry:
        g.page.mouse.click(*qty, click_count=3)
        g.page.keyboard.type(str(n))
    g.pause(0.8, 1.2)
    g.click_ui("shop_buy")
    g.click(g.grid.p(*drop), note="soltar saco na grama")   # o saco comprado vem no cursor; este clique o larga
    if not g.dry and not _wait_sack(g, crop, n, drop):       # 3) só segue se o saco existe de verdade
        g.buy_fail[crop] = g.buy_fail.get(crop, 0) + 1
        if g.buy_fail[crop] >= 2:
            g.bad_crops[crop] = time.time() + 1800
            g.buy_fail[crop] = 0
        raise RuntimeError(f"comprei {crop} mas o saco não apareceu na grama (tile {drop[0]},{drop[1]}); não vou plantar sem saco")
    g.buy_fail[crop] = 0
    return _sack_points(g, *drop)


def _plant_batch(g: Game, crop: str, tiles: list[dict]) -> None:
    """Planta `tiles` (todos vazios) com `crop`, UM POR UM. O saco fica preso ao cursor; só clica em tiles vazios."""
    order = _bottom_up(tiles)
    sacks = [s for s in g.link.objects("seed_sack")
             if s["data"].get("seed_type") == crop and s["data"].get("count", 0) >= len(tiles)
             and (s["x"], s["y"]) not in g.bad_sacks]
    g.escape()
    sack_pt = None
    if sacks:                                   # reaproveita saco existente, mas só se conseguir prendê-lo
        s = sacks[0]
        log.debug("[PLANTIO] tentando o saco existente de %s (%s sementes)", crop, s["data"].get("count"))
        pts = _sack_points(g, s["x"], s["y"])
        try:
            g.ensure_sack(pts, g.grid.tile_click(order[0]["x"], order[0]["y"]))
            sack_pt = pts
        except RuntimeError:
            g.bad_sacks.add((s["x"], s["y"]))
            log.warning("[PLANTIO] não consegui pegar o saco existente de %s em (%d,%d); compro sementes novas",
                        crop, s["x"], s["y"])
            g.escape()
    if sack_pt is None:
        sack_pt = _buy_and_place(g, crop, len(tiles))
    for o in order:
        g.checkpoint()                          # vaca/galinheiro/porco/mina com timer vencido? atende e volta
        g.ensure_energy(config.ENERGY_PLANT)
        pt = g.grid.tile_click(o["x"], o["y"])  # mesmo ponto de clique validado nas árvores/colheita
        g.ensure_sack(sack_pt, pt)              # confere o ícone do saco no cursor ANTES de cada semente
        g.click(pt, note=f"plantar {o['x']},{o['y']}")
        g.spend(config.ENERGY_PLANT)
        g.mark(f"plant:{o['x']},{o['y']}")
        g.memory[f"{o['x']},{o['y']}"] = crop
    g.escape()                                  # solta o saco
    g.save_memory()


def _protected(g: Game) -> set[tuple[int, int]]:
    """Tiles logo à frente de uma colmeia (colmeia em (x, y-1)): as plantas ali alimentam as abelhas; não mexer."""
    if not config.PROTECT_FRONT_OF_BEEHIVES:
        return set()
    return {(h["x"], h["y"] + 1) for h in g.link.objects("beehouse")}


# ---- plano de plantio em quadrantes -------------------------------------------------------------
def _qty(g: Game, crop: str) -> int:
    """Quantidade da colheita `crop` no inventário (ou o `have` da fábrica; 0 se nunca colhida)."""
    item = f"harvest_{crop}"
    if item in g.inv:
        return g.inv[item]
    return g.stock.get(item, (0, 0))[0]


def _ranking(g: Game) -> list[str]:
    """Culturas na ordem em que devem ser plantadas.

    1) Alguma ração com MENOS de FEED_MIN sacos: planta os ingredientes (plantas) escassos dessa ração, começando
       pela ração com MENOS sacos (o que falta para ela é urgente).
    2) Todas as rações com FEED_MIN sacos ou mais: planta as culturas MAIS CARAS do mercado (preço da semente).
    O restante dos quadrantes é completado pelas culturas mais caras."""
    refresh_available_crops(g)
    all_crops = [c for c in config.SHOP_SEED if _crop_usable(g, c)]
    if not all_crops:
        raise RuntimeError("nenhuma semente disponível para este jogador (nível/loja)")
    by_price = sorted(all_crops, key=lambda c: (-config.SEED_PRICE.get(c, 0), c))
    order: list[str] = []
    reasons: dict[str, str] = {}
    sacks = {r["output"]: g.inv.get(r["output"], 0) for r in g.recipes} if g.inv else {}
    for item, miss in sorted(g.demand.items(), key=lambda kv: -kv[1]):       # missões e pedidos de entrega (orders.py)
        c = _crop_of(item)
        if miss > 0 and c and _crop_usable(g, c) and c not in order:
            order.append(c)
            reasons[c] = f"missão/pedido precisa de {miss}"
    low = sorted((r for r in g.recipes if r["unlocked"] and r["output"] in sacks and sacks[r["output"]] < config.FEED_MIN),
                 key=lambda r: sacks[r["output"]])
    for r in low:                                    # ração mais escassa primeiro
        need = sorted(((have / max(n, 1), item) for item, have, n in r["inputs"] if _crop_of(item) and _crop_usable(g, _crop_of(item))))
        for ratio, item in need:
            c = _crop_of(item)
            if ratio < config.STOCK_RATIO_TARGET and c not in order:   # ingrediente escasso (folhas vêm das árvores)
                order.append(c)
                reasons[c] = f"falta p/ {r['name']} ({sacks[r['output']]} sacos)"
    mode = "ração baixa" if order else "mais caras do mercado"
    for c in by_price:
        if c not in order:
            order.append(c)
            reasons.setdefault(c, f"mais cara ({config.SEED_PRICE.get(c, '?')} ouro)")
    g.ratios = {c: float(_qty(g, c)) for c in order[:4]}
    g.plan_reasons = {c: reasons.get(c, "") for c in order[:4]}
    g.plan_mode = mode
    return order


def _field_box(soils: list[dict]):
    tiles = {(o["x"], o["y"]) for o in soils}
    comps = components(tiles)
    field = max(comps, key=len)
    return field, bbox(field), [c for c in comps if c is not field]


def _quadrant(x: int, y: int, box) -> int:
    mx, my = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return (1 if x >= mx else 0) + (2 if y >= my else 0)


def plan_quadrants(g: Game, soils: list[dict]) -> dict[int, str]:
    """Divide o canteiro em 4 quadrantes e escolhe uma cultura para cada, priorizando o que falta para as rações.
    Um quadrante mantém a cultura dominante se ela ainda é prioridade; senão muda quando for replantado."""
    field, box, _ = _field_box(soils)
    order = _ranking(g)
    top = order[:4]
    cur: dict[int, Counter] = {q: Counter() for q in range(4)}
    for o in soils:
        c = o["data"].get("crop")
        if c and (o["x"], o["y"]) in field:
            cur[_quadrant(o["x"], o["y"], box)][c["type"]] += 1
    plan: dict[int, str] = {}
    for q in range(4):
        if cur[q]:
            dom = cur[q].most_common(1)[0][0]
            if dom in top and dom not in plan.values():
                plan[q] = dom
    avail = [c for c in top if c not in plan.values()]
    for q in range(4):
        if q not in plan:
            plan[q] = avail.pop(0) if avail else top[q % len(top)]
    names = {0: "esq-cima", 1: "dir-cima", 2: "esq-baixo", 3: "dir-baixo"}
    prio = ", ".join(f"{c} ({int(g.ratios.get(c, 0))} no inventário; {g.plan_reasons.get(c, '')})" for c in top)
    log.info("[PLANO] %s: %s", g.plan_mode, ", ".join(f"{names[q]}={plan[q]}" for q in range(4)))
    log.debug("[PLANO] prioridade: %s", prio)
    g.memory["plan"] = plan
    return plan


def crops(g: Game) -> None:
    g.hand()
    soils = _soils(g)
    prot = _protected(g)
    ready = [o for o in soils if o["data"].get("crop", {}).get("harvestable") and (o["x"], o["y"]) not in prot
             and not g.cooling(f"harv:{o['x']},{o['y']}", 150)]
    # 1) colher (sem gastar energia), de baixo para cima
    if ready:
        log.info("[COLHEITA] %d tiles prontos", len(ready))
        for o in _bottom_up(ready):
            g.checkpoint()
            g.memory[f"{o['x']},{o['y']}"] = o["data"]["crop"].get("type", config.DEFAULT_CROP)
            g.click(g.grid.tile_click(o["x"], o["y"]), note=f"colher {o['x']},{o['y']}")
            g.mark(f"harv:{o['x']},{o['y']}")
        g.save_memory()
        if any(f"harvest_{o['data']['crop'].get('type')}" in g.stock for o in ready):
            g.request_factory = True            # colheu ingrediente de ração: reconsulta inventário/fábrica já
        g.link.refresh()                        # estado novo: tiles já vazios
        soils = _soils(g)

    # 2) plantar os tiles vazios, 1 por 1: cada quadrante do canteiro recebe uma cultura que falta para as rações
    for attempt in range(2):
        empty = [o for o in soils if not o["data"].get("crop") and (o["x"], o["y"]) not in prot
                 and not g.cooling(f"plant:{o['x']},{o['y']}", 90)]
        if not empty:
            break
        field, box, _others = _field_box(soils)
        plan = plan_quadrants(g, soils)
        by_crop: dict[str, list[dict]] = {}
        for o in empty:
            if (o["x"], o["y"]) in field:
                crop = plan[_quadrant(o["x"], o["y"], box)]
            else:                                # horta fora do canteiro: mantém a cultura anterior
                crop = g.memory.get(f"{o['x']},{o['y']}", config.DEFAULT_CROP)
            by_crop.setdefault(crop, []).append(o)
        for crop, tiles in by_crop.items():
            for i in range(0, len(tiles), config.MAX_SACK):
                try:
                    _plant_batch(g, crop, tiles[i:i + config.MAX_SACK])
                except SeedUnavailable as e:         # sem essa semente: deixa os tiles vazios; o próximo plano usa outra cultura
                    log.warning("[PLANTIO] %s; pulando essa cultura por enquanto", e)
                    g.escape()
                    break
        g.link.refresh()
        soils = _soils(g)
        wrong: Counter = Counter()
        for o in soils:                          # alarme: o jogo plantou algo diferente do planejado?
            c, exp = o["data"].get("crop"), g.memory.get(f"{o['x']},{o['y']}")
            if c and exp and c["type"] != exp and g.cooling(f"plant:{o['x']},{o['y']}", 180):
                wrong[(exp, c["type"])] += 1
        for (exp, got), n in wrong.items():
            log.error("[PLANTIO] ERRO: era para plantar %s mas o jogo plantou %s em %d tiles; confira SHOP_SEED['%s'] em config.py",
                      exp, got, n, exp)

    # 3) regar 1 por 1 (ferramenta Water, área de 1 tile) tudo que está seco
    dry = [o for o in soils if o["data"].get("crop") and not o["data"]["crop"].get("watered") and (o["x"], o["y"]) not in prot
           and not g.cooling(f"water:{o['x']},{o['y']}", 90)]
    if dry:
        log.info("[REGA] %d tiles secos (1 por 1)", len(dry))
        g.escape()                              # garante o saco fora do cursor antes de regar
        g.tool = None
        for o in _bottom_up(dry):
            g.checkpoint()
            g.ensure_energy(config.ENERGY_WATER_TILE)
            g.select_tool("water", "1")         # a compra de energia solta a ferramenta; reseleciona se preciso
            g.click(g.grid.tile_click(o["x"], o["y"]), note=f"regar {o['x']},{o['y']}")
            g.spend(config.ENERGY_WATER_TILE)
            g.mark(f"water:{o['x']},{o['y']}")
        g.hand()
