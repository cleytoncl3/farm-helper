# Cria na Area de Trabalho o atalho "Brave (bot)": o Brave NORMAL (mesmo perfil, carteira e login) ja com a porta
# de depuracao. Se voce sempre abrir o Brave por esse atalho, o iniciar_bot.bat nao precisa fechar nada.
$brave = "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
if (-not (Test-Path $brave)) { $brave = "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\Application\brave.exe" }
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Brave (bot).lnk"
$w = New-Object -ComObject WScript.Shell
$s = $w.CreateShortcut($lnk)
$s.TargetPath = $brave
$s.Arguments = "--remote-debugging-port=9222"
$s.IconLocation = "$brave,0"
$s.Save()
Write-Host "Atalho criado: $lnk"