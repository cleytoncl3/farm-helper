@echo off
rem Abre o app Farm Helper (janela com log, construcoes e calibracao). Sem prompt de comando.
cd /d "%~dp0"
if exist "Farm Helper.exe" ( start "" "Farm Helper.exe" & exit /b 0 )
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist python.txt set /p PY=<python.txt
if not exist "%PY%" set "PY=python"
set "PYW=%PY:python.exe=pythonw.exe%"
if exist "%PYW%" ( start "" "%PYW%" fh_app.py ) else ( start "" "%PY%" fh_app.py )
