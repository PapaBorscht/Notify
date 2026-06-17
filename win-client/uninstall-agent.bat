@echo off
chcp 65001 >nul
REM ================================================================
REM  uninstall-agent.bat - completely removes Notify Agent
REM ================================================================

setlocal
set INSTALL_DIR=%LOCALAPPDATA%\NotifyAgent

echo ============================================
echo   Notify Agent - Uninstall
echo ============================================

echo [..] Stopping agent...
taskkill /IM NotifyAgent.exe /F >nul 2>&1

echo [..] Removing autostart...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "NotifyAgent" /f >nul 2>&1

echo [..] Removing firewall rule...
netsh advfirewall firewall delete rule name="NotifyAgent" >nul 2>&1

echo [..] Removing files...
if exist "%INSTALL_DIR%" rmdir /S /Q "%INSTALL_DIR%"

echo.
echo ============================================
echo   Notify Agent removed completely
echo ============================================
pause
