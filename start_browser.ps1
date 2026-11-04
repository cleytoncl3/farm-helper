# Abre o navegador escolhido COM extensoes/carteira/login e a porta de depuracao 9222 (para o bot conectar).
# Le a configuracao feita por setup.ps1 (browser.json). Parametros sobrescrevem o arquivo.
#
#   powershell -ExecutionPolicy Bypass -File start_browser.ps1                    (usa browser.json)
#   ... -Browser brave|chrome|edge  -Mode default|clone  -Restart  -Resync
#
# default = perfil normal (so Brave aceita a porta nele). clone = COPIA do perfil em C:\fh-*-profile (extensoes, carteira
# e login copiados; o original nao muda). A copia e feita so na 1a vez (ou com -Resync).
param([string]$Browser = "", [string]$Mode = "", [int]$Port = 9222, [switch]$Restart, [switch]$Resync)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg = $null
$cfgFile = Join-Path $here "browser.json"
if (Test-Path $cfgFile) { try { $cfg = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch {} }
if (-not $Browser) { $Browser = if ($cfg) { $cfg.browser } else { "brave" } }
$urlFile = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "game_url.txt"
$url = if (Test-Path $urlFile) { (Get-Content $urlFile -Raw -Encoding UTF8).Trim() } else { "" }
if (-not $url) { $url = "https://play.rrfvillage.xyz/" }

$defs = @{
    brave  = @{ exe = @("C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe", "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe"); ud = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data"; clone = "C:\fh-brave-clone"; proc = "brave"; mode = "default" }
    chrome = @{ exe = @("C:\Program Files\Google\Chrome\Application\chrome.exe", "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"); ud = "$env:LOCALAPPDATA\Google\Chrome\User Data"; clone = "C:\fh-chrome-profile"; proc = "chrome"; mode = "clone" }
    edge   = @{ exe = @("C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "C:\Program Files\Microsoft\Edge\Application\msedge.exe"); ud = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"; clone = "C:\fh-edge-profile"; proc = "msedge"; mode = "clone" }
}
if (-not $defs.ContainsKey($Browser)) { Write-Host "ERRO: navegador '$Browser' desconhecido (brave, chrome ou edge)." -ForegroundColor Red; exit 2 }
$d = $defs[$Browser]
$exe = $d.exe | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Mode) { $Mode = if ($cfg -and $cfg.browser -eq $Browser -and $cfg.mode) { $cfg.mode } else { $d.mode } }
if ($Browser -ne "brave") { $Mode = "clone" }          # Chrome/Edge recentes ignoram a porta no perfil normal
$proc = $d.proc

function Test-Port { try { Invoke-WebRequest "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 6 | Out-Null; return $true } catch { return $false } }

if (Test-Port) { Write-Host "OK: o navegador ja esta com a depuracao ativa na porta $Port."; exit 0 }
if (-not $exe) { Write-Host "ERRO: $Browser nao encontrado neste computador. Rode instalar.bat." -ForegroundColor Red; exit 2 }

if (Get-Process $proc -ErrorAction SilentlyContinue) {
    if (-not $Restart) { Write-Host "AVISO: o $Browser esta aberto SEM a porta de depuracao." -ForegroundColor Yellow; exit 1 }
    Write-Host "Fechando o $Browser para reabrir com a depuracao (as abas voltam)..."
    Get-Process $proc -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object { $_.CloseMainWindow() | Out-Null }
    for ($i = 0; $i -lt 12; $i++) { Start-Sleep 1; if (-not (Get-Process $proc -ErrorAction SilentlyContinue)) { break } }
    Get-Process $proc -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
}

if ($Mode -eq "clone") {
    $clone = $d.clone
    if ($Resync -or -not (Test-Path "$clone\Default")) {
        Write-Host "Copiando seu perfil (extensoes, carteira, login) para $clone ... (so na 1a vez)"
        robocopy $d.ud $clone /E /XD "Cache" "Code Cache" "GPUCache" "GraphiteDawnCache" "DawnCache" "ShaderCache" "Crashpad" "CacheStorage" /XF "Singleton*" "lockfile" /NFL /NDL /NJH /NJS | Out-Null
    }
    $argsList = @("--remote-debugging-port=$Port", "--user-data-dir=$clone", "--no-first-run", "--restore-last-session", $url)
} else {
    $argsList = @("--remote-debugging-port=$Port", "--restore-last-session", $url)
}

Start-Process $exe -ArgumentList $argsList
Write-Host "Aguardando o $Browser abrir (pode levar ate 90 s)..."
for ($i = 0; $i -lt 90; $i++) { Start-Sleep 1; if (Test-Port) { Write-Host "OK: depuracao ativa na porta $Port."; exit 0 } }
Write-Host "ERRO: a porta $Port NAO abriu." -ForegroundColor Red
exit 1
