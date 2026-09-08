@echo off
REM Export fish_annotations.db + fine-tune depuis fish_detect_family.pt
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo Run setup_env.bat first
  exit /b 1
)
set RANK=%1
if "%RANK%"=="" set RANK=family
.venv\Scripts\python.exe scripts\retrain_from_db.py --rank %RANK% %*
