@echo off
setlocal
cd /d "%~dp0.."

if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    py -3.10 -m venv .venv
    if errorlevel 1 (
        echo Failed to create venv. Ensure Python 3.10+ is installed.
        exit /b 1
    )
)

echo Installing dependencies...
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\pip install -r requirements.txt

echo.
echo Installing PyTorch CUDA (RTX 5070 Ti / cu128)...
.venv\Scripts\python.exe -m pip uninstall torch torchvision -y >nul 2>&1
.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

echo.
echo Checking CUDA...
.venv\Scripts\python.exe -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"

echo.
echo Initializing annotation database...
.venv\Scripts\python.exe scripts\db_init.py

echo.
echo Done. Activate with: fish-vision\.venv\Scripts\activate
endlocal
