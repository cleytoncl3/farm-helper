"""Farm Helper: laço principal.

Ordem de cada ciclo: ração -> animais -> mina -> mel -> árvores -> plantas (colher, replantar, regar).
Decide pelo estado real do jogo; compra energia (token de testnet) quando acaba, se permitido.
Erros aparecem no prompt (e em bot.log) com o motivo; o bot tenta se recuperar e continua.
Pare com Ctrl+C ou criando um arquivo STOP na pasta.
"""
import argparse
import logging
import time

from playwright.sync_api import Error as PlaywrightError

import config
import crafts
import orders
import tasks
import userconfig
from game import EnergyWait, Game, StopRequested, banner, enter_game, load_grid, open_game, setup_logging
from gamestate import Link

log = logging.getLogger("fh")
CHECK_EVERY = config.CHECK_EVERY   # segundos entre verificações do estado (config.py)
STALE_AFTER = 150        # sem salvamento do jogo por tanto tempo => recarrega a sessão
MAX_FAILS = 6            # falhas seguidas de uma mesma tarefa antes de avisar em destaque


# passo do laço -> chave da rotina em config_usuario.json
STEP_MODULE = {"factory": "feed_factory", "mine": "mining_station", "honey": "beehouse", "animals": "animals"}


def safe(g: Game, name: str, fn, fails: dict) -> bool:
    try:
        fn(g)
        fails[name] = 0
        return True
    except (StopRequested, KeyboardInterrupt):
        raise
    except EnergyWait as e:                            # sem energia e sem permissão de comprar: não é erro; tenta no próximo ciclo
        log.info("[ENERGIA] %s: %s", name, e)
        try:
            g.recover()
        except Exception:  # noqa: BLE001
            pass
        return False
    except PlaywrightError as e:
        fails[name] = fails.get(name, 0) + 1
        log.error("[ERRO] %s: falha no navegador (%s); recuperando", name, e.message.splitlines()[0])
    except Exception as e:  # noqa: BLE001 - o bot não deve parar por um lag
        fails[name] = fails.get(name, 0) + 1
        log.error("[ERRO] %s: %s: %s; recuperando", name, type(e).__name__, e, exc_info=True)
    if fails.get(name, 0) >= MAX_FAILS:
        log.error("[ATENÇÃO] a tarefa '%s' falhou %d vezes seguidas; confira a tela do jogo / a câmera", name, fails[name])
    try:
        g.recover()
    except Exception as e:  # noqa: BLE001
        log.error("[ERRO] recuperação falhou: %s", e)
    return False


def status_line(g: Game) -> str:
    soils = g.link.objects("soil")
    plant = [o for o in soils if o["data"].get("crop")]
    ready = sum(1 for o in plant if o["data"]["crop"].get("harvestable"))
    dry = sum(1 for o in plant if not o["data"]["crop"].get("watered"))
    return f"energia {g.energy():.0f} · plantas {len(plant)} (prontas {ready} · secas {dry} · vazias {len(soils) - len(plant)})"


def run_loop(g: Game, skip=(), once: bool = False, deadline: float | None = None,
             due: dict | None = None) -> dict:
    """Roda ciclos até STOP/Ctrl+C, `once` ou `deadline` (epoch). Devolve `due` para continuar depois."""
    link = g.link
    due = due if due is not None else {"factory": 0.0, "mine": 0.0, "honey": 0.0}
    fails: dict[str, int] = {}
    last_status = ['', 0.0]
    g.service_fn = tasks.run_services     # timers vencidos (animais/mina) interrompem tarefas longas e depois elas retomam
    try:
        while True:
            g.check_stop()
            link.snapshot()
            if link.t and time.time() - link.t / 1000 > STALE_AFTER and not once:
                try:
                    g.restart_session()
                except Exception as e:  # noqa: BLE001
                    log.error("[ERRO] não consegui recarregar a sessão: %s", e, exc_info=True)
                    time.sleep(20)
                continue
            now = time.time()
            if g.request_factory and now - g.last_factory > 120:   # colheu ingrediente de ração: reconsulta já
                due["factory"] = 0.0
                g.request_factory = False
                log.info("[BOT] colheita de ingrediente: vou reconferir inventário e fábrica agora")
            fac_when = due["factory"]
            try:    # algum animal com produto pronto/fome? então olha a fábrica ANTES (coleta ração e lê o inventário)
                if now - g.last_factory > 20 and any(tasks.animal_due(g, o, now) for t in config.ANIMALS for o in link.objects(t)):
                    fac_when = 0.0
            except Exception:  # noqa: BLE001
                pass
            steps = [
                # ordem de verificação: mina, vaca, galinha, porco, fábrica de ração, (animais de novo com a ração
                # nova), mel, árvores e por último as plantas
                ("factory", tasks.feed_factory, fac_when, config.INTERVAL_FACTORY),   # antes dos animais: coleta a ração pronta
                ("mine", tasks.mine, 0, 0),       # só age quando o timer da mina venceu
                ("animals", tasks.animals, 0, 0),
                ("honey", tasks.honey, g.honey_next, 0),
                ("orders", orders.orders, 0, 0),       # missões e pedidos (lê o que falta; as casas de craft produzem primeiro isso)
                ("windmill", crafts.windmill, 0, 0),
                ("dairy", crafts.dairy, 0, 0),
                ("bakery", crafts.bakery, 0, 0),
                ("trees", tasks.trees, 0, 0),
                ("crops", tasks.crops, 0, 0),
            ]
            for name, fn, when, interval in steps:
                if name in skip or now < when or STEP_MODULE.get(name, name) in g.disabled:
                    continue
                if g.calib is not None:
                    g.recalibrate()          # a câmera pode ter sido arrastada: relê a tela (~0,03 s se igual)
                ok = safe(g, name, fn, fails)
                if name == "factory":
                    g.last_factory = time.time()
                    again = min(interval, g.factory_next) if (ok and g.factory_next) else interval
                    due[name] = time.time() + (again if ok else max(60, interval // 4))   # volta quando a fila terminar
                elif interval:               # em caso de erro tenta de novo em 1/4 do intervalo
                    due[name] = time.time() + (interval if ok else max(60, interval // 4))
                link.snapshot()
            if once:
                break
            st = status_line(g)                       # só mostra quando mudar (ou a cada 2 min) para o prompt ficar limpo
            key = st.split(" · ", 1)[-1]                  # sem a energia (muda a cada poucos segundos e polui o log)
            if key != last_status[0] or time.time() - last_status[1] > 120:
                log.info("[STATUS] %s", st)
                last_status[0], last_status[1] = key, time.time()
            for _ in range(CHECK_EVERY):
                g.check_stop()
                if deadline and time.time() >= deadline:
                    return due
                time.sleep(1)
            if deadline and time.time() >= deadline:
                break
    except (StopRequested, KeyboardInterrupt):
        log.info("[BOT] Parado pelo usuário")
        raise
    return due


_PW = [None]      # conexão Playwright atual (encerrada antes de reconectar)


def build(args):
    if _PW[0] is not None:
        try:
            _PW[0].stop()
        except Exception:  # noqa: BLE001
            pass
        _PW[0] = None
    _PW[0], page = open_game(args.cdp or None, args.profile, args.exe)
    link = Link(page)
    log.info("[BOT] Aguardando o jogo e o primeiro estado salvo...")
    enter_game(page, link)
    g = Game(page, link, None, auto_energy=not args.no_energy, dry=args.dry)
    mods = userconfig.load_modules()                      # rotinas ligadas/desligadas na tela de configuração
    g.disabled = {k for k, v in mods.items() if not v}
    config.ANIMALS = tuple(t for t in ("cow", "chickencoop", "pig") if mods.get(t, True))
    if not config.ANIMALS:
        g.disabled.add("animals")
    if g.disabled:
        log.info("[CONFIG] rotinas desligadas: %s", ", ".join(sorted(g.disabled)))
    if args.manual_calib:
        g.grid = load_grid()
    elif args.keep_camera:
        g.recalibrate(force=True)
    elif args.recalibrate or not g.use_saved_calibration():
        g.frame_camera()                                    # 1ª vez neste computador (ou --recalibrate): calibra e salva
        g.save_calibration()
    log.info("[BOT] Iniciado (energia %.0f). Crie um arquivo STOP ou use Ctrl+C para parar.", link.energy)
    return g


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://localhost:9222", help="URL CDP ('' abre navegador próprio)")
    ap.add_argument("--profile", default="profile", help="pasta do perfil (modo próprio)")
    ap.add_argument("--exe", default=None, help="caminho do brave.exe/chrome.exe (modo próprio)")
    ap.add_argument("--once", action="store_true", help="roda um ciclo e sai")
    ap.add_argument("--dry", action="store_true", help="só registra os cliques, sem clicar")
    ap.add_argument("--no-energy", action="store_true", help="não comprar energia (espera a recarga)")
    ap.add_argument("--skip", default="", help="tarefas a pular: factory,animals,mine,honey,orders,windmill,dairy,bakery,trees,crops")
    ap.add_argument("--manual-calib", action="store_true", help="usa calibration.json (calibrate.py) em vez da automática")
    ap.add_argument("--verbose", action="store_true", help="mostra no prompt também os detalhes (calibração, ferramentas, saco)")
    ap.add_argument("--recalibrate", action="store_true", help="refaz a calibração salva (calibracao.json)")
    ap.add_argument("--keep-camera", action="store_true", help="não mexer no zoom/posição; só calibrar pela tela")
    args = ap.parse_args()
    setup_logging(args.verbose)
    banner()
    # Trava de instância única: dois bots no mesmo jogo recarregam/atrapalham a página um do outro
    # ("Frame was detached"). A porta fica presa enquanto este processo viver.
    import socket
    global _LOCK
    _LOCK = socket.socket()
    try:
        _LOCK.bind(("127.0.0.1", 47653))
    except OSError:
        log.error("[BOT] Já existe outro bot rodando (outra janela do iniciar_bot.bat?). Feche-o antes de abrir este.")
        raise SystemExit(1)
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    attempt = 0
    while True:
        try:
            g = build(args)
            attempt = 0
            run_loop(g, skip=skip, once=args.once)
            return
        except (StopRequested, KeyboardInterrupt):
            return
        except Exception as e:  # noqa: BLE001 - navegador fechou, porta 9222 caiu etc.
            attempt += 1
            log.error("[FATAL] %s: %s", type(e).__name__, e, exc_info=True)
            if attempt >= 5:
                log.error("[FATAL] 5 tentativas seguidas falharam. Confira: (1) o Brave/Chrome está aberto pelo iniciar_bot.bat (start_browser.ps1)? "
                          "(2) você está logado no jogo? (3) a porta 9222 responde? Encerrando.")
                raise SystemExit(1)
            log.error("[FATAL] tentando reconectar em 30 s (tentativa %d/5)...", attempt)
            time.sleep(30)


if __name__ == "__main__":
    main()
