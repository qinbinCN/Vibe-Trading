@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "CONDA_ROOT=D:\Miniconda3"

echo ============================================
echo   Vibe-Trading - Starting from source
echo ============================================

echo.
echo [*] Activating conda py311...
if exist "%CONDA_ROOT%\Scripts\activate.bat" (
    call "%CONDA_ROOT%\Scripts\activate.bat" py311 2>nul
    if errorlevel 1 (
        echo   WARNING: py311 not found, trying base...
        call "%CONDA_ROOT%\Scripts\activate.bat" base 2>nul
    )
    echo   Conda: %CONDA_DEFAULT_ENV%  Python: %CONDA_PYTHON_EXE%
) else (
    echo   WARNING: Conda not found at %CONDA_ROOT%
)

echo.
echo [*] Checking vibe-trading-ai CLI...
where vibe-trading >nul 2>&1
if errorlevel 1 (
    echo   Not found in PATH. Looking in Scripts...
    set "PATH=%CONDA_ROOT%\Scripts;%PATH%"
    where vibe-trading >nul 2>&1
    if errorlevel 1 (
        echo   CLI not found. Reinstalling...
        python -m pip install -e . --no-deps -q
        where vibe-trading >nul 2>&1
        if errorlevel 1 (
            echo   FATAL: vibe-trading CLI still not found after reinstall.
            pause
            exit /b 1
        )
    )
)
echo   OK: vibe-trading found.

echo.
echo [*] Checking frontend node_modules...
if not exist "frontend\node_modules" (
    echo   Installing...
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
echo [*] Starting backend (separate window)...
start "Vibe-Trading Backend" "%~dp0dev-backend.cmd"
echo   Backend window opened. Wait for "Application startup complete".

echo.
echo [*] Starting frontend dev server on http://localhost:5899...
timeout /t 2 /nobreak >nul
start "" http://localhost:5899
cd frontend
call npm run dev -- --host 0.0.0.0 --port 5899
