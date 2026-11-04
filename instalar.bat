@echo off
rem Instalador/configurador: confere Python, instala bibliotecas, escolhe o navegador e configura a depuracao.
cd /d "%~dp0"
chcp 65001 >nul
powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
