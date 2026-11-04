@echo off
rem Gera "Farm Helper.exe" (janela sem prompt) nesta pasta. Precisa do Python do bot. O .exe so abre o app; o bot continua sendo o main.py.
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist python.txt set /p PY=<python.txt
if not exist "%PY%" set "PY=python"
if exist "Farm Helper.exe" del "Farm Helper.exe"
"%PY%" -m pip install --quiet pyinstaller
if not exist fh_icon.ico "%PY%" make_icon.py
"%PY%" -m PyInstaller --noconfirm --onefile --noconsole --name "Farm Helper" --icon "%~dp0fh_icon.ico" --distpath . --workpath build_tmp --specpath build_tmp fh_app.py
if exist build_tmp rmdir /s /q build_tmp
if exist "Farm Helper.exe" ( echo. & echo Pronto: "Farm Helper.exe" ) else ( echo ERRO ao gerar o exe & pause & exit /b 1 )

