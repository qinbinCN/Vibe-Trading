@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Ensure conda and local scripts are on PATH
set "CONDA_ROOT=D:\Miniconda3"
set "PATH=%CONDA_ROOT%;%CONDA_ROOT%\Scripts;%CONDA_ROOT%\Library\bin;%PATH%"

echo ============================================
echo   Vibe-Trading - Starting from source
echo ============================================

echo.
echo [*] Activating conda py311...
if exist "!CONDA_ROOT!\Scripts\activate.bat" (
    call "!CONDA_ROOT!\Scripts\activate.bat" py311 2>nul
    if errorlevel 1 (
        echo   WARNING: conda activate py311 failed, trying base...
        call "!CONDA_ROOT!\Scripts\activate.bat" base 2>nul
    )
    echo   Conda environment: !CONDA_DEFAULT_ENV!
    echo   Python: !CONDA_PYTHON_EXE!
) else (
    echo   WARNING: Conda not found at !CONDA_ROOT!
)

echo.
echo [*] Checking vibe-trading-ai...
python -m pip show vibe-trading-ai >nul 2>&1
if errorlevel 1 (
    echo   Not installed. Running: python -m pip install -e . --no-deps
    python -m pip install -e . --no-deps -q
    if errorlevel 1 (
        echo   WARNING: pip install -e . failed. Trying full install...
        python -m pip install -e . -q
        if errorlevel 1 (
            echo   ERROR: pip install -e . failed.
            pause
            exit /b 1
        )
    )
    if not exist "!CONDA_ROOT!\Scripts\mootdx.exe" (
        echo   Installing mootdx ^(a-stock-data K-line^)...
        python -m pip install mootdx stockstats --no-deps -q 2>nul
    )
    echo   Done: vibe-trading-ai installed.
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
start "Vibe-Trading Backend" cmd /c "call D:\Miniconda3\Scripts\activate.bat py311 2>nul && cd /d \"%~dp0\" && echo Starting Vibe-Trading Backend... && vibe-trading serve --host 0.0.0.0 --port 8899 || (echo ERROR: Backend failed to start. Check if port 8899 is already in use. && pause)"
echo   Backend launching in separate window...

echo.
echo [*] Starting frontend dev server on port 5899...
cd frontend
start "" http://localhost:5899
call npm run dev -- --host 0.0.0.0 --port 5899
