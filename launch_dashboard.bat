@echo off
REM Double-click launcher for rcon_dashboard.py (Windows).
REM Checks for Python 3 and the textual package, installing textual if it's
REM missing, then runs the dashboard.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python 3 is required but wasn't found.
    echo Install it from https://www.python.org/downloads/ and try again.
    echo IMPORTANT: check "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

python -c "import textual" >nul 2>nul
if errorlevel 1 (
    echo Installing required package ^(textual^)...
    python -m pip install --quiet textual
    if errorlevel 1 (
        echo Automatic install failed. Try running manually:
        echo   python -m pip install textual
        pause
        exit /b 1
    )
)

python rcon_dashboard.py

pause
