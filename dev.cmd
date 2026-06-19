@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   Vibe-Trading - Starting from source
echo ============================================

echo.
echo [*] Activating conda py311...
call D:\Miniconda3\Scripts\activate.bat py311 2>nul
if errorlevel 1 (
    echo   WARNING: conda activate py311 failed, trying base...
    call D:\Miniconda3\Scripts\activate.bat base 2>nul
)
echo   Conda environment: !CONDA_DEFAULT_ENV!

echo.
echo [*] Checking vibe-trading-ai...
pip show vibe-trading-ai >nul 2>&1
if errorlevel 1 (
    echo   Not installed. Running: pip install -e .
    pip install -e .
    if errorlevel 1 (
        echo   ERROR: pip install -e . failed.
        pause
        exit /b 1
    )
) else (
    echo   OK: vibe-trading-ai found.
)

echo.
echo [*] Checking frontend node_modules...
if not exist "frontend\node_modules" (
    echo   Not found. Running: npm install
    cd frontend
    call npm install
    cd ..
    if errorlevel 1 (
        echo   ERROR: npm install failed.
        pause
        exit /b 1
    )
) else (
    echo   OK: node_modules found.
)

echo.
echo [*] Starting backend API server on port 8899...
start "Vibe-Trading Backend" cmd /c "call D:\Miniconda3\Scripts\activate.bat py311 2>nul && cd /d \"%~dp0\" && vibe-trading serve --host 0.0.0.0 --port 8899"
echo   Backend launching in separate window...

echo.
echo [*] Starting frontend dev server on port 5899...
cd frontend
start "" http://localhost:5899
call npm run dev -- --host 0.0.0.0 --port 5899
