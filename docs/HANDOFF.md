# Handoff — aprendizados sobre o jogo

Atualizado em 2026-11-03 depois de jogar a rotina completa pelo navegador (Claude in Chrome).

## Descobertas que mudaram o bot

- O jogo é **Godot/WASM** dentro de um iframe same-origin; não há objetos JS com o estado.
- O estado inteiro sai em `POST /api/player/state` (~20 s), corpo JSON enviado como bytes. Chaves:
  `energy, energy_spent, farm_objects, gold, grid_data{grid_size:19}, version, world_owner`.
  Cada `farm_objects[i]`: `{type, x, y, sx, sy, data}`.
- Tipos vistos: `soil, tree, seed_sack, feed_factory, barn, mining_station, windmill, delivery, cow, dairy,
  bakery, chickencoop, pig, beehouse`.
  - `soil.data = {watered, crop?{type, elapsed, harvestable, watered, watered_at, fertilizer...}}` (tile de terra;
    `crop` presente = plantado).
  - Animais: `data.state` 1 = produzindo (com `ready_at`), 2 = pronto (bolha), depois de coletar precisa alimentar.
  - `tree.data = {ready, ready_at, regrowing, slug, tree_id}`.
  - `seed_sack.data = {count, seed_type}` (saco solto na grama).
- O canteiro real é uma grade **9x9** (x,y 10..18) + horta 3x2 (16..18, 7..8). O buraco no meio (14,14) é o espantalho.
- Não dá para ler corpos de requisição pela ferramenta de rede; o gancho em `fetch`/XHR dentro do iframe dá o JSON.
  Não ler nem usar `test_session` (sessão).
- Chamadas observadas (nomes): `/api/xp/gain`, `/api/trees/harvest {tree_id}`, `/api/factory/frame`,
  `/api/stats/increment`, `/api/shop/buy`, `/api/player/inventory/consume`, `/api/mining/state`, etc.

## Atualização 2026-11-03 (tarde): formato de salvamento e calibração

- O jogo passou a enviar `farm_compact` (completo) no 1º POST e `farm_delta` (`upserts`/`removes`) depois; corpos
  vêm como `Int8Array`. Linha de solo: `[x,y,0]` vazio; `[x,y,flags,tipo,regado_em,decorrido,adubo,seg_adubo]`
  plantado (flags: 1 regado, 4 pronto; 7 = regado+pronto, 3 = crescendo, 0 = seco; decorrido limitado ao total:
  milho 1500 s, cana 2400 s). `kinds` = nomes das culturas. Itens não-solo seguem como dicts.
- O jogo só salva quando algo muda/periodicamente; o botão de salvar do HUD não força o POST de forma confiável.
- Calibração automática: borda frontal da ilha (grama→penhasco) forma um "V"; vértice de baixo = canto do tile
  (18,18) **mais um lábio de grama de ~0,77 meia-altura**; largura total da ilha = 2*19 meias-larguras.
  Validada acertando tiles de canto na tela real.
- Viewport emulada (1600x723) só vale enquanto o processo Playwright está conectado.

## Fábrica de ração: dados do próprio jogo

`GET /api/factory/state?x=0&y=0` (capturado pelo gancho em `window.__fhApi`) devolve `recipes[]` com
`inputs[{item_id, have, need}]`, `craftable`, `craft_seconds`, `unlocked`; `queue[]` (com `is_ready`), `queue_slots`,
`max_per_craft` (5), `craft_energy` (3). Receitas vistas: Cow Feed = 2 `harvest_wheat` + 2 `leaves`; Chicken Feed =
2 `harvest_corn` + 2 `leaves`; Pig Feed = 2 `harvest_cabbage` + 2 `harvest_carrot` + 3 `harvest_cassava` (600 s, nível 7).
`harvest_X` -> semente `X` na Farm Store; `leaves` vêm das árvores. `/api/barn/status` e `/api/animals/state` também são
capturados. Interface da fábrica: lista de receitas em y = 234 + 80*índice, "+" da quantidade em (883,386).

## Mina: estado do jogo

`GET /api/mining/state` (consultado pelo jogo a cada ~5 s; capturado em `window.__fhApi`) traz `drop_table`,
`barn_used/barn_capacity` e as corridas com tempo restante; o parser `_mine_remaining` lê qualquer campo
`seconds_left`/`ready_at`/`is_ready` e bateu com o timer real (corrida de 20 min). Reserva: `mine_ready_at`.
Fila da fábrica: cada trabalho tem `count`, `ready_count`, `seconds_left` do trabalho inteiro (unidades saem em sequência,
Pig Feed = 600 s por unidade; Collect All pega as unidades já prontas).

## Inventário

Clicar no celeiro (âncora + ~6 px abaixo) abre o Inventário e o jogo consulta `GET /api/player/inventory` ->
`{"items":[{"item_id","item_type","quantity"}]}` (capturado em `window.__fhApi`). Ids: `feed_cow`, `feed_chicken`,
`feed_pig`, `harvest_<cultura>` (+ peixes e itens de evento). **Itens com quantidade 0 não aparecem** (ausente = 0).
X do diálogo em (1070,149) no quadro 1568. Bolhas de produto: o centro fica em âncora + (0, -104 px) a meia-altura
11,4 (medido no ovo); porco ~ -86 px.

## Mecânicas confirmadas

- **Plantar:** comprar sementes (máx. 99 por saco; milho 25 ouro, cresce 25 min; cana 36, 40 min) → o saco vem
  no cursor → soltar na grama → clicar nele **uma vez** → clicar só em tiles de terra **vazios**.
  Clicar no saco de novo ou num tile já plantado solta o saco. Esc solta. Passar o mouse num tile plantado mostra "WATER ME".
- **Colher:** Hand, um tile por clique, +1 item +8 XP, sem energia. Clique no centro deslocado p/ esquerda-baixo.
- **Regar:** Water + 3x3 (18 energia/bloco, cobra só o seco). Blocos alinhados ao canto do canteiro (centros 11,14,17).
- **Árvores:** clicar na raiz (centro do tile deslocado) — ~2 energia; árvore em crescimento mostra "READY IN".
  Cai tronco/fibra sozinho; às vezes um item laranja precisa de clique.
- **Energia:** barra até 170. Zerou → abre "Buy Energy". BEST VALUE = +150 por 20 token (testnet).
- **Fábrica:** Collect All; receitas vaca (trigo + folhas), galinha (milho + folhas), porco; 3 energia cada.
- **Mina:** botão único COLLECT / START RUN; corrida = 5 energia, 20 min. Abrir clicando no **tile central da
  pegada 3x3** (x=1,y=10). A **Unit Factory** (galpão vermelho, tiles y<=8, fora do estado salvo) fica colada ao norte:
  clicar perto da borda y=8/9 abre ela em vez da mina. O bot confere o botão verde/cinza do diálogo da mina antes de
  clicar COLLECT/START e tenta pontos alternativos (`MINE_OFFSETS`). Sequência: COLLECT → "You mined!" OK → START RUN.
  (Nas primeiras rodadas o bot errava a abertura e a mina ficou parada com 3 achados esperando.)
- **Câmera:** roda do mouse = zoom (lento, rolar 30+), arrastar = mover. Botões +/- da UI não mudam o zoom.
- O jogo vende, mas **o usuário proibiu vender** (Farmers' Market/Market Mae).

## Lições de processo

- Cliques às cegas pela imagem falharam: erro de 1 linha de tile, sacos desligando sozinhos, plantio duplicado.
  Sempre ler o estado antes (alvos exatos) e conferir depois.
- A ação `zoom` de captura pode devolver imagem antiga; confiar em screenshots completos.
- Estado é atrasado até ~20 s: esperar o próximo salvamento antes de decidir de novo.

## Pendências

- [ ] Rodar `calibrate.py` + `main.py --once --dry` na máquina do usuário e depois um ciclo supervisionado.
- [ ] Estado legível da mina e das colmeias (hoje só por intervalo).
- [ ] Receita da ração de porco; coleta dos itens laranjas das árvores.
- [ ] Culturas além de milho/cana: tempos de crescimento (milho 25, cana 40, abóbora 40, trigo 10).

## Último estado da fazenda (2026-11-03 ~14:05)

Nível 25 · ouro ~24,5 mil · token ~1.314 · canteiro 9x9 todo de milho (plantado 13:50, pronto ~14:15),
horta de cana replantada. Árvores cortadas; animais alimentados.

## Atualização (noite)
- CHECK_EVERY 15 s; FACTORY_MAX_PER_FOOD 3; FEED_MIN 5 + plantio por preço (SEED_PRICE); canas à frente das colmeias protegidas; sacos na linha das colmeias à esquerda; log simples (--verbose p/ detalhes).
- Erros fatais vistos: 2 instâncias do bot ao mesmo tempo (Frame detached) e reconexão chamando sync_playwright duas vezes (corrigido: pw.stop() + trava de instância única + _eval com retry).
