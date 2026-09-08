@echo off
cd /d "%~dp0.."
set PY=%~dp0..\.venv\Scripts\python.exe
if not exist "%PY%" (
    echo fish-vision\.venv introuvable — lancez scripts\setup_env.bat
    pause
    exit /b 1
)
"%PY%" scripts\download_fishial.py %*
