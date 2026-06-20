@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "CONDA_ROOT=D:\Miniconda3"

echo ============================================
echo   Vibe-Trading - Starting from source
echo ============================================

:: ── Cleanup: kill leftover processes from previous runs ──────────────
echo.
echo [*] Cleaning up leftover processes...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8899.*LISTENING" 2^>nul') do (
    echo   Killing PID %%a (port 8899)...
    taskkill /f /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5899.*LISTENING" 2^>nul') do (
    echo   Killing PID %%a (port 5899)...
    taskkill /f /pid %%a >nul 2>&1
)
taskkill /f /im vibe-trading.exe >nul 2>&1
echo   Cleanup done.

:: ── Activate conda ───────────────────────────────────────────────────
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

:: ── Check backend CLI ────────────────────────────────────────────────
echo.
echo [*] Checking vibe-trading-ai CLI...
where vibe-trading >nul 2>&1
if errorlevel 1 (
    echo   Not found in PATH. Looking in Scripts...
    set "PATH=%CONDA_ROOT%\Scripts;!PATH!"
    where vibe-trading >nul 2>&1
    if errorlevel 1 (
        echo   CLI not found. Reinstalling...
        python -m pip install -e . --no-deps -q
        if errorlevel 1 (
            echo   FATAL: Failed to install vibe-trading-ai.
            pause
            exit /b 1
        )
    )
)
echo   OK: vibe-trading found.

:: ── Check frontend ───────────────────────────────────────────────────
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

:: ── Start backend in separate window ─────────────────────────────────
echo.
echo [*] Starting backend (separate window)...
start "Vibe-Trading Backend" "%~dp0dev-backend.cmd"
echo   Backend window opened. Wait for "Application startup complete".

:: ── Start frontend ───────────────────────────────────────────────────
echo.
echo [*] Starting frontend dev server on http://localhost:5899...
timeout /t 3 /nobreak >nul
start "" http://localhost:5899
cd frontend
call npm run dev -- --host 0.0.0.0 --port 5899
