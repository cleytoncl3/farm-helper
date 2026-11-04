"""Leitura do estado real do jogo.

O jogo (Godot/WASM num iframe) salva com POST /api/player/state a cada ~20 s (e quando algo muda).
Um gancho em fetch/XHR dentro do iframe põe cada corpo JSON numa fila (window.__fhQ); o Python reconstrói o estado:

- formato antigo: `farm_objects` (lista completa de objetos);
- formato atual: `farm_compact` (completo, solo em linhas curtas) e `farm_delta` (só o que mudou).
  Linha de solo: [x, y, 0] = vazio; [x, y, flags, tipo_da_cultura, regado_em, segundos_decorridos,
  adubo, segundos_de_adubo] = plantado. flags: bit1 = regado, bit4 = pronto (7 regado+pronto, 3 crescendo, 0 seco).

O bot NUNCA lê nem usa a sessão/credenciais; só observa o corpo de salvamento.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("fh")

HOOK_JS = r"""
(() => {
  if (window.__fhHook) return;
  window.__fhHook = true;
  window.__fhN = 0;
  window.__fhQ = [];
  const dec = (b) => {
    try {
      if (b == null) return '';
      if (typeof b === 'string') return /^\d+(,\d+)+$/.test(b) ? new TextDecoder().decode(Uint8Array.from(b.split(',').map(Number))) : b;
      if (b instanceof ArrayBuffer) return new TextDecoder().decode(new Uint8Array(b));
      if (ArrayBuffer.isView(b)) return new TextDecoder().decode(new Uint8Array(b.buffer, b.byteOffset, b.byteLength));
    } catch (e) {}
    return '';
  };
  const keep = (url, method, body) => {
    if (!/\/api\/player\/state/.test(String(url)) || String(method || 'GET').toUpperCase() !== 'POST') return;
    try { const j = JSON.parse(dec(body)); if (j && typeof j === 'object') { window.__fhQ.push({ t: Date.now(), j }); window.__fhN++; } } catch (e) {}
  };
  // Respostas de consulta do próprio jogo (só leitura): receitas/estoque da fábrica, celeiro, animais.
  window.__fhApi = {};
  window.__fhCalls = [];       // só método + caminho + status das chamadas (sem corpo, sem cabeçalhos): confirma o efeito de um clique
  const note = (m, u, s) => {
    try {
      const x = String(u).match(/\/api\/[^?]*/);
      if (!x || /player\/state|sync-pulse/.test(x[0])) return;
      window.__fhCalls.push({ t: Date.now(), m: String(m || 'GET').toUpperCase(), u: x[0], s });
      if (window.__fhCalls.length > 80) window.__fhCalls.shift();
    } catch (e) {}
  };
  const API_RE = /\/api\/(?!player\/state)/;
  const of = window.fetch;
  window.fetch = function (u, o) {
    let p;
    try { keep((u && u.url) || u, (o && o.method) || (u && u.method), o && o.body); } catch (e) {}
    p = of.apply(this, arguments);
    try {
      const url0 = String((u && u.url) || u), meth0 = (o && o.method) || (u && u.method);
      p.then(r => note(meth0, url0, r.status)).catch(() => {});
    } catch (e) {}
    try {
      const url = String((u && u.url) || u);
      const m = url.match(/\/api\/[^?]*/);
      if (m && API_RE.test(url) && String((o && o.method) || 'GET').toUpperCase() === 'GET')
        p.then(r => r.clone().json()).then(j => { window.__fhApi[m[0]] = { t: Date.now(), j }; }).catch(() => {});
    } catch (e) {}
    return p;
  };
  const op = XMLHttpRequest.prototype.open, sd = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__u = u; this.__m = m; return op.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) {
    try { keep(this.__u, this.__m, b); } catch (e) {}
    try { this.addEventListener('loadend', () => note(this.__m, this.__u, this.status)); } catch (e) {}
    return sd.apply(this, arguments);
  };
})();
"""

DRAIN_JS = "() => { const q = window.__fhQ || []; window.__fhQ = []; return { n: window.__fhN || 0, q }; }"
COUNT_JS = "() => window.__fhN || 0"


def _key(o) -> tuple:
    if isinstance(o, list):
        return ("soil", o[0], o[1])
    if o.get("type") == "tree" and isinstance(o.get("data"), dict) and o["data"].get("tree_id"):
        return ("tree", o["data"]["tree_id"])
    return (o.get("type"), o.get("x"), o.get("y"))


class FarmModel:
    """Estado reconstruído a partir dos salvamentos (completo + deltas)."""

    def __init__(self):
        self.objs: dict[tuple, dict] = {}
        self.kinds: list[str] = []
        self.top: dict = {}
        self.ready = False

    def _norm(self, o):
        if isinstance(o, dict):
            return o
        x, y = o[0], o[1]
        if len(o) < 8:
            return {"type": "soil", "x": x, "y": y, "sx": 1, "sy": 1, "data": {"watered": False}}
        flags, kind, t, elapsed, fert, fert_s = o[2], o[3], float(o[4]), float(o[5]), o[6], o[7]
        name = self.kinds[kind] if 0 <= kind < len(self.kinds) else f"kind{kind}"
        watered = bool(flags & 1) or t > 0
        # flags: bit1 = regado, bit4 = pronto para colher (7 = regado+pronto, 3 = regado crescendo, 0 = seco).
        # elapsed = tempo de crescimento acumulado (s), limitado ao total da cultura (milho 1500, cana 2400).
        crop = {"type": name, "watered": watered, "watered_at": t, "elapsed": elapsed, "flags": flags,
                "harvestable": bool(flags & 4), "fertilizer": fert, "fertilizer_seconds": fert_s}
        return {"type": "soil", "x": x, "y": y, "sx": 1, "sy": 1, "data": {"watered": watered, "crop": crop}}

    def apply(self, body: dict) -> None:
        for k in ("energy", "energy_spent", "gold", "version", "grid_data"):
            if k in body:
                self.top[k] = body[k]
        if "farm_objects" in body:                           # formato antigo
            self.objs = {_key(o): self._norm(o) for o in body["farm_objects"]}
            self.ready = True
        if "farm_compact" in body:                           # estado completo compacto
            fc = body["farm_compact"]
            self.kinds = fc.get("kinds", [])
            self.objs = {_key(o): self._norm(o) for o in fc.get("e", [])}
            self.ready = True
        if "farm_delta" in body and self.ready:              # só mudanças
            fd = body["farm_delta"]
            if fd.get("kinds"):
                self.kinds = fd["kinds"]
            for rm in fd.get("removes", []):
                self._remove(rm)
            for up in fd.get("upserts", []):
                o = up[1] if isinstance(up, list) and len(up) == 2 and up[1] is not None else up
                try:
                    self.objs[_key(o)] = self._norm(o)
                except Exception as e:  # noqa: BLE001
                    log.warning("[ESTADO] upsert não entendido: %.120s (%s)", o, e)

    def _remove(self, rm) -> None:
        keys = []
        if isinstance(rm, list) and len(rm) >= 2 and all(isinstance(v, int) for v in rm[:2]):
            keys.append(("soil", rm[0], rm[1]))
        elif isinstance(rm, str):
            keys.append(("tree", rm))
        elif isinstance(rm, dict):
            keys.append(_key(rm))
        for k in keys:
            self.objs.pop(k, None)
        if not keys:
            log.warning("[ESTADO] remoção não entendida: %.120s", rm)

    def objects(self) -> list[dict]:
        return list(self.objs.values())


class Link:
    """Ponte com o iframe do jogo."""

    def __init__(self, page):
        self.page = page
        self.n = 0
        self.t = 0.0                    # ms (relógio do navegador) em que o último salvamento chegou
        self.model = FarmModel()

    @property
    def state(self) -> dict | None:
        return {"energy": self.model.top.get("energy", 0), "grid_data": self.model.top.get("grid_data", {}),
                "gold": self.model.top.get("gold", 0), "farm_objects": self.model.objects()} if self.model.ready else None

    def frame(self):
        for f in self.page.frames:
            if "/game/" in f.url:
                return f
        raise RuntimeError("iframe do jogo não encontrado (o jogo carregou?)")

    def _eval(self, js: str, arg=None):
        """evaluate no iframe do jogo, tolerando o frame ser trocado/recarregado (ex.: logo depois do PLAY)."""
        for i in range(8):
            try:
                return self.frame().evaluate(js, arg)
            except Exception as e:  # noqa: BLE001 - "Frame was detached", contexto destruído etc.
                if i == 7:
                    raise
                log.debug("[ESTADO] iframe indisponível (%s); tentando de novo", str(e).splitlines()[0])
                time.sleep(1.0)

    def _drain(self) -> int:
        r = self._eval(DRAIN_JS)
        for item in r["q"]:
            self.model.apply(item["j"])
            self.t = float(item["t"])
        self.n = r["n"]
        return len(r["q"])

    def snapshot(self) -> dict | None:
        self._drain()
        return self.state

    def refresh(self, timeout: float = 50.0) -> dict:
        """Espera o PRÓXIMO salvamento do jogo (posterior a esta chamada): estado mais novo, até ~20 s."""
        self._drain()
        base = self._eval(COUNT_JS)              # só vale um salvamento POSTERIOR a esta chamada
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._eval(COUNT_JS) > base:
                self._drain()
                if self.model.ready:
                    return self.state
            time.sleep(1.0)
        if self.model.ready:
            return self.state
        raise TimeoutError("nenhum estado recebido do jogo")

    def api(self, path: str) -> dict | None:
        """Última resposta capturada de uma consulta do jogo, ex. '/api/factory/state' -> {'t': ms, 'j': {...}}."""
        return self._eval("(p) => (window.__fhApi || {})[p] || null", path)

    def calls(self, after_ms: float = 0.0) -> list[dict]:
        """Chamadas de API feitas pelo jogo depois de `after_ms` (método, caminho, status): confirma o que um clique fez."""
        try:
            return [c for c in (self._eval("() => window.__fhCalls || []") or []) if c["t"] > after_ms]
        except Exception:  # noqa: BLE001
            return []

    def wait_api(self, path: str, after_ms: float, timeout: float = 6.0) -> dict | None:
        """Espera uma resposta MAIS NOVA que `after_ms`; se não vier, devolve a última conhecida (ou None)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.api(path)
            if r and r["t"] > after_ms:
                return r
            time.sleep(0.4)
        return self.api(path)

    def inventory(self, after_ms: float = 0.0, timeout: float = 6.0) -> dict[str, int] | None:
        """Quantidade de cada item (feed_cow, harvest_wheat, ...) lida da consulta de inventário do próprio jogo.
        O jogo só a consulta quando o celeiro (Inventário) é aberto; `after_ms` exige uma resposta mais nova."""
        r = self.wait_api("/api/player/inventory", after_ms, timeout)
        if not r or not isinstance(r.get("j"), dict):
            return None
        return {it["item_id"]: int(it.get("quantity", 0)) for it in r["j"].get("items", [])}

    def objects(self, *types: str) -> list[dict]:
        objs = self.model.objects() if self.model.ready else []
        return [o for o in objs if not types or o["type"] in types]

    @property
    def energy(self) -> float:
        return float(self.model.top.get("energy", 0))
