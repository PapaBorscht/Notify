@echo off
chcp 65001 >nul
REM ================================================================
REM  install-agent.bat - installs Notify Agent and sets up autostart
REM  via Windows Registry (HKCU Run key)
REM ================================================================

setlocal

set INSTALL_DIR=%LOCALAPPDATA%\NotifyAgent
set EXE_NAME=NotifyAgent.exe

echo ============================================
echo   Notify Agent - Windows Installer
echo ============================================

REM 1. Create install folder
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

REM 2. Copy exe (assumed to be next to this bat file)
echo [..] Copying files...
copy /Y "%~dp0NotifyAgent.exe" "%INSTALL_DIR%\%EXE_NAME%" >nul
if errorlevel 1 (
    echo [ERROR] Could not copy NotifyAgent.exe
    echo Make sure NotifyAgent.exe is in the same folder as this installer
    pause
    exit /b 1
)
echo [OK] Files copied to %INSTALL_DIR%

REM 3. Add to autostart via registry (current user only)
echo [..] Setting up autostart...
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "NotifyAgent" /t REG_SZ /d "\"%INSTALL_DIR%\%EXE_NAME%\"" /f >nul

if errorlevel 1 (
    echo [ERROR] Could not set up autostart
    pause
    exit /b 1
)
echo [OK] Autostart configured

REM 4. Open port 9988 in Windows Firewall
echo [..] Configuring firewall...
netsh advfirewall firewall show rule name="NotifyAgent" >nul 2>&1
if errorlevel 1 (
    netsh advfirewall firewall add rule name="NotifyAgent" dir=in action=allow protocol=TCP localport=9988 >nul
    echo [OK] Firewall rule added (port 9988)
) else (
    echo [OK] Firewall rule already exists
)

REM 5. Start the agent now
echo [..] Starting agent...
start "" "%INSTALL_DIR%\%EXE_NAME%"

echo.
echo ============================================
echo   Installation complete!
echo ============================================
echo.
echo   Agent is running and added to autostart.
echo   Look for the icon in the system tray.
echo.
echo   Log file: %INSTALL_DIR%\notify-agent-%USERNAME%.log
echo.
echo   Test command (run separately, wait a few seconds first):
echo   curl -X POST http://127.0.0.1:9988 -H "X-Token: supersecrettoken123" -H "Content-Type: application/json" -d "{\"title\":\"Test\",\"message\":\"Works!\",\"level\":\"info\"}"
echo.
pause
