"""Navegador, cliques, ferramentas, energia e utilidades de interface."""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import autocal
import config
import userconfig
from gamestate import HOOK_JS, Link
from geometry import Grid

log = logging.getLogger("fh")
_GAME_HOST = urlparse(config.GAME_URL).hostname or ""
STOP_FILE = Path("STOP")
CALIB_FILE = Path("calibration.json")
MEMORY_FILE = Path("memory.json")


class MapClickBlocked(RuntimeError):
    """Clique de mapa barrado: o alvo está sob o HUD (câmera fora de posição)."""


class StopRequested(Exception):
    pass


class EnergyWait(Exception):
    """Sem energia para a ação e SEM direito de comprar (energia acima do limite): a tarefa para e volta no próximo ciclo."""


NOMES_ANIMAIS = {"cow": "vaca", "chickencoop": "galinheiro", "pig": "porco"}
_CORES = {"FÁBRICA": "35", "INVENTÁRIO": "34", "ANIMAL": "33", "MINA": "36", "MEL": "93", "ÁRVORES": "32", "COLHEITA": "92",
          "PLANTIO": "32", "REGA": "94", "PLANO": "95", "ENERGIA": "93", "INTERRUPÇÃO": "1;35", "STATUS": "90", "BOT": "1;37",
          "SESSÃO": "1;33", "ERRO": "1;31", "ATENÇÃO": "1;31", "FATAL": "1;31", "AVISO": "33"}
_ESCONDE_NO_PROMPT = ("FERRAMENTA", "SACO", "CALIB", "DRY")     # detalhes: só no bot.log (ou com --verbose)


class _PromptFormatter(logging.Formatter):
    """Prompt compacto: `HH:MM:SS  CATEGORIA   mensagem`, colorido; erros em vermelho com a causa resumida."""

    def __init__(self, color: bool):
        super().__init__()
        self.color = color

    def _c(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def format(self, record: logging.LogRecord) -> str:
        import re
        import traceback

        msg = record.getMessage()
        m = re.match(r"\[([^\]]+)\]\s*(.*)", msg, re.S)
        tag, text = (m.group(1), m.group(2)) if m else ("", msg)
        if record.levelno >= logging.ERROR and tag not in ("ERRO", "ATENÇÃO", "FATAL"):
            tag = "ERRO"
        elif record.levelno == logging.WARNING and not tag:
            tag = "AVISO"
        label = tag or "BOT"
        code = _CORES.get(label, "37")
        if record.levelno >= logging.ERROR:
            code = "1;31"
        elif record.levelno == logging.WARNING:
            code = "33"
        line = f"{self._c('90', self.formatTime(record, '%H:%M:%S'))}  {self._c(code, f'{label:<11}')} " \
               f"{self._c('31', text) if record.levelno >= logging.ERROR else text}"
        if record.exc_info and record.exc_info[2]:                 # causa resumida; o traceback completo vai para o bot.log
            fr = traceback.extract_tb(record.exc_info[2])[-1]
            line += "\n" + self._c("90", f"{'':>20}↳ {record.exc_info[0].__name__}: {record.exc_info[1]}  ({fr.filename.split(chr(92))[-1]}:{fr.lineno} em {fr.name})")
        return line


class _PromptFilter(logging.Filter):
    def __init__(self, verbose: bool):
        super().__init__()
        self.verbose = verbose

    def filter(self, record: logging.LogRecord) -> bool:
        if self.verbose or record.levelno >= logging.WARNING:
            return True
        msg = record.getMessage()
        return not any(msg.startswith(f"[{t}]") for t in _ESCONDE_NO_PROMPT)


def setup_logging(verbose: bool = False) -> None:
    import os
    import sys

    for st in (sys.stdout, sys.stderr):              # acentos corretos no prompt do Windows
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    if color:
        os.system("")                               # habilita cores ANSI no console do Windows 10+
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_PromptFormatter(color))
    console.addFilter(_PromptFilter(verbose))
    fh = logging.FileHandler("bot.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(console)
    root.addHandler(fh)


def banner() -> None:
    import sys

    c = sys.stdout.isatty()
    t = (lambda x: f"\x1b[1;36m{x}\x1b[0m") if c else (lambda x: x)
    print(t("╔══════════════════════════════════════════════════╗"))
    print(t("║") + "   Farm Helper  ·  verificação a cada %2d s        " % config.CHECK_EVERY + t("║"))
    print(t("║") + "   Parar: Ctrl+C  ou  arquivo STOP nesta pasta     " + t("║"))
    print(t("║") + "   Detalhes completos em bot.log                   " + t("║"))
    print(t("╚══════════════════════════════════════════════════╝"))


def open_game(cdp: str | None, profile: str, exe: str | None):
    """Conecta no navegador (CDP) ou abre um com perfil próprio. O login é feito por você."""
    pw = sync_playwright().start()
    try:
        if cdp:
            browser = pw.chromium.connect_over_cdp(cdp)
            ctx = browser.contexts[0]
        else:
            kw = {"executable_path": exe} if exe else {}
            ctx = pw.chromium.launch_persistent_context(profile, headless=False, viewport=config.VIEWPORT, **kw)
        ctx.add_init_script(HOOK_JS)
        page = next((p for p in ctx.pages if _GAME_HOST in p.url), None)
        if page is None:
            page = ctx.pages[0] if (not cdp and ctx.pages) else ctx.new_page()
            page.goto(config.GAME_URL)
        page.set_viewport_size(config.VIEWPORT)
        page.reload()               # recarrega para o gancho valer dentro do iframe do jogo
        page.bring_to_front()
    except BaseException:
        try:                                    # não deixa o Playwright preso (senão a próxima tentativa falha com 'Sync API inside the asyncio loop')
            pw.stop()
        except Exception:  # noqa: BLE001
            pass
        raise
    return pw, page


def enter_game(page, link: Link) -> None:
    """Espera o primeiro estado; se o jogo estiver na tela inicial, clica em PLAY."""
    try:
        link.refresh(timeout=20)
        return
    except TimeoutError:
        pass
    s = config.VIEWPORT["width"] / config.REF_WIDTH
    log.info("[BOT] Tela inicial detectada; clicando em PLAY")
    page.mouse.click(config.UI["play"][0] * s, config.UI["play"][1] * s)
    link.refresh(timeout=120)


class Game:
    def __init__(self, page, link: Link, grid: Grid | None, auto_energy: bool = True, dry: bool = False):
        self.page, self.link, self.grid = page, link, grid
        self.calib = None                                # autocal.Calib quando a calibração é automática
        self.auto_energy, self.dry = auto_energy, dry
        self.tool: tuple | None = None
        self.events: list[tuple[float, float]] = []   # (ts, delta de energia) desde o último salvamento
        self.recent: dict[str, float] = {}
        self.tries: dict[str, int] = {}                  # tentativas por alvo (para variar o ponto de clique)
        self.stock: dict[str, tuple[int, int]] = {}      # ingrediente -> (tenho, preciso por fabricação), lido da fábrica
        self.ratios: dict[str, float] = {}
        self.inv: dict[str, int] = {}                    # inventário lido do celeiro: item_id -> quantidade
        self.recipes: list[dict] = []                    # receitas da fábrica: {output, name, inputs[(item, have, need)]}
        self.plan_reasons: dict[str, str] = {}
        self.plan_mode = ""
        self.last_missing: dict[str, str] = {}           # último aviso de ingrediente faltando (evita repetir no log)
        self.mine_ready_at = 0.0                         # quando a corrida da mina termina (timer próprio, reserva)
        self.mine_next_try = 0.0
        self.craft_next: dict[str, float] = {}           # quando revisitar moinho/laticínio/padaria
        self.disabled: set[str] = set()                  # rotinas desligadas pelo usuário (config_usuario.json)
        self.bias: dict | None = None                    # correção da grade medida nos tiles (tileprobe)
        self.land_g: int | None = None                   # tamanho da land (tiles por lado) lido na tela
        self.avail_crops: set[str] | None = None         # sementes que o nível do jogador libera (catálogo + nível)
        self.bad_crops: dict[str, float] = {}            # semente -> até quando ignorar (não achada na loja)
        self.buy_fail: dict[str, int] = {}
        self.demand: dict[str, int] = {}                 # item -> quanto falta produzir p/ missões e pedidos (orders.py)
        self.honey_next = 0.0                            # quando as colmeias devem ser visitadas de novo
        self.request_factory = False                     # colheita de ingrediente: reconsultar fábrica/inventário já
        self.last_factory = 0.0
        self.factory_next = 0.0                          # em quantos s a fábrica deve ser revisitada (fila em produção)
        self.bad_sacks: set[tuple[int, int]] = set()     # sacos existentes que não consegui prender
        self.service_fn = None                           # tasks.run_services (definido pelo main)
        self._in_service = False
        self._last_check = 0.0
        self.memory: dict[str, str] = json.loads(MEMORY_FILE.read_text("utf-8")) if MEMORY_FILE.exists() else {}
        self.ui_scale = config.VIEWPORT["width"] / config.REF_WIDTH

    # ---------------------------------------------------------------- básico
    def check_stop(self) -> None:
        if STOP_FILE.exists():
            raise StopRequested

    def pause(self, lo: float = config.DELAY_MIN, hi: float = config.DELAY_MAX) -> None:
        self.check_stop()
        time.sleep(random.uniform(lo, hi))

    def blocked_zone(self, x: float, y: float) -> str | None:
        """Cliques de MAPA nunca podem cair no HUD: barra do rodapé (Friends, loja, atalhos...), cantos do topo e ícones
        laterais. Devolve o motivo se (x, y) está numa dessas zonas."""
        w = config.VIEWPORT["width"]
        h = self.page.viewport_size["height"] if self.page.viewport_size else config.VIEWPORT["height"]
        if y > autocal.SAFE_YV * h:
            return "barra do rodapé"
        if y < 0.14 * h and (x < 0.16 * w or x > 0.86 * w):
            return "HUD do topo"
        if x < 0.08 * w or x > 0.92 * w:
            return "ícones laterais"
        return None

    def click(self, pt, jitter: float = 1.5, wait: bool = True, note: str = "", ui: bool = False) -> None:
        """`ui=True` para botões/diálogos fixos da interface; o padrão é clique de mapa, que é barrado no HUD."""
        x = pt[0] + random.uniform(-jitter, jitter)
        y = pt[1] + random.uniform(-jitter, jitter)
        if not ui and not self.dry:
            zone = self.blocked_zone(x, y)
            if zone:
                raise MapClickBlocked(f"clique de mapa em ({x:.0f}, {y:.0f}) cairia na {zone} ({note}); a câmera está errada")
        if self.dry:
            log.info("[DRY] click (%.0f, %.0f) %s", x, y, note)
        else:
            self.page.mouse.move(x, y, steps=random.randint(4, 9))
            self.page.mouse.click(x, y)
        if wait:
            self.pause()

    def ui(self, name: str, sub: str | None = None):
        v = config.UI[name] if sub is None else config.UI[name][sub]
        return v[0] * self.ui_scale, v[1] * self.ui_scale

    def click_ui(self, name: str, sub: str | None = None, jitter: float = 1.0) -> None:
        self.click(self.ui(name, sub), jitter=jitter, note=name, ui=True)

    def escape(self, times: int = 1) -> None:
        for _ in range(times):
            if not self.dry:
                self.page.keyboard.press("Escape")
            time.sleep(0.6)

    def cooling(self, key: str, seconds: float = 120.0) -> bool:
        """True se já agimos neste alvo há pouco (o estado salvo pode estar defasado até ~20 s)."""
        return time.time() - self.recent.get(key, 0.0) < seconds

    def mark(self, key: str) -> None:
        self.recent[key] = time.time()

    def save_memory(self) -> None:
        MEMORY_FILE.write_text(json.dumps(self.memory, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- interrupção por timers
    def checkpoint(self, every: float = 6.0) -> bool:
        """Chamado dentro das tarefas longas (plantar, regar, colher, cortar). Se algum timer venceu (vaca, galinheiro,
        porco ou mina), interrompe, atende e RETOMA: reseleciona a ferramenta; o saco de sementes é reprendido pelo
        `ensure_sack` antes da próxima semente. Devolve True se atendeu algo."""
        if self.service_fn is None or self._in_service or self.dry:
            return False
        now = time.time()
        if now - self._last_check < every:
            return False
        self._last_check = now
        try:
            self.link.snapshot()
            self._in_service = True
            prev = self.tool
            ran = self.service_fn(self)
        except (StopRequested, KeyboardInterrupt):
            raise
        except Exception as e:  # noqa: BLE001 - uma falha no atendimento não derruba a tarefa que estava rodando
            log.error("[INTERRUPÇÃO] falha ao atender os timers: %s", e, exc_info=True)
            try:
                self.recover()
            except Exception:  # noqa: BLE001
                pass
            return False
        finally:
            self._in_service = False
        if ran:
            log.info("[INTERRUPÇÃO] %s atendido(s); voltando ao que estava fazendo", ", ".join(ran))
            self.tool = None
            if prev:
                self.select_tool(*prev)
            else:
                self.hand()
        return bool(ran)

    # ---------------------------------------------------------------- calibração automática
    def _state_grid_size(self) -> int:
        return int((self.link.state or {}).get("grid_data", {}).get("grid_size", 19))

    def _grid_size(self) -> int:
        """Tiles por lado da land: o que foi LIDO na tela (tileprobe), senão o que o jogo informa."""
        return self.land_g or self._state_grid_size()

    def close_dialogs(self) -> int:
        """Fecha qualquer janela aberta por engano. O Esc não fecha todas (o menu Friends do rodapé ignora), então
        procura o botão vermelho X do canto superior direito e clica nele. Devolve quantas janelas fechou."""
        if self.dry:
            return 0
        closed = 0
        for _ in range(4):
            self.page.keyboard.press("Escape")
            time.sleep(0.5)
            xy = autocal.find_close_x(autocal.grab(self.page))
            if xy is None:
                break
            log.warning("[JANELA] janela aberta por cima do jogo; clicando no X (%d, %d)", *xy)
            self.page.mouse.move(*xy, steps=4)
            self.page.mouse.click(*xy)
            closed += 1
            time.sleep(0.8)
        if closed:
            self.tool = None
        return closed

    def recalibrate(self, force: bool = False) -> bool:
        """Relê a tela: se a câmera mudou (arrastar/zoom), recalibra. Devolve True se mudou. ~0,03 s se igual.
        Garante ilha enquadrada acima do rodapé; se não der, LEVANTA erro (a tarefa não deve clicar às cegas)."""
        c = None
        for attempt in range(2):
            try:
                c = autocal.auto_calibrate(self.page, self._grid_size(), None if force else self.calib, self.bias)
                break
            except Exception as e:  # noqa: BLE001 - normalmente uma janela por cima escondendo a ilha
                log.warning("[CALIB] não consegui reler a tela (%s); fechando janelas e tentando de novo", e)
                self.close_dialogs()
        if c is None:
            self.tool = None
            raise RuntimeError("calibração indisponível: a ilha não aparece (janela aberta por cima?)")
        changed = c is not self.calib
        self.calib, self.grid = c, c.grid
        h = self.page.viewport_size["height"] if self.page.viewport_size else config.VIEWPORT["height"]
        if c.yv > autocal.SAFE_YV * h:
            log.warning("[CALIB] a ilha está atrás da barra do rodapé (vértice y=%.0f); reenquadrando a câmera", c.yv)
            self.close_dialogs()                 # um clique no rodapé pode ter aberto o menu Friends
            self.frame_camera()                  # levanta erro se não conseguir
            changed = True
        return changed

    def frame_camera(self, measure: bool | None = None) -> None:
        """Enquadra a land (zoom + tile central no centro da tela) e calibra. Com `measure` (padrão: se ainda não há
        medida), LÊ os tiles da land pelo destaque do mouse: confirma quantos tiles ela tem e ajusta a grade exata."""
        import tileprobe

        def occupied() -> set[tuple[int, int]]:
            occ: set[tuple[int, int]] = set()             # tiles com prédio/planta/árvore/animal (e folga): não arrastar daí
            for o in self.link.objects():
                sx, sy = int(o.get("sx", 1) or 1), int(o.get("sy", 1) or 1)
                for i in range(-sx - 1, sx + 2):
                    for j in range(-sy - 1, sy + 2):
                        occ.add((o["x"] + i, o["y"] + j))
            return occ

        g = self._grid_size()
        self.calib = autocal.frame_camera(self.page, g, occupied=occupied())
        self.grid = self.calib.grid
        if measure is None:
            measure = self.bias is None or self.land_g is None
        if not measure:
            self.calib = autocal.calibrate_array(autocal.grab(self.page), g, self.bias)
            self.grid = self.calib.grid
            return
        try:
            self.hand()
            res = tileprobe.measure_land(self.page, g, self.link.objects())
        except Exception as e:  # noqa: BLE001
            log.warning("[CALIB] não consegui ler os tiles da land (%s); uso a grade pela borda da ilha", e)
            res = None
        if not res:
            log.warning("[CALIB] o jogo não mostrou o destaque dos tiles; uso a grade pela borda da ilha e o tamanho informado (%d)", g)
            return
        if res["g"] != g:
            log.info("[CALIB] a land tem %dx%d tiles (o jogo informava %d); reenquadrando", res["g"], res["g"], g)
            self.land_g = res["g"]
            self.calib = autocal.frame_camera(self.page, self.land_g, occupied=occupied())
            res = tileprobe.measure_land(self.page, self.land_g, self.link.objects()) or res
        self.land_g = res["g"]
        self.bias = res.get("bias")
        self.calib = autocal.calibrate_array(autocal.grab(self.page), self.land_g, self.bias)
        self.grid = self.calib.grid
        if self.bias:
            log.info("[CALIB] land %dx%d lida pelos tiles; grade exata: tile %.2f×%.2f px, correção da borda (%+.1f, %+.1f) px, erro médio %.1f px (%d tiles medidos)",
                     self.land_g, self.land_g, res["hw"], res["hh"], res["dxpx"], res["dypx"], res["rms"], res["n"])
        else:
            log.warning("[CALIB] land %dx%d lida pelos tiles, mas poucos tiles livres para ajustar a grade exata", self.land_g, self.land_g)

    # ---------------------------------------------------------------- calibração salva (feita uma vez)
    def system_info(self) -> dict:
        """Aspectos deste computador/navegador que influenciam a calibração (guardados em calibracao.json)."""
        import platform

        info = {"os": platform.platform(), "python": platform.python_version(),
                "viewport_usado": dict(config.VIEWPORT)}
        try:
            info["tela"] = self.page.evaluate("() => ({w: screen.width, h: screen.height, dpr: window.devicePixelRatio, "
                                              "ua: navigator.userAgent})")
        except Exception:  # noqa: BLE001
            pass
        try:
            cdp = self.page.context.new_cdp_session(self.page)
            win = cdp.send("Browser.getWindowForTarget")
            info["janela_navegador"] = win.get("bounds", {})
            cdp.detach()
        except Exception:  # noqa: BLE001
            pass
        return info

    def save_calibration(self) -> None:
        c = self.calib
        if c is None:
            return
        from datetime import datetime

        objs = self.link.objects()
        counts: dict[str, int] = {}
        for o in objs:
            counts[o["type"]] = counts.get(o["type"], 0) + 1
        userconfig.save_calibration({
            "version": 2, "locked": True, "created": datetime.now().isoformat(timespec="seconds"),
            "grid_size": self._grid_size(), "state_grid_size": self._state_grid_size(), "bias": self.bias,
            "system": self.system_info(),
            "camera": {"xv": c.xv, "yv": c.yv, "m": c.m, "hw": c.hw, "frac": c.frac, "grid": c.grid.to_dict()},
            "buildings": counts,
        })
        log.info("[CALIB] calibração salva em calibracao.json (não será refeita nas próximas vezes)")

    def use_saved_calibration(self) -> bool:
        """Reaproveita a calibração salva se a tela ainda bate com ela (ou se, depois de reenquadrar a câmera, ela fica igual).
        Devolve False se não existe/não serve (aí quem chama calibra e salva)."""
        d = userconfig.load_calibration()
        if not d:
            return False
        state_g = self._state_grid_size()
        if int(d.get("state_grid_size", d.get("grid_size", 0))) != state_g:
            log.info("[CALIB] o tamanho da land mudou (%s -> %s); preciso calibrar de novo", d.get("state_grid_size", d.get("grid_size")), state_g)
            return False
        self.bias = d.get("bias")
        self.land_g = int(d["grid_size"]) if self.bias else None
        k = d["camera"]
        saved = autocal.Calib(Grid.from_dict(k["grid"]), k["xv"], k["yv"], k["m"], k["hw"], k["frac"], self.bias)
        if autocal.still_valid(autocal.grab(self.page), saved):
            self.calib, self.grid = saved, saved.grid
            log.info("[CALIB] usando a calibração salva (land %dx%d)", self._grid_size(), self._grid_size())
            return True
        had_bias = self.bias is not None
        self.frame_camera()                       # câmera diferente (jogo reiniciou/arrastou): reenquadra (e mede, se faltar a medida)
        c = self.calib
        if had_bias and abs(c.xv - saved.xv) < 8 and abs(c.yv - saved.yv) < 8 and abs(c.hw - saved.hw) < 1.2:
            self.calib, self.grid = saved, saved.grid
            log.info("[CALIB] câmera reenquadrada; calibração salva mantida")
        else:
            log.info("[CALIB] câmera reenquadrada (posição diferente da salva; usando a leitura atual da tela)")
            if not had_bias:
                self.save_calibration()
        return True

    # ---------------------------------------------------------------- diálogos
    def pixel(self, pt) -> tuple[int, int, int]:
        from io import BytesIO

        from PIL import Image

        png = self.page.screenshot(clip={"x": max(0, pt[0] - 2), "y": max(0, pt[1] - 2), "width": 5, "height": 5})
        return Image.open(BytesIO(png)).convert("RGB").getpixel((2, 2))

    def mine_dialog_open(self) -> bool:
        """O botão grande do diálogo da mina (COLLECT/START RUN em verde, DIGGING em cinza) está na tela?"""
        if self.dry:
            return True
        if autocal.find_close_x(autocal.grab(self.page)) is None:      # sem o X vermelho de diálogo, nada está aberto
            return False
        x, y = self.ui("mine_main")
        s = self.ui_scale
        # o botão muda de largura/texto (START RUN + energia, COLLECT, DIGGING): olha vários pontos do lado esquerdo, fora do texto
        for ox, oy in ((-140, 0), (-130, -9), (-130, 9), (-115, -9), (-115, 9), (-100, 0)):
            r, g, b = self.pixel((x + ox * s, y + oy * s))
            green = g > 140 and r < 150 and b < 120 and g > r + 40
            grey = abs(r - g) < 22 and abs(g - b) < 30 and 100 < r < 190
            if green or grey:
                return True
        return False

    # ---------------------------------------------------------------- saco de sementes
    def sack_attached(self, x: float, y: float, half: int = 36) -> bool:
        """O saco de sementes está preso ao cursor? O jogo desenha o ícone do saco (creme) junto do ponteiro:
        ~400 pixels cremes na janela ao redor do cursor com o saco; <40 sem ele."""
        if self.dry:
            return True
        from io import BytesIO

        from PIL import Image

        png = self.page.screenshot(clip={"x": max(0, x - half), "y": max(0, y - half - 6),
                                         "width": 2 * half, "height": 2 * half})
        im = Image.open(BytesIO(png)).convert("RGB")
        n = 0
        for r, g, b in im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata():
            if r > 215 and g > 195 and b > 150 and r - b < 95 and abs(r - g) < 40:
                n += 1
        return n >= 150

    def ensure_sack(self, sack_pts, target) -> None:
        """Garante o saco no cursor sobre `target`; se soltou (Esc, diálogo, clique em tile plantado), clica
        no saco para prender de novo, testando pontos alternativos (`sack_pts`: lista de (x, y)) a cada tentativa.
        Nunca clica no saco se ele já está preso (isso o solta)."""
        if isinstance(sack_pts, tuple):
            sack_pts = [sack_pts]
        for attempt in range(2 * len(sack_pts)):
            if not self.dry:
                self.page.mouse.move(target[0], target[1], steps=5)
                time.sleep(0.25)
            if self.sack_attached(*target):
                return
            pt = sack_pts[attempt % len(sack_pts)]
            log.info("[SACO] fora do cursor; clicando no saco (tentativa %d, ponto %d)", attempt + 1, attempt % len(sack_pts))
            self.click(pt, jitter=1.0, note="prender saco")
        raise RuntimeError("não consegui prender o saco de sementes ao cursor")

    # ---------------------------------------------------------------- ferramentas
    def select_tool(self, tool: str, area: str | None = None) -> None:
        key = (tool, area)
        if self.tool == key:
            return
        self.click_ui("tool_button")
        self.click_ui(f"tool_{tool}")
        if area:
            self.click_ui(f"area_{area}")
        self.tool = key
        log.info("[FERRAMENTA] %s %s", tool.upper(), area or "")

    def hand(self) -> None:
        self.select_tool("hand")

    # ---------------------------------------------------------------- energia
    def energy(self) -> float:
        """Energia estimada = último estado salvo + gastos/compras feitos depois dele."""
        t = (self.link.t or 0) / 1000.0
        self.events = [e for e in self.events if e[0] > t]
        return min(config.ENERGY_MAX, self.link.energy + sum(d for _, d in self.events))

    def spend(self, n: float) -> None:
        self.events.append((time.time(), -n))

    def _fresh_energy(self) -> float:
        """Energia REAL: espera o próximo salvamento do jogo (a estimativa ignora a recarga natural e erra para baixo)."""
        if self.dry:
            return self.energy()
        try:
            self.link.refresh(timeout=30)
        except Exception:  # noqa: BLE001
            self.link.snapshot()
        return self.energy()

    def ensure_energy(self, need: float) -> None:
        """Garante energia para a próxima ação (o custo real dela). COMPRA SÓ quando a energia REAL é <= ENERGY_BUY_AT (6) e
        não paga a ação; acima disso NUNCA compra: levanta EnergyWait (a tarefa para sem gastar nada e volta no próximo ciclo)."""
        self.link.snapshot()
        if self.energy() >= need:
            return
        e = self._fresh_energy()                          # confirma com a leitura real antes de decidir
        if e >= need:
            return
        if e > config.ENERGY_BUY_AT or not self.auto_energy:
            raise EnergyWait(f"energia {e:.0f} < {need:.0f} para a ação; não compro (só compro com {config.ENERGY_BUY_AT} ou menos); "
                             "espero a recarga natural")
        self.buy_energy()

    def affordable(self, per_unit: float, want: int) -> int:
        """Quantas unidades (de `per_unit` de energia cada) dá para pagar AGORA, até `want`. Compra só se a energia real <= 6."""
        if self.energy() < per_unit:
            try:
                self.ensure_energy(per_unit)
            except EnergyWait:
                return 0
        return max(0, min(want, int(self.energy() // max(per_unit, 1))))

    def buy_energy(self) -> None:
        e0 = self._fresh_energy()
        if e0 > config.ENERGY_BUY_AT:                     # trava final: nunca compra acima do limite
            raise EnergyWait(f"energia {e0:.0f} > {config.ENERGY_BUY_AT}: compra cancelada")
        log.info("[ENERGIA] energia %.0f (acabou); comprando pacote BEST VALUE (token de testnet)", e0)
        self.click_ui("energy_plus")
        self.click_ui("energy_best_buy")
        self.pause(1.5, 2.5)
        self.click_ui("energy_close")
        self.tool = None
        self.escape()
        e1 = self._fresh_energy()
        if not self.dry and e1 < e0 + 20:
            raise RuntimeError(f"a compra de energia não aumentou a energia ({e0:.0f} -> {e1:.0f}); confira o saldo de tokens e o botão")
        log.info("[ENERGIA] energia %.0f -> %.0f", e0, e1)


    # ---------------------------------------------------------------- recuperação
    def restart_session(self) -> None:
        """Sem salvamentos novos há muito tempo: recarrega o jogo, entra de novo, reenquadra e recalibra."""
        from gamestate import FarmModel

        log.warning("[SESSÃO] sem salvamentos do jogo há muito tempo; recarregando a página")
        self.page.reload()
        time.sleep(10)
        self.link.model = FarmModel()
        self.link.n = 0
        self.tool = None
        enter_game(self.page, self.link)
        self.frame_camera()

    def recover(self) -> None:
        """Fecha diálogos e volta para a mão depois de um erro."""
        self.escape(2)
        self.tool = None
        try:
            self.hand()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.calib is not None:
                self.recalibrate(force=True)      # câmera/ilha fora do lugar? reenquadra antes da próxima tarefa
        except Exception as e:  # noqa: BLE001
            log.error("[CALIB] reenquadramento na recuperação falhou: %s", e)


def load_grid() -> Grid:
    if not CALIB_FILE.exists():
        raise SystemExit("calibration.json não encontrado. Rode primeiro: python calibrate.py")
    return Grid.from_dict(json.loads(CALIB_FILE.read_text("utf-8"))["grid"])
