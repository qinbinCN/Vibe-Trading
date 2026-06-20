@echo off
call D:\Miniconda3\Scripts\activate.bat py311 2>nul || call D:\Miniconda3\Scripts\activate.bat base 2>nul
cd /d "%~dp0"
echo ============================================
echo   Vibe-Trading Backend (port 8899)
echo   Conda: %CONDA_DEFAULT_ENV%
echo ============================================
vibe-trading serve --host 0.0.0.0 --port 8899
if errorlevel 1 (
    echo.
    echo ERROR: Backend failed to start.
    echo Check: port 8899 already in use? vibe-trading-ai installed?
    pause
)
