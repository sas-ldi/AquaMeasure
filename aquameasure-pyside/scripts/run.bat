@echo off
setlocal
REM Racine du depot : scripts\ -> aquameasure-pyside\ -> depot
cd /d "%~dp0..\.."
set ROOT=%CD%
if exist "%ROOT%\fish-vision\.venv\Scripts\python.exe" (
  set PY=%ROOT%\fish-vision\.venv\Scripts\python.exe
) else (
  set PY=python
)
"%PY%" -m pip install -q -r "%ROOT%\aquameasure-pyside\requirements.txt" 2>nul
"%PY%" "%ROOT%\aquameasure-pyside\main.py"
endlocal
