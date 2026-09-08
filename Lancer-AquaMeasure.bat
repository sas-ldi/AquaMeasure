@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Environnement Python absent. Suivez la rubrique Lancer depuis les sources du README.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "src\interface\main.py"
if errorlevel 1 pause
endlocal
