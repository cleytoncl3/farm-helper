@echo off
rem Mantem o bot rodando: se ele encerrar por erro, reinicia sozinho. Para parar de vez: crie um arquivo chamado STOP nesta pasta.
cd /d "%~dp0"
chcp 65001 >nul
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist python.txt set /p PY=<python.txt
if not exist "%PY%" set "PY=python"
:loop
if exist STOP ( echo STOP encontrado: encerrando. & exit /b 0 )
"%PY%" main.py %*
if exist STOP ( echo STOP encontrado: encerrando. & exit /b 0 )
echo [%date% %time%] bot encerrou; reiniciando em 30 s...
timeout /t 30 /nobreak >nul
goto loop
