@echo off
chcp 65001 >nul
REM ================================================================
REM  build.bat - builds agent_windows.py into a single .exe file
REM ================================================================

echo ============================================
echo   Notify Agent - Build .exe for Windows
echo ============================================

REM 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://python.org
    echo Make sure to check "Add Python to PATH" during install
    pause
    exit /b 1
)

echo [OK] Python found
python --version

REM 2. Install dependencies
echo.
echo [..] Installing dependencies, this may take a minute...
pip install PyQt5 pyinstaller

if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)

REM 3. Build .exe
echo.
echo [..] Building NotifyAgent.exe...

if exist notify.ico (
    pyinstaller --onefile --windowed --name "NotifyAgent" --icon=notify.ico agent_windows.py
) else (
    echo [INFO] notify.ico not found, building without custom icon
    pyinstaller --onefile --windowed --name "NotifyAgent" agent_windows.py
)

if errorlevel 1 (
    echo [ERROR] Build failed, see messages above
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Done! File: dist\NotifyAgent.exe
echo ============================================
echo.
echo Next steps:
echo   1. Copy dist\NotifyAgent.exe to the target PC
echo   2. Run install-agent.bat to set up autostart
echo.
pause
