# Farm Helper (Python + Playwright)

Roda a rotina da fazenda do jogo  **sem gastar tokens**, com o mesmo método usado
na sessão manual: lê o **estado real do jogo** e clica com posicionamento calibrado **automaticamente pela tela**.

Rotina por ciclo (ordem pedida): ração → animais → mina → mel → árvores → plantas
(colher, replantar a mesma cultura, regar 3x3). Intervalo aleatório de 1–2 s entre ações.
Compra energia (pacote BEST VALUE, token de **testnet**) quando acaba. **Nunca vende nada.**

> **Para rodar sem supervisão leia `COMO_USAR.md`** (instalar com `instalar.bat`, rodar com `iniciar_bot.bat`;
> os erros aparecem na janela do prompt e em `bot.log`).

## Como funciona

- **Estado:** o jogo (Godot/WASM num iframe) salva com `POST /api/player/state` a cada ~20 s. Um gancho em
  `fetch`/XHR dentro do iframe guarda cada corpo; `gamestate.py` reconstrói o estado (formato completo
  `farm_compact` + mudanças `farm_delta`). Daí saem tiles plantados/secos/prontos, árvores prontas,
  estado dos animais, energia, sacos de semente.
- **Calibração automática** (`autocal.py`, ~0,1 s para ajustar e ~0,03 s para validar): lê um screenshot,
  acha a borda frontal da ilha (gramado × penhasco), ajusta o "V" e obtém escala e posição → projeção
  tile→tela. A cada tarefa o bot revalida; se você **arrastou ou deu zoom**, ele percebe e recalibra sozinho.
  Ao iniciar, ele também enquadra a câmera (zoom pela roda do mouse e arrasta o céu).
- **Sacos de semente:** antes de cada semente confere pela tela o ícone do saco junto do cursor.
- **Ração inteligente:** ao abrir a fábrica, o bot lê a resposta `/api/factory/state` do próprio jogo (receitas,
  `have`/`need` de cada ingrediente, slots da fila) e fabrica o que dá. O que falta vira prioridade de plantio.
- **Plantio em 4 quadrantes:** o canteiro é dividido em 4 e cada parte recebe uma cultura que falta para as rações
  (menor estoque ÷ consumo primeiro). Plantar e regar são sempre **1 por 1** (sem 3x3).
- O bot **não usa sessão/credenciais**: só observa o corpo de salvamento e clica como uma pessoa.

## Instalação (Windows)

```bash
winget install -e --id Python.Python.3.12 --scope user
pip install -r requirements.txt
playwright install chromium
```

## Usando o seu Brave (com extensões e login)

Feche **todas** as janelas do Brave e rode:

```bash
powershell -ExecutionPolicy Bypass -File start_brave.ps1
```

Ele abre o seu perfil padrão com a porta de depuração 9222 (login e extensões ficam como estão). Se a porta não abrir,
use `-Mode clone` (copia o perfil para `C:\fh-brave-clone`).
Sem Brave aberto: `python main.py --cdp "" --exe "C:\caminho\brave.exe" --profile profile`.

## Uso

```bash
python main.py --once --dry   # só registra os cliques no log (testa leitura e calibração)
python main.py --once         # um ciclo real
python main.py                # laço automático
```

O bot recarrega o jogo, clica em PLAY sozinho, enquadra a câmera, calibra e roda. **Não precisa calibrar na mão.**
Pare com **Ctrl+C** ou criando um arquivo `STOP` na pasta. Log em `bot.log`.

Opções: `--no-energy` (não compra energia), `--skip trees,honey` (pula tarefas), `--keep-camera` (não mexe no
zoom/posição; só calibra pela tela), `--manual-calib` (usa `calibration.json` feito com `calibrate.py`).

## Regras de interação (aprendidas jogando)

- Clique em tiles no **centro deslocado para a esquerda/baixo** (`TILE_DX/TILE_DY`); sempre **de baixo para cima**.
- Saco de sementes: comprar → soltar na grama → clicar nele **uma vez** (preso ao cursor) → clicar só em tiles
  **vazios**. Clicar de novo no saco ou num tile plantado **solta** o saco; o bot confere antes de cada semente.
- Depois de comprar energia/fechar diálogos a ferramenta (Water/Hand) é reselecionada.
- Árvores e sacos têm pontos de clique alternativos (`TREE_OFFSETS`, `SACK_OFFSETS`) testados quando o estado
  mostra que o clique falhou.
- O estado chega com até ~20 s de atraso: o bot espera um salvamento **posterior** à ação antes de decidir de novo
  e usa um intervalo de segurança por alvo (`Game.cooling`).

## Arquivos

| Arquivo | Função |
|---|---|
| `config.py` | UI fixa (quadro 1568), offsets de clique, sementes, energia, intervalos |
| `gamestate.py` | gancho fetch/XHR + reconstrução do estado (compacto + delta) |
| `autocal.py` | calibração automática pela tela + enquadramento da câmera |
| `geometry.py` | projeção tile↔tela, componentes do canteiro, blocos de rega 3x3 |
| `game.py` | navegador (CDP/perfil próprio), clique humanizado, ferramentas, energia, saco |
| `tasks.py` | fábrica, animais, mina, mel, árvores, colher/plantar/regar |
| `calibrate.py` | calibração manual (3 cliques) — alternativa |
| `main.py` | laço principal |
| `start_brave.ps1` | abre o Brave com a porta de depuração |
| `docs/` | `HANDOFF.md` (aprendizados), `ROUTINE.md` (regras do usuário) |

## Limitações conhecidas

- A calibração depende da borda frontal da ilha estar visível (gramado sobre penhasco). Sobreposições grandes
  (diálogos abertos) impedem a leitura: o bot mantém a calibração anterior e tenta de novo na próxima tarefa.
- Mina e mel não têm estado legível: o bot tenta em intervalos fixos (`INTERVAL_MINE`, `INTERVAL_HONEY`).
- Itens laranjas que caem das árvores não são coletados (sem posição no estado).
- Posições de UI (fábrica, mina, loja, energia, PLAY) são relativas à viewport 1600x723 que o bot impõe.
- Receita da ração de porco não mapeada: o bot só tenta fabricar (botão desativa se faltar ingrediente).
- Se o jogo mudar o formato de salvamento de novo, o leitor em `gamestate.py` precisa ser ajustado.
