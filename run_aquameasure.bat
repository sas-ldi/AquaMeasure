@echo off
REM AquaMeasure + detection IA (ultralytics/torch GPU dans fish-vision\.venv)
cd /d "%~dp0"
set PY=%~dp0fish-vision\.venv\Scripts\python.exe
if not exist "%PY%" (
    echo fish-vision\.venv introuvable — lancez fish-vision\scripts\setup_env.bat
    pause
    exit /b 1
)
"%PY%" aquameasure.py %*
