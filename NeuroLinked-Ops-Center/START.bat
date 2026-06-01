@echo off
REM =====================================================================
REM   NeuroLinked Ops Center — one-click launcher
REM   Starts the NeuroLinked Brain (:8020), Jarvis (:8340), and the
REM   Ops Center dashboard (:8010), then opens Chrome to the dashboard.
REM
REM   First run: see README.md for the 60-second setup.
REM =====================================================================

setlocal
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

REM ---- 1. Python check --------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Python is not installed or not in PATH.
    echo   Get it from: https://www.python.org/downloads/
    echo   IMPORTANT: tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

REM ---- 2. Install Python deps the FIRST time (silent thereafter) -------
echo Checking Python dependencies (one-time, ~60 sec on first run)...
python -m pip install --quiet --user --upgrade pip >nul 2>&1
python -m pip install --quiet --user fastapi uvicorn websockets httpx pyyaml anthropic openai groq psutil cryptography pillow numpy python-multipart >nul 2>&1

REM ---- 3. Generate a launch token shared by all three services --------
if not exist "%ROOT%\neurolinked-brain\.launch-token" (
    echo Generating launch token...
    python -c "import secrets; open(r'%ROOT%\neurolinked-brain\.launch-token','w').write(secrets.token_urlsafe(32))"
)
set /p TOKEN=<"%ROOT%\neurolinked-brain\.launch-token"

REM ---- 4. Start all three services ------------------------------------
echo.
echo Starting NeuroLinked Brain on :8020...
start "Brain" /min cmd /c "set NEUROLINKED_TOKEN=%TOKEN% && cd /d ""%ROOT%\neurolinked-brain"" && python run.py --port 8020 --host 127.0.0.1"
timeout /t 8 /nobreak >nul

echo Starting Jarvis on :8340...
start "Jarvis" /min cmd /c "set NEUROLINKED_TOKEN=%TOKEN% && cd /d ""%ROOT%\ops-center\_jarvis"" && python server.py"
timeout /t 4 /nobreak >nul

echo Starting Ops Center on :8010...
start "OpsCenter" /min cmd /c "set NEUROLINKED_TOKEN=%TOKEN% && cd /d ""%ROOT%\ops-center"" && python server.py"
timeout /t 4 /nobreak >nul

echo.
echo Opening dashboard in your default browser...
start "" "http://localhost:8010"

echo.
echo  ================================================
echo    NeuroLinked Ops Center is running.
echo
echo    Ops Center dashboard:   http://localhost:8010
echo    NeuroLinked Brain:      http://localhost:8020
echo    Jarvis voice/text UI:   http://localhost:8340
echo  ================================================
echo.
echo  Close this window or run STOP.bat to shut it down.
echo.
pause
