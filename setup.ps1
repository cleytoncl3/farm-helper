# Instalador / configurador do Farm Helper (maquina nova ou primeira vez).
#   powershell -ExecutionPolicy Bypass -File setup.ps1                  (interativo)
#   ... -Browser brave|chrome|edge  -Yes                               (sem perguntas)
#
# Faz: 1) confere/instala Python 3.12  2) instala as bibliotecas  3) detecta os navegadores e voce escolhe um
#      4) configura o modo de depuracao (porta 9222) COM as extensoes  5) abre e testa  6) cria atalho na area de trabalho
param([ValidateSet("", "brave", "chrome", "edge")][string]$Browser = "", [switch]$Yes, [switch]$NoLaunch)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$port = 9222

function Say($t, $c = "White") { Write-Host $t -ForegroundColor $c }
function Step($n, $t) { Write-Host ""; Write-Host "[$n] $t" -ForegroundColor Cyan }
function Ask($q, $default = "S") {
    if ($Yes) { return $true }
    $r = Read-Host "$q [S/n]"
    return ($r -eq "" -or $r -match "^(s|y)")
}
function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }

Say "=====================================================" Cyan
Say "   Farm Helper - instalacao e configuracao" Cyan
Say "=====================================================" Cyan

# ------------------------------------------------------------------ 1) Python
Step 1 "Procurando o Python (3.10 ou mais novo)"
function Find-Python {
    $cands = @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
               "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
               "C:\Program Files\Python312\python.exe", "C:\Program Files\Python313\python.exe", "C:\Program Files\Python311\python.exe")
    foreach ($c in $cands) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") {      # ignora o atalho falso da Microsoft Store
        $v = & $cmd.Source -c "import sys;print(sys.version_info>=(3,10))" 2>$null
        if ($v -eq "True") { return $cmd.Source }
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Say "Python nao encontrado. Vou instalar o Python 3.12 automaticamente (so para o seu usuario, sem precisar de administrador)." Yellow
    # 1a tentativa: winget (vem no Windows 10/11 atualizado)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Tentando pelo winget..."
        winget install --id Python.Python.3.12 -e --silent --scope user --accept-package-agreements --accept-source-agreements
        Refresh-Path
        $py = Find-Python
    }
    # 2a tentativa: baixa o instalador oficial do python.org e instala em silencio
    if (-not $py) {
        Say "Baixando o instalador oficial do python.org..."
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $arch = if ([Environment]::Is64BitOperatingSystem) { "amd64" } else { "" }
            $name = if ($arch) { "python-3.12.8-amd64.exe" } else { "python-3.12.8.exe" }
            $inst = Join-Path $env:TEMP $name
            Invoke-WebRequest "https://www.python.org/ftp/python/3.12.8/$name" -OutFile $inst -UseBasicParsing
            $sig = Get-AuthenticodeSignature $inst
            if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "Python Software Foundation") { throw "assinatura do instalador invalida; abortei por seguranca" }
            Say "Instalando o Python (1-2 minutos)..."
            Start-Process $inst -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0", "Include_launcher=0", "Include_pip=1" -Wait
            Refresh-Path
            $py = Find-Python
        } catch { Say "Falhou o download/instalacao automatica: $_" Red }
    }
    if (-not $py) {
        Say "ERRO: nao consegui instalar o Python sozinho (sem internet, proxy ou antivirus bloqueando). Instale manualmente de https://www.python.org/downloads/ (marque 'Add python.exe to PATH') e rode este instalador de novo." Red
        Read-Host "Enter para sair"; exit 1
    }
}
& $py -m ensurepip --upgrade 2>&1 | Out-Null
[IO.File]::WriteAllText((Join-Path $here "python.txt"), $py, (New-Object Text.UTF8Encoding($false)))
Say "OK: $py" Green

# ------------------------------------------------------------------ 2) Bibliotecas
Step 2 "Instalando as bibliotecas (playwright, pillow, numpy)"
& $py -m pip install --upgrade pip --quiet 2>&1 | Out-Null
& $py -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { Say "ERRO ao instalar as bibliotecas (sem internet?). Rode de novo." Red; Read-Host "Enter para sair"; exit 1 }
& $py -c "import playwright, PIL, numpy; print('imports ok')"
if ($LASTEXITCODE -ne 0) { Say "ERRO: as bibliotecas nao carregaram." Red; Read-Host "Enter para sair"; exit 1 }
Say "OK: bibliotecas instaladas (o bot usa o SEU navegador; nao baixa outro)." Green

# ------------------------------------------------------------------ 3) Navegador
Step 3 "Escolhendo o navegador"
$defs = @(
    @{ id = "brave";  nome = "Brave";         exe = @("C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe", "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe");  ud = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data";  clone = "C:\fh-brave-clone";  mode = "default" },
    @{ id = "chrome"; nome = "Google Chrome"; exe = @("C:\Program Files\Google\Chrome\Application\chrome.exe", "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"); ud = "$env:LOCALAPPDATA\Google\Chrome\User Data"; clone = "C:\fh-chrome-profile"; mode = "clone" },
    @{ id = "edge";   nome = "Microsoft Edge"; exe = @("C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "C:\Program Files\Microsoft\Edge\Application\msedge.exe"); ud = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"; clone = "C:\fh-edge-profile"; mode = "clone" }
)
$found = @()
foreach ($d in $defs) {
    $e = $d.exe | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($e) { $d.path = $e; $found += $d }
}
if ($found.Count -eq 0) { Say "ERRO: nao achei Brave, Chrome nem Edge instalados. Instale um deles e rode de novo." Red; Read-Host "Enter para sair"; exit 1 }
$sel = $null
if ($Browser) { $sel = $found | Where-Object { $_.id -eq $Browser } | Select-Object -First 1 }
if (-not $sel) {
    if ($found.Count -eq 1 -or $Yes) { $sel = $found[0] }
    else {
        Say "Navegadores encontrados:"
        for ($i = 0; $i -lt $found.Count; $i++) { Say ("  [{0}] {1}" -f ($i + 1), $found[$i].nome) }
        do { $n = Read-Host "Qual voce usa para jogar (numero)"; $ok = ($n -match "^\d+$" -and [int]$n -ge 1 -and [int]$n -le $found.Count) } until ($ok)
        $sel = $found[[int]$n - 1]
    }
}
Say "Escolhido: $($sel.nome)  ($($sel.path))" Green

# ------------------------------------------------------------------ 4) Modo de depuracao + extensoes
Step 4 "Configurando o modo de depuracao (porta $port) mantendo as extensoes"
if ($sel.id -eq "brave") {
    Say "Brave: o bot reabre o seu Brave com a porta de depuracao no PERFIL NORMAL (extensoes, carteira e login intactos)."
    Say "Se o seu Brave nao aceitar, o bot muda sozinho para uma copia do perfil (modo clone)."
} else {
    Say "$($sel.nome) recente IGNORA a porta de depuracao no perfil normal (protecao de seguranca)."
    Say "Por isso o bot usa uma COPIA do seu perfil em $($sel.clone): extensoes, carteira e login sao copiados; o original nao muda."
    Say "Na primeira vez a carteira pode pedir login de novo nessa janela: faca uma vez e fica salvo."
}
$cfg = [ordered]@{ browser = $sel.id; name = $sel.nome; exe = $sel.path; mode = $sel.mode; user_data = $sel.ud; clone = $sel.clone; port = $port }
[IO.File]::WriteAllText((Join-Path $here "browser.json"), ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
Say "Configuracao salva em browser.json" Green

# ------------------------------------------------------------------ 5) Abrir e testar
function Test-Port { try { Invoke-WebRequest "http://127.0.0.1:$port/json/version" -UseBasicParsing -TimeoutSec 4 | Out-Null; return $true } catch { return $false } }
function Count-Extensions($profileRoot) {
    $dir = Join-Path $profileRoot "Default\Extensions"
    if (-not (Test-Path $dir)) { return @() }
    $names = @()
    foreach ($ext in Get-ChildItem $dir -Directory -ErrorAction SilentlyContinue) {
        $ver = Get-ChildItem $ext.FullName -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
        $mf = if ($ver) { Join-Path $ver.FullName "manifest.json" } else { $null }
        $n = $ext.Name
        if ($mf -and (Test-Path $mf)) { try { $n = (Get-Content $mf -Raw -Encoding UTF8 | ConvertFrom-Json).name } catch {} }
        if ($n -notmatch "^__MSG_") { $names += $n }
    }
    return $names
}

if (-not $NoLaunch) {
    # Endereco do jogo: o oficial (um game_url.txt opcional pode trocar).
$urlFile = Join-Path $here "game_url.txt"
$gameUrl = if (Test-Path $urlFile) { (Get-Content $urlFile -Raw).Trim() } else { "" }
if (-not $gameUrl) { $gameUrl = "https://play.rrfvillage.xyz/" }
Step 5 "Abrindo o $($sel.nome) em modo de depuracao e testando"
    if (Ask "Posso abrir/reabrir o $($sel.nome) agora? (as abas voltam; salve o que estiver fazendo)") {
        $running = Get-Process ($(if ($sel.id -eq "brave") { "brave" } elseif ($sel.id -eq "chrome") { "chrome" } else { "msedge" })) -ErrorAction SilentlyContinue
        $args = @("-ExecutionPolicy", "Bypass", "-File", "start_browser.ps1", "-Browser", $sel.id)
        if ($running -and -not (Test-Port)) { $args += "-Restart" }
        & powershell @args
        $code = $LASTEXITCODE
        if ($code -ne 0 -and $sel.id -eq "brave") {
            Say "A porta nao abriu no perfil normal; tentando o modo clone..." Yellow
            $cfg.mode = "clone"
            [IO.File]::WriteAllText((Join-Path $here "browser.json"), ($cfg | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
            & powershell -ExecutionPolicy Bypass -File start_browser.ps1 -Browser brave -Mode clone -Restart
            $code = $LASTEXITCODE
        }
        if (Test-Port) {
            $v = (Invoke-WebRequest "http://127.0.0.1:$port/json/version" -UseBasicParsing).Content | ConvertFrom-Json
            Say "OK: depuracao ativa na porta $port ($($v.Browser))" Green
            $root = if ($cfg.mode -eq "clone") { $sel.clone } else { $sel.ud }
            $exts = Count-Extensions $root
            if ($exts.Count -gt 0) { Say ("Extensoes encontradas no perfil usado ({0}): {1}" -f $exts.Count, (($exts | Select-Object -Unique -First 12) -join ", ")) Green }
            else { Say "AVISO: nao vi extensoes no perfil usado. Instale a carteira (a que o jogo usa) NESTA janela do navegador e faca o login no jogo." Yellow }
            Say "IMPORTANTE: nesta janela, entre no jogo ($gameUrl) e conecte a carteira uma vez. Depois e so rodar o bot." Yellow
        } else {
            Say "ERRO: a porta $port nao abriu. Feche TODAS as janelas do navegador e rode o instalador de novo." Red
        }
    }
}

# ------------------------------------------------------------------ 5b) Calibracao + construcoes
Step "5b" "Calibracao pela SUA tela e escolha das construcoes"
Say "O assistente le o jogo no seu navegador (tamanho da janela, tela, zoom, tamanho da land e onde ficam as construcoes) e salva UMA vez."
Say "Depois voce marca quais construcoes tem (Vaca, Galinheiro, Porcos, Mina, Moinho, Laticinio, Padaria...); o que desmarcar o bot pula."
if (Test-Port) {
    if (Ask "Quer calibrar o bot com base na sua tela de jogo agora? (o jogo precisa estar logado neste navegador)") {
        & $py calibrate_wizard.py
        if ($LASTEXITCODE -ne 0) { Say "A calibracao falhou. Voce pode refazer depois pela tela de configuracao (configurar.bat)." Yellow }
    } else { Say "Ok. Na primeira execucao o bot calibra sozinho e salva (so uma vez)." }
} else { Say "O navegador nao esta com a depuracao ativa agora; calibre depois pela tela de configuracao (configurar.bat)." Yellow }
# ------------------------------------------------------------------ 6) Atalho
Step 6 "Criando atalho na area de trabalho (app com janela, sem prompt)"
$appExe = Join-Path $here "Farm Helper.exe"
$pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
try {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Farm Helper.lnk"))
    if (Test-Path $appExe) { $lnk.TargetPath = $appExe }
    elseif (Test-Path $pyw) { $lnk.TargetPath = $pyw; $lnk.Arguments = '"' + (Join-Path $here "fh_app.py") + '"' }
    else { $lnk.TargetPath = (Join-Path $here "iniciar_bot.bat") }
    $lnk.WorkingDirectory = $here
    $ico = Join-Path $here "fh_icon.ico"
    if (Test-Path $ico) { $lnk.IconLocation = $ico } else { $lnk.IconLocation = $sel.path }
    $lnk.Save()
    Say "OK: atalho 'Farm Helper' criado na area de trabalho (abre o app: log, construcoes e calibracao)." Green
} catch { Say "Nao consegui criar o atalho (sem problema): $_" Yellow }
if (-not $Yes -and (Test-Path $pyw)) { if (Ask "Abrir o app Farm Helper agora?") { Start-Process $pyw -ArgumentList ('"' + (Join-Path $here "fh_app.py") + '"') -WorkingDirectory $here } }

Say ""
Say "=====================================================" Cyan
Say " PRONTO. Para jogar: abra o atalho 'Farm Helper' (app com janela) e clique em Iniciar." Cyan
Say " Sem janela? iniciar_bot.bat roda o bot no prompt. Gerar o .exe: build_exe.bat" Cyan
Say " Teste primeiro:  iniciar_bot.bat --once --dry   (so mostra os cliques)" Cyan
Say " Trocar de navegador: rode instalar.bat de novo." Cyan
Say "=====================================================" Cyan
if (-not $Yes) { Read-Host "Enter para fechar" }
