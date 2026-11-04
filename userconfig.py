"""Configuração do usuário (quais construções/rotinas o bot deve rodar) e calibração salva.

Arquivos (na pasta do bot):
  config_usuario.json  -> {"modules": {"cow": true, "pig": false, ...}}   (feito pela tela de configuração)
  calibracao.json      -> calibração da tela/land deste computador (feita uma vez; depois só é reaproveitada)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("fh")

HERE = Path(__file__).resolve().parent
USER_FILE = HERE / "config_usuario.json"
CALIB_FILE = HERE / "calibracao.json"

# Catálogo de rotinas/construções que aparecem na tela de configuração.
#   key: chave guardada | label: nome | desc: o que o bot faz | types: tipos de objeto no estado do jogo (detecção)
#   auto: False = só listada (o bot ainda não automatiza) | default: ligado por padrão
CATALOG = [
    {"key": "feed_factory", "label": "Fábrica de ração", "icon": "🏭", "types": ["feed_factory"], "group": "Produção",
     "desc": "Coleta a ração pronta e fabrica a de menor estoque (máx. 3 de cada)."},
    {"key": "cow", "label": "Vaca (Cow Farm)", "icon": "🐄", "types": ["cow"], "group": "Animais",
     "desc": "Coleta o leite e alimenta com ração de vaca."},
    {"key": "chickencoop", "label": "Galinheiro (Chicken Farm)", "icon": "🐔", "types": ["chickencoop"], "group": "Animais",
     "desc": "Coleta os ovos e alimenta com ração de galinha."},
    {"key": "pig", "label": "Porcos (Pig Farm)", "icon": "🐖", "types": ["pig"], "group": "Animais",
     "desc": "Coleta a carne e alimenta com ração de porco."},
    {"key": "mining_station", "label": "Mina", "icon": "⛏️", "types": ["mining_station"], "group": "Produção",
     "desc": "Coleta os minérios e inicia nova corrida (20 min)."},
    {"key": "beehouse", "label": "Colmeias (mel)", "icon": "🍯", "types": ["beehouse"], "group": "Produção",
     "desc": "Coleta o mel clicando na base de cada colmeia."},
    {"key": "windmill", "label": "Moinho (farinhas)", "icon": "🌬️", "types": ["windmill"], "group": "Casas de craft",
     "desc": "Mói 5 colheitas em 1 farinha (trigo, milho, arroz...). Só usa o excedente das colheitas."},
    {"key": "dairy", "label": "Laticínio", "icon": "🧀", "types": ["dairy"], "group": "Casas de craft",
     "desc": "Transforma leite em queijo, manteiga e creme (fila de 4)."},
    {"key": "bakery", "label": "Padaria", "icon": "🥐", "types": ["bakery"], "group": "Casas de craft",
     "desc": "Assa pães e bolos quando há ingredientes (farinhas, ovos, mel, manteiga...)."},
    {"key": "delivery", "label": "Entregas (pedidos)", "icon": "🚚", "types": ["delivery"], "group": "Pedidos e missões",
     "desc": "Produz o que falta e envia os pedidos de entrega (troca itens por ouro/XP). Pula pedidos com peixe ou itens que o bot não produz."},
    {"key": "taskboard", "label": "Task Board (missões)", "icon": "📋", "types": [], "group": "Pedidos e missões",
     "desc": "Lê as missões em aberto (Deliver 5 cheese...), produz o necessário e entrega. Não precisa de construção."},
    {"key": "trees", "label": "Árvores", "icon": "🌳", "types": ["tree"], "group": "Terra",
     "desc": "Corta as árvores prontas (folhas e troncos)."},
    {"key": "crops", "label": "Plantação", "icon": "🌽", "types": ["soil"], "group": "Terra",
     "desc": "Colhe, replanta e rega o canteiro."},
]
KEYS = [c["key"] for c in CATALOG]


def default_modules() -> dict[str, bool]:
    return {c["key"]: bool(c.get("default", True)) for c in CATALOG}


def load_modules() -> dict[str, bool]:
    mods = default_modules()
    if USER_FILE.exists():
        try:
            saved = json.loads(USER_FILE.read_text("utf-8-sig")).get("modules", {})
            for k, v in saved.items():
                if k in mods:
                    mods[k] = bool(v)
        except Exception as e:  # noqa: BLE001
            log.warning("[CONFIG] config_usuario.json ilegível (%s); usando o padrão", e)
    return mods


def save_modules(mods: dict[str, bool]) -> None:
    clean = {k: bool(mods.get(k, True)) for k in KEYS}
    USER_FILE.write_text(json.dumps({"version": 1, "modules": clean}, indent=2), encoding="utf-8")


def load_calibration() -> dict | None:
    if CALIB_FILE.exists():
        try:
            d = json.loads(CALIB_FILE.read_text("utf-8-sig"))
            if d.get("version") == 2 and d.get("camera"):
                return d
        except Exception as e:  # noqa: BLE001
            log.warning("[CALIB] calibracao.json ilegível (%s)", e)
    return None


def save_calibration(d: dict) -> None:
    CALIB_FILE.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
