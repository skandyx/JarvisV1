@echo off
title Jarvis - Voice Assistant
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Run install.bat first.
    pause
    exit /b 1
)

REM Jarvis boots even without keys — you can configure from the gear icon in the UI.
echo Starting Jarvis on http://localhost:8340 ...
echo Press Ctrl+C to stop.
echo.
start "" "http://localhost:8340"
python server.py
