"""Atualizador do Farm Helper: baixa a versão mais recente do repositório e troca os arquivos do programa.

Não precisa de git. Baixa o .zip da branch principal, copia por cima os arquivos do programa e NUNCA mexe nos seus
dados (calibração, configuração, módulos escolhidos, URL do jogo, preferências, logs).
"""
from __future__ import annotations

import io
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

REPO = "cleytoncl3/farm-helper"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
ZIP = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
VERSION_FILE = "version.txt"

# arquivos do usuário: nunca sobrescritos, mesmo que existam no repositório
PROTECTED = {
    "calibracao.json", "calibracao_preview.png", "config_usuario.json", "game_url.txt", "python.txt", "browser.json",
    "app_prefs.json", "memory.json", "bot.log", "STOP", VERSION_FILE, "Farm Helper.exe.old",
}
PROTECTED_DIRS = {"profile", ".venv", "__pycache__", ".git", "build_tmp"}
UA = {"User-Agent": "farm-helper-updater"}


def _get(url: str, timeout: float = 30.0) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def local_version(folder: Path) -> str:
    f = folder / VERSION_FILE
    try:
        return f.read_text("utf-8").strip()
    except OSError:
        return ""


def latest_version() -> str:
    """Código (SHA) do último commit publicado; levanta erro se não houver internet."""
    return json.loads(_get(API, 15).decode("utf-8"))["sha"]


def check(folder: Path) -> tuple[bool, str, str]:
    """(há_atualização, versão_local, versão_nova)."""
    new = latest_version()
    cur = local_version(folder)
    return new != cur, cur, new


def apply(folder: Path, new_sha: str | None = None, log=print) -> list[str]:
    """Baixa e instala a versão mais recente em `folder`. Devolve a lista de arquivos trocados."""
    new_sha = new_sha or latest_version()
    log("Baixando a versão %s..." % new_sha[:7])
    data = _get(ZIP, 120)
    changed: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        root = names[0].split("/")[0] + "/"
        for n in names:
            rel = n[len(root):]
            parts = Path(rel).parts
            if not parts or parts[0] in PROTECTED_DIRS or Path(rel).name in PROTECTED:
                continue
            dest = folder / rel
            blob = z.read(n)
            if dest.exists() and dest.read_bytes() == blob:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".new")
            tmp.write_bytes(blob)
            try:
                tmp.replace(dest)
            except PermissionError:
                # arquivo em uso (o .exe aberto): o Windows deixa RENOMEAR um .exe em execução; põe o novo no lugar
                old = dest.with_name(dest.name + ".old")
                try:
                    old.unlink(missing_ok=True)
                    dest.rename(old)
                    tmp.replace(dest)
                    log("%s trocado; feche e abra o app para usar a versão nova" % rel)
                except OSError:
                    tmp.unlink(missing_ok=True)
                    log("Não consegui trocar %s (em uso); feche o app e atualize de novo" % rel)
                    continue
            changed.append(rel)
    (folder / VERSION_FILE).write_text(new_sha, encoding="utf-8")
    log("Arquivos atualizados: %d" % len(changed))
    return changed


if __name__ == "__main__":                                # teste: python updater.py [pasta]
    import sys
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    has, cur, new = check(dest)
    print("local:", cur[:7] or "(desconhecida)", "| nova:", new[:7], "| atualização:", has)
    if has and len(sys.argv) > 2 and sys.argv[2] == "apply":
        print(apply(dest, new))
