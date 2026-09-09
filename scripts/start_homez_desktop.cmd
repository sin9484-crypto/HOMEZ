@echo off
REM =========================================================
REM Homez OS
REM
REM File : scripts/start_homez_desktop.cmd
REM
REM HOMEZ Desktop Shell launcher (PyWebView independent window).
REM Plain-ASCII on purpose, same convention as scripts/start_homez.cmd.
REM Korean log messages are written by app/desktop/server.py's logger
REM (UTF-8 file, not console output) to storage\logs\desktop-launcher.log.
REM
REM This is the DEVELOPMENT desktop launcher - it runs app.desktop.main
REM directly with the project's venv Python. It does not build or run
REM HOMEZ.exe (PyInstaller packaging is a separate, later Gate).
REM
REM scripts/start_homez.cmd (browser-based launcher) remains available
REM as a fallback if the desktop window path has an issue.
REM =========================================================

chcp 65001 >nul

cd /d "%~dp0.."

if not exist "venv\Scripts\python.exe" (
    echo.
    echo [HOMEZ Desktop] venv not found at venv\Scripts\python.exe
    echo Run the project setup first.
    pause
    exit /b 1
)

"venv\Scripts\python.exe" -m app.desktop.main

if errorlevel 1 (
    echo.
    echo [HOMEZ Desktop] Startup failed. See storage\logs\desktop-launcher.log
    pause
)
