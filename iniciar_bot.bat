@echo off
rem Abre o navegador configurado (com porta de depuracao e extensoes) e roda o bot. Logs/erros nesta janela e em bot.log.
rem   iniciar_bot.bat                  -> usa o navegador escolhido no instalar.bat (browser.json)
rem   iniciar_bot.bat chrome           -> forca Chrome (ou brave / edge)
rem Argumentos extras vao para o bot, ex.:  iniciar_bot.bat --once   |   iniciar_bot.bat --no-energy
cd /d "%~dp0"
chcp 65001 >nul

set "BROWSER="
if /i "%~1"=="chrome" ( set "BROWSER=chrome" & shift )
if /i "%~1"=="brave" ( set "BROWSER=brave" & shift )
if /i "%~1"=="edge" ( set "BROWSER=edge" & shift )

if not exist browser.json (
  echo Primeira vez neste computador: rodando o instalador...
  call "%~dp0instalar.bat"
)

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist python.txt set /p PY=<python.txt
if not exist "%PY%" set "PY=python"
"%PY%" -c "import playwright, PIL, numpy" >nul 2>&1
if errorlevel 1 (
  echo Python ou bibliotecas nao encontrados: rodando o instalador...
  call "%~dp0instalar.bat"
  "%PY%" -c "import playwright, PIL, numpy" >nul 2>&1
  if errorlevel 1 ( echo ERRO: instalacao incompleta. & pause & exit /b 1 )
)

if defined BROWSER ( set "BARG=-Browser %BROWSER%" ) else ( set "BARG=" )
powershell -ExecutionPolicy Bypass -File start_browser.ps1 %BARG%
if errorlevel 2 goto erro
if errorlevel 1 goto aberto
goto rodar

:aberto
echo.
echo O navegador esta aberto sem a porta de depuracao. Ele sera fechado e reaberto (as abas voltam).
choice /c SN /m "Fechar e reabrir agora"
if errorlevel 2 goto erro
powershell -ExecutionPolicy Bypass -File start_browser.ps1 %BARG% -Restart
if errorlevel 1 goto erro
goto rodar

:erro
echo.
echo O navegador nao ficou pronto. Leia a mensagem acima ou rode instalar.bat de novo.
pause
exit /b 1

:rodar
if exist STOP del STOP
echo.
echo === Bot iniciado. Para parar: Ctrl+C ou crie um arquivo chamado STOP nesta pasta. ===
"%PY%" main.py %1 %2 %3 %4 %5 %6 %7 %8 %9
echo.
echo === Bot encerrado (codigo %errorlevel%). Veja os ultimos erros acima ou em bot.log ===
pause

