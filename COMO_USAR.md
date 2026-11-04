# Como usar o Farm Helper (sem supervisão)

## 1. Instalar (uma vez)

**Máquina nova:** dê dois cliques em `instalar.bat`. Ele confere/instala o Python, instala as bibliotecas, detecta Brave/Chrome/Edge, deixa você ESCOLHER o navegador, configura a porta de depuração (9222) mantendo as extensões (Brave: perfil normal; Chrome/Edge: cópia do perfil com extensões e carteira), abre o navegador, testa a porta, lista as extensões encontradas e cria o atalho **Farm Helper** na área de trabalho. A escolha fica em `browser.json` (rode `instalar.bat` de novo para trocar de navegador). Na janela do navegador, entre no jogo e conecte a carteira uma vez.

(As instruções abaixo são o passo a passo manual.)

1. Dê dois cliques em **`instalar.bat`**. Ele instala o Python 3.12 (se faltar), as bibliotecas e o navegador de teste.
   Se aparecer "Python não encontrado", feche a janela, abra outra e rode `instalar.bat` de novo.

## 2. Rodar

1. **Feche todas as janelas do Brave** (inclusive o ícone perto do relógio).
2. Dê dois cliques em **`iniciar_bot.bat`**. Ele abre o seu Brave (com suas extensões e o seu login) com a porta de
   depuração e roda o bot. **Os logs e erros aparecem nessa janela preta** e também em `bot.log`.
3. Deixe a janela do Brave aberta (pode minimizar, **não feche**). O bot clica sozinho em PLAY, ajusta a câmera,
   calibra pela tela e começa. Se você arrastar a câmera, ele percebe e recalibra sozinho.
4. **Parar:** Ctrl+C na janela preta ou crie um arquivo chamado `STOP` nesta pasta.

Primeira vez? Teste em ordem: `iniciar_bot.bat --once --dry` (só mostra os cliques) → `iniciar_bot.bat --once`
(um ciclo real) → `iniciar_bot.bat` (laço contínuo).

Se o Brave bloquear a porta de depuração no perfil padrão: `powershell -ExecutionPolicy Bypass -File start_brave.ps1 -Mode clone`
(copia o seu perfil para `C:\fh-brave-clone`) e depois `python main.py`.

## 3. O que o bot faz a cada ciclo (nesta ordem)

1. **Inventário e fábrica de ração:** primeiro abre o celeiro e **lê o inventário** (quantos sacos de ração de cada
   animal e quanto tem de cada colheita). Depois abre a fábrica, coleta o pronto e lê as receitas. **Fabrica primeiro a
   ração com MENOS sacos** (e em maior quantidade), depois as outras. Registra o que **falta** (ex.: `Pig Feed: faltam
   harvest_cassava 2/3 -> vou plantar: cassava`). Folhas vêm das árvores.
2. **Animais:** coleta leite/ovo/carne e alimenta.
3. **Mina:** coleta os minérios e inicia nova corrida (confere na tela que abriu a mina e não a Unit Factory).
4. **Mel:** clica nas colmeias.
5. **Árvores:** corta as prontas, de baixo para cima (tenta pontos de clique alternativos se falhar).
6. **Plantas:** colhe, **replanta e rega 1 tile por vez** (nunca 3x3), de baixo para cima, clicando no ponto do tile
   validado nas árvores/colheita (centro deslocado para a esquerda/baixo).
   - **Plano em 4 quadrantes:** o canteiro 9x9 é dividido em 4 partes, cada uma com uma cultura:
     1. Se algum animal tem **menos de 15 sacos** de ração (`FEED_TARGET` em `config.py`), planta os **ingredientes
        escassos** da ração com MENOS sacos (ex.: mandioca para a ração do porco).
     2. Se **todos** têm 5 ou mais, planta as culturas **mais caras do mercado** (SEED_PRICE).
     3. Os quadrantes que sobram também recebem as colheitas de menor quantidade.
     Um quadrante só troca de cultura quando for replantado. Veja a linha `[PLANO]` no log (mostra o motivo).
   - **Porco/carne:** o bot só alimenta um animal se houver ração dele no inventário (sem `feed_pig` ele só tenta
     coletar a carne e avisa). A ração do porco exige 2 repolho + 2 cenoura + 3 mandioca.
   - Antes de cada semente o bot confere pela tela se o saco está no cursor; se soltou, prende de novo.
   - Quando a energia acaba compra o pacote BEST VALUE (+150 por 20 token de testnet). Use `--no-energy` para desligar.

**Interrupção por timers:** durante tarefas longas (plantar, regar, colher, cortar árvores) o bot confere a cada ~6 s
se algum timer venceu: produto pronto na **vaca, galinheiro ou porco**, animal com fome **e há ração dele**, ou corrida da
**mina** terminada (lida do estado do jogo; reserva: timer de 20 min). Se sim, ele **pausa**, atende (`[INTERRUPÇÃO]` no
log), reseleciona a ferramenta, reprende o saco de sementes e **volta ao que estava fazendo**.

**Fábrica e porco:** quando uma colheita de ingrediente de ração é feita, o bot reconsulta o inventário e a fábrica
na hora (não espera 5 min), e volta à fábrica quando a fila terminar para coletar. A ração do porco leva ~10 min por
unidade; o porco só é alimentado quando `feed_pig` > 0 no inventário.

O bot **nunca vende** nada e não usa o Farmers' Market.

## 4. Lendo os logs (janela preta e `bot.log`)

| Prefixo | Significado |
|---|---|
| `INFO [FÁBRICA]/[ANIMAL]/[MINA]/[MEL]/[ÁRVORES]/[COLHEITA]/[PLANTIO]/[REGA]/[PLANO]` | o que está fazendo |
| `INFO [BOT] energia ~N \| plantas ...` | resumo a cada verificação (15 s) |
| `INFO [CALIB]` | calibração/enquadramento da câmera |
| `ERROR [ERRO] tarefa: motivo` + traceback | uma tarefa falhou; o bot se recupera (fecha diálogos) e tenta de novo |
| `ERROR [ATENÇÃO] ... falhou N vezes seguidas` | algo persistente: olhe a tela do jogo / câmera |
| `INFO [INTERRUPÇÃO] timers vencidos: ...` | pausou a tarefa para atender casas/mina e depois retoma |
| `WARNING [ANIMAL] ... com fome mas não há ração` | falta `feed_*` no inventário; a fábrica precisa produzir |
| `WARNING [PLANTIO] não consegui pegar o saco existente` | o bot compra sementes novas (não trava mais) |
| `WARNING [SESSÃO]` | o jogo parou de salvar; o bot recarregou a página sozinho |
| `ERROR [FATAL]` | navegador caiu/desconectou; tenta reconectar 5× (15 s) e então encerra com instruções |

## 5. Problemas comuns

| Sintoma | O que fazer |
|---|---|
| `ERRO: o Brave está aberto sem a porta de depuração` | Feche TODAS as janelas do Brave e rode `iniciar_bot.bat` de novo |
| `a porta 9222 NÃO abriu` | Use o modo clone (acima) |
| `não achei a borda da ilha na tela` | Deixe a fazenda inteira visível (sem diálogos abertos) e rode de novo |
| Cliques deslocados após mexer na janela | O bot recalibra sozinho; se persistir, feche o jogo e rode de novo |
| `[SACO] fora do cursor` repetido e `não consegui prender o saco` | Ajuste `SACK_OFFSETS` em `config.py` (pontos de clique do saco) |
| `[MINA] diálogo errado/fechado` repetido | Ajuste `MINE_OFFSETS` em `config.py` |
| Faltou semente/ouro | O bot só compra com ouro do jogo; confira o ouro no topo da tela |

## 6. Onde mudar o comportamento (`config.py`)

- `FACTORY_BATCH`: unidades por fabricação (máx. 5). `STOCK_RATIO_TARGET`/`DEFAULT_PLAN`: plano de plantio.
- `INTERVAL_FACTORY/MINE/HONEY`: de quanto em quanto tempo tentar cada tarefa.
- `TILE_DX/TILE_DY`: ponto de clique dentro do tile. `ENERGY_*`: custos usados para decidir quando comprar energia.

Detalhes técnicos: `README.md` e `docs/HANDOFF.md`.


## 7. Usar o Google Chrome em vez do Brave

Rode **`iniciar_bot.bat chrome`**. O Chrome 136+ ignora a porta de depuração no perfil normal, então o bot abre o Chrome com uma **cópia do seu perfil** em `C:\fh-chrome-profile` (extensões, carteira e login copiados; o perfil original não muda). A cópia é feita só na 1ª vez; para recopiar (trocou de carteira/extensão) use `powershell -ExecutionPolicy Bypass -File start_browser.ps1 -Browser chrome -Restart -Resync`. Se a carteira pedir login de novo na cópia, faça-o uma vez nessa janela.



## 8. Tela de configuração e calibração (novo)

- **configurar.bat** abre uma tela moderna no navegador: botão **Calibrar** (lê a SUA tela/land e salva em `calibracao.json` uma vez; depois só é reaproveitado) e a lista de construções/rotinas (Fábrica de ração, Vaca, Galinheiro, Porcos, Mina, Colmeias, Moinho, Laticínio, Padaria, Árvores, Plantação). **Desmarcou, o bot pula.** Salvo em `config_usuario.json`. `--recalibrate` refaz a calibração.
- O `instalar.bat` pergunta se você quer calibrar e abre essa tela.
- **rodar_sempre.bat** reinicia o bot sozinho se ele cair (pare criando o arquivo STOP).
- Casas de craft: **Moinho** (5 colheitas -> farinha, só com excedente), **Laticínio** (leite -> queijo/manteiga/creme) e **Padaria** (pães e bolos com os ingredientes que houver). **Entregas** é venda (ouro/XP): só listada, o bot não automatiza.



## 9. App com janela (sem prompt de comando)

Abra o atalho **Farm Helper** (ou `configurar.bat`). E uma janelinha com:
- **Log** correndo em tempo real, botao **Iniciar/Parar**, modo **claro/escuro** (☀/🌙), reinicio automatico se o bot cair.
- **Construcoes**: chaves para ligar/desligar cada rotina (o que estiver desligado o bot pula).
- **Calibracao**: botao "Calibrar agora" com barra de progresso e cada etapa escrita; a calibracao fica salva.

Para gerar o `Farm Helper.exe` (nao vai no GitHub): rode `build_exe.bat`. Sem o .exe, o atalho usa `pythonw fh_app.py`.
O app so abre/para o bot (`main.py`); fechar o app pergunta e para o bot.


## 10. Missoes (Task Board) e Pedidos de entrega

Ligue "Task Board (missoes)" e "Entregas (pedidos)" na aba Construcoes (a calibracao ja marca sozinha o que existe na sua land).
O bot NAO reabre as telas toda hora: o Task Board so muda 1x por dia (le no inicio e no reinicio diario) e cada pedido de entrega so muda quando voce envia um (a vaga ganha um novo 15 min depois). Ele guarda o que leu e so abre quando chega a hora, ou quando o inventario ja cobre uma missao/pedido. Ao abrir:
1. abre o Task Board, le as missoes em aberto e entrega as que o inventario ja cobre;
2. abre as Entregas, le os 3 pedidos; se TODAS as caixas de um pedido ja tem item, clica no + (vira check verde) de cada caixa e depois em SHIP;
3. o que falta e o bot sabe produzir vira prioridade: moinho (farinhas), laticinio (queijo/manteiga/creme), padaria (receitas)
   e plantio (colheitas). Ingredientes que um pedido espera nao sao gastos em outras receitas.
Pedidos/missoes com item que o bot nao produz (peixes, diamante, reliquia...) sao deixados de lado e nada e gasto com eles.
Atencao: entregar da ouro/XP (e uma venda por encomenda); desligue as duas chaves se nao quiser isso.

## 11. Como a camera e calibrada (qualquer tamanho de land)

1. Zoom e centro: o bot ajusta o zoom para o tamanho da land e arrasta a camera ate o TILE CENTRAL ficar no meio da area util
   da tela (nao depende do ceu; o arrasto comeca num tile de grama livre).
2. Leitura dos tiles: o jogo desenha 4 tracinhos brancos no tile sob o mouse (e nada fora da land). O bot passa o mouse em tiles
   livres perto da borda da frente e confere que o tile de dentro existe e o vizinho de fora nao: assim LE quantos tiles a land
   tem por lado (ex.: 20x20), mesmo que o jogo informe outro numero (expansao em andamento).
3. Grade exata: mede os centros reais de tiles distantes e ajusta a grade (tamanho do tile e posicao), corrigindo a leitura pela
   borda da ilha (cerca/penhasco). O resultado fica salvo em calibracao.json.
A imagem calibracao_preview.png mostra o contorno da land reconhecida (branco), o tile central (magenta), as construcoes
(circulos vermelhos) e a terra de plantio (pontos azuis). Depois de uma expansao o bot percebe o novo tamanho e recalibra.