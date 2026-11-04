"""Configuração do Farm Helper.

Posições de interface (UI) estão em pixels do quadro de referência 1568x709 (o tamanho de captura de
tela usado nos testes). O bot força a viewport 1600x723 e converte por `ui_scale`. As posições de
mapa (árvores, animais, tiles) NÃO são pixels fixos: vêm do estado real do jogo + calibração.
"""

def _game_url() -> str:
    """Endereço do jogo: o oficial por padrão; um arquivo game_url.txt (opcional) pode trocar."""
    import os
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "game_url.txt")
    try:
        with open(f, encoding="utf-8-sig") as fh:
            u = fh.read().strip()
    except OSError:
        u = ""
    u = u or "https://play.rrfvillage.xyz/"
    return u if u.endswith("/") else u + "/"


GAME_URL = _game_url()
VIEWPORT = {"width": 1600, "height": 723}
REF_WIDTH = 1568  # largura do quadro de referência das constantes UI

# Pausa aleatória entre ações (segundos) — regra do usuário
DELAY_MIN = 1.0
DELAY_MAX = 2.0

# Ponto de clique dentro do tile (frações do meio-tile). Validado pelo usuário nas árvores;
# vale também para colher, plantar e regar: centro deslocado para a esquerda/baixo.
TILE_DX = -0.21   # * meia-largura do tile
TILE_DY = 0.25    # * meia-altura do tile

# Pontos alternativos de clique (fração da meia-largura/meia-altura a partir do centro do tile), testados em
# ordem a cada tentativa que o ESTADO do jogo mostra ter falhado. O sprite de árvore/saco fica acima do chão.
SACK_OFFSETS = [(-0.21, 0.25), (-0.2, -0.5), (-0.2, -1.1), (0.0, -0.2)]
TREE_OFFSETS = [(-0.21, 0.25), (-0.21, -0.4), (-0.21, -0.9), (0.15, -0.2), (-0.4, 0.0)]

# Deslocamentos de clique em relação ao tile-âncora (centro) do objeto, em px com meia-altura
# de tile = 16 (escalam com a calibração). (dx, dy)
OBJ_CLICK = {
    "feed_factory": (0, -71),
    "mining_station": (41, -18),
    "cow": (13, 1),
    "chickencoop": (-10, -42),
    "pig": (0, 4),
    "beehouse": (0, -18),
    "barn": (0, 6),               # abre o Inventário (lista quantidades de ração e colheitas)
}
# Pontos alternativos para abrir a mina (px a meia-altura 16), usados se o diálogo certo não abrir
MINE_OFFSETS = [(0, 0), (14, -6), (-12, 6), (41, -18)]   # (0,0) = tile central da pegada 3x3 (confirmado)
# Colmeia: clicar na base (frações da meia-largura/altura a partir do centro do tile); o pote de mel na frente não serve
HIVE_OFFSETS = [(0.0, 0.45), (-0.25, 0.3)]
BUBBLE_CLICK = {          # bolha de produto pronto (leite/ovo/carne)
    "cow": (0, -134),            # medido na tela: o ovo fica em âncora + (0, -104 px) a meia-altura 11,4
    "chickencoop": (0, -147),
    "pig": (0, -122),
}
# Se a bolha segue pronta depois do clique, a próxima tentativa soma um destes deslocamentos (px a meia-altura 16)
BUBBLE_TRY = [(0, 0), (0, -9), (0, 8), (9, 0), (-9, 0)]
FEED_OF = {"cow": "feed_cow", "chickencoop": "feed_chicken", "pig": "feed_pig"}   # ração de cada animal no inventário
FEED_MIN = 5                   # com MENOS que isso de ração de algum animal: plantar o que falta para ela (urgente);
                               # com FEED_MIN ou mais de cada ração: plantar as culturas MAIS CARAS do mercado
CHECK_EVERY = 15               # segundos entre verificações (animais, mina, colheita...)
ANIMALS = ("cow", "chickencoop", "pig")

# Elementos fixos de interface (quadro 1568 de referência)
UI = {
    "play": (783, 418),             # botão PLAY da tela inicial
    "tool_button": (1133, 647),
    "tool_water": (865, 462),
    "tool_hand": (926, 462),
    "area_1": (832, 565),
    "area_3x3": (1092, 570),
    "shop_button": (1484, 630),
    "shop_qty": (1200, 502),
    "shop_buy": (1200, 602),
    "shop_close": (1340, 48),
    "energy_plus": (1193, 38),
    "energy_best_buy": (958, 450),   # pacote BEST VALUE (+150 por 20 token de testnet)
    "energy_close": (1057, 153),
    "factory_collect_all": (1173, 567),
    "factory_craft": (783, 477),
    "factory_close": (1346, 68),
    "factory_recipe_x": 330,         # lista de receitas: y = 234 + 80*índice (vaca, galinha, porco...)
    "factory_recipe_y0": 234,
    "factory_recipe_dy": 80,
    "inventory_close": (1070, 149),  # X do diálogo do Inventário
    "factory_qty_plus": (883, 386),
    "factory_qty_minus": (709, 386),
    "mine_main": (783, 512),         # COLLECT / START RUN / DIGGING (mesmo botão)
    "mine_ok": (783, 466),
    "mine_close": (1335, 55),
}

# Sementes na Farm Store (clique na embalagem)
SHOP_SEED = {
    "cabbage": (534, 270), "carrot": (670, 270), "cassava": (806, 270), "chili": (942, 270),
    "corn": (534, 425), "eggplant": (670, 425), "potato": (806, 425), "pumpkin": (942, 425),
    "rice": (534, 580), "sugarcane": (670, 580), "tomato": (806, 580), "wheat": (942, 580),
}
MAX_SACK = 99                  # sementes por saco
STOCK_RATIO_TARGET = 12        # alvo: estoque >= 12x o que 1 fabricação consome (define o que plantar primeiro)
DEFAULT_PLAN = ["wheat", "corn", "carrot", "cassava"]   # usado até a fábrica informar o estoque
FACTORY_MAX_PER_FOOD = 3       # NUNCA mais que isso de cada ração (fabricando + na fila), mesmo com recursos de sobra
# Preço da semente na Farm Store (ouro) = ranking de "mais caras do mercado"
SEED_PRICE = {"sugarcane": 36, "eggplant": 30, "cassava": 28, "chili": 28, "corn": 25, "cabbage": 20, "pumpkin": 20,
              "tomato": 20, "potato": 18, "carrot": 15, "rice": 14, "wheat": 10}
PROTECT_FRONT_OF_BEEHIVES = True   # não colher/plantar/regar os tiles logo à frente das colmeias (alimentam as abelhas)
SACK_ROW_FROM_BEEHIVES = True      # sacos de semente ficam na linha das colmeias, enfileirando para a esquerda
DEFAULT_CROP = "corn"          # usado quando o tile não tem histórico

# Custos de energia aproximados
ENERGY_PLANT = 1
ENERGY_WATER_TILE = 2          # rega 1 por 1 (ferramenta Water, área 1 tile)
ENERGY_TREE = 2
ENERGY_FEED = 2
ENERGY_MAX = 170
ENERGY_RESERVE = 15

# Intervalos (segundos) entre tentativas das tarefas sem estado legível
INTERVAL_FACTORY = 120
INTERVAL_MINE = 1260           # corrida de 20 min + margem
INTERVAL_HONEY = 300           # sem estado legível: ronda curta (3 colmeias ~10 s); também roda dentro de colheita/plantio
