@echo off
setlocal
cd /d "%~dp0"
title Pulso - servidor de jogo
where python.exe >nul 2>nul
if errorlevel 1 goto trylauncher
python -B iniciar.py
goto finish
:trylauncher
py -3 -B iniciar.py
:finish
if errorlevel 1 pause
