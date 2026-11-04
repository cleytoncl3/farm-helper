# Abre o Brave COM suas extensoes/carteira/login e com a porta de depuracao 9222 (para o bot conectar).
#
# Uso (normalmente chamado por iniciar_bot.bat):
#   powershell -ExecutionPolicy Bypass -File start_brave.ps1             # tenta o perfil padrao
#   powershell -ExecutionPolicy Bypass -File start_brave.ps1 -Restart    # fecha o Brave aberto e reabre com a porta
#   powershell -ExecutionPolicy Bypass -File start_brave.ps1 -Mode clone # copia o perfil e abre a copia
#
# O modo de depuracao usa o MESMO perfil: carteira, extensoes e login continuam funcionando.
# - Se o Brave JA estiver com a porta aberta, nao faz nada (sucesso).
# - Se o Brave estiver aberto SEM a porta: sem -Restart avisa (exit 1); com -Restart fecha as janelas e reabre.
# - Chromium recente pode bloquear a depuracao no perfil padrao; se a porta nao abrir use -Mode clone
#   (copia o perfil para C:\fh-brave-clone; o original nao muda).
param([ValidateSet("default", "clone")][string]$Mode = "default", [int]$Port = 9222, [switch]$Restart)

$brave = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
if (-not (Test-Path $brave)) { $brave = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe" }
$userData = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data"
$clone = "C:\fh-brave-clone"
$urlFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "game_url.txt"
$url = if (Test-Path $urlFile) { (Get-Content $urlFile -Raw -Encoding UTF8).Trim() } else { "" }
if (-not $url) { $url = "https://play.rrfvillage.xyz/" }

function Test-Port { try { Invoke-WebRequest "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 6 | Out-Null; return $true } catch { return $false } }

if (Test-Port) { Write-Host "OK: o Brave ja esta com a depuracao ativa na porta $Port."; exit 0 }
if (-not (Test-Path $brave)) { Write-Host "ERRO: brave.exe nao encontrado." -ForegroundColor Red; exit 2 }

if (Get-Process brave -ErrorAction SilentlyContinue) {
    if (-not $Restart) {
        Write-Host "AVISO: o Brave esta aberto SEM a porta de depuracao." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Fechando o Brave para reabrir com a depuracao (suas abas voltam; carteira e login ficam como estao)..."
    Get-Process brave -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object { $_.CloseMainWindow() | Out-Null }
    for ($i = 0; $i -lt 12; $i++) { Start-Sleep 1; if (-not (Get-Process brave -ErrorAction SilentlyContinue)) { break } }
    Get-Process brave -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
}

if ($Mode -eq "clone") {
    Write-Host "Copiando o perfil para $clone (sem caches)..."
    robocopy $userData $clone /E /XD "Cache" "Code Cache" "GPUCache" "GraphiteDawnCache" "DawnCache" "ShaderCache" "Crashpad" /XF "Singleton*" /NFL /NDL /NJH /NJS | Out-Null
    $args = @("--remote-debugging-port=$Port", "--user-data-dir=$clone", $url)
} else {
    $args = @("--remote-debugging-port=$Port", "--restore-last-session", $url)
}

Start-Process $brave -ArgumentList $args
Write-Host "Aguardando o Brave abrir (restaurar as abas pode levar ate 90 s)..."
for ($i = 0; $i -lt 90; $i++) { Start-Sleep 1; if (Test-Port) { Write-Host "OK: depuracao ativa na porta $Port."; exit 0 } }
Write-Host "ERRO: a porta $Port NAO abriu. Tente: powershell -ExecutionPolicy Bypass -File start_brave.ps1 -Mode clone" -ForegroundColor Red
exit 1