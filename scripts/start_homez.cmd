@echo off
REM =========================================================
REM Homez OS
REM
REM File : scripts/start_homez.cmd
REM
REM HOMEZ local launcher entry point (what the desktop icon runs).
REM Keeps this file plain-ASCII on purpose - all Korean user-facing
REM messages are emitted by start_homez.ps1 (UTF-8 with BOM), not here.
REM Switches the console to UTF-8 (chcp 65001) first so PowerShell's
REM Korean output renders correctly instead of mojibake.
REM =========================================================

chcp 65001 >nul

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_homez.ps1"

if errorlevel 1 (
    echo.
    echo [HOMEZ Launcher] Startup failed. See the messages above and storage\logs\homez-launcher.log
    pause
)
