@echo off
chcp 65001 >nul
cd /d "%~dp0"

:: Activate conda py311 environment
set CONDA_ROOT=D:\Miniconda3
if exist "%CONDA_ROOT%\Scripts\activate.bat" (
    call "%CONDA_ROOT%\Scripts\activate.bat" py311
    if %errorlevel% neq 0 (
        echo   WARNING: conda activate py311 failed, trying base...
        call "%CONDA_ROOT%\Scripts\activate.bat" base
    )
    echo   Conda environment: %CONDA_DEFAULT_ENV% (Python %CONDA_PYTHON_EXE%)
) else (
    echo   WARNING: Conda not found at %CONDA_ROOT%, using system Python.
)

echo ============================================
echo   Vibe-Trading — Starting from source
echo ============================================

echo.
echo [1/4] Checking Python dependencies...
python -m pip show vibe-trading-ai >nul 2>&1
if %errorlevel% neq 0 (
    echo   Installing vibe-trading-ai (editable mode)...
    python -m pip install -e . -q
    if %errorlevel% neq 0 (
        echo   ERROR: pip install -e . failed. Check Python 3.11+ environment.
        pause
        exit /b 1
    )
    echo   Done: vibe-trading-ai installed.
) else (
    echo   Done: vibe-trading-ai already installed.
)

echo.
echo [2/4] Checking frontend dependencies...
if not exist "frontend\node_modules" (
    echo   Installing frontend npm packages...
    cd frontend
    call npm install
    cd ..
    if %errorlevel% neq 0 (
        echo   ERROR: npm install failed. Check Node.js installation.
        pause
        exit /b 1
    )
    echo   Done: frontend packages installed.
) else (
    echo   Done: frontend packages already installed.
)

echo.
echo [3/4] Starting backend API server (port 8899)...
:: Backend window also needs conda activation
start "Vibe-Trading Backend" cmd /c "call D:\Miniconda3\Scripts\activate.bat py311 && cd /d \"%~dp0\" && echo Conda: %CONDA_DEFAULT_ENV% && vibe-trading serve --host 0.0.0.0 --port 8899"
echo   Backend started in separate window.

echo.
echo [4/4] Starting frontend dev server (port 5899)...
cd frontend
echo   Opening http://localhost:5899 in browser...
start "" http://localhost:5899
call npm run dev -- --host 0.0.0.0 --port 5899
