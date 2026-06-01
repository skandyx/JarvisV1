@echo off
setlocal EnableDelayedExpansion
title Jarvis Installer - premium tier Edition

echo.
echo ========================================================
echo   JARVIS - Voice Assistant Installer
echo   premium tier Edition
echo ========================================================
echo.

REM ---- Check Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not installed or not on PATH.
    echo.
    echo   Install Python 3.10 or newer from:
    echo   https://www.python.org/downloads/
    echo.
    echo   Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PY_VER=%%i
echo [ok] Python %PY_VER% detected

REM ---- Install pip dependencies ----
echo.
echo [1/4] Installing Python dependencies...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed. See error above.
    pause
    exit /b 1
)

REM ---- Install Playwright browser ----
echo.
echo [2/4] Installing Playwright Chromium browser...
python -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Playwright install failed. Browser control will not work until fixed.
)

REM ---- Config (interactive, optional) ----
echo.
echo [3/4] Configuration
echo.
echo   Jarvis supports multiple LLM providers. Pick one (or skip and use
echo   the gear icon in the UI later):
echo.
echo     1) Claude (Anthropic)   - best all-round
echo     2) GPT (OpenAI)
echo     3) Groq                 - fastest, free tier
echo     4) Ollama               - local, 100%% free, no key needed
echo     5) Skip and configure in the UI
echo.
set /p LLM_CHOICE="Choose [1-5] (default 1): "
if "%LLM_CHOICE%"=="" set LLM_CHOICE=1

set LLM_PROVIDER=anthropic
set API_KEY=
set KEY_FIELD=anthropic_api_key
if "%LLM_CHOICE%"=="1" (
    set /p API_KEY="Anthropic API key (sk-ant-...): "
    set LLM_PROVIDER=anthropic
    set KEY_FIELD=anthropic_api_key
) else if "%LLM_CHOICE%"=="2" (
    set /p API_KEY="OpenAI API key (sk-...): "
    set LLM_PROVIDER=openai
    set KEY_FIELD=openai_api_key
) else if "%LLM_CHOICE%"=="3" (
    set /p API_KEY="Groq API key (gsk_...): "
    set LLM_PROVIDER=groq
    set KEY_FIELD=groq_api_key
) else if "%LLM_CHOICE%"=="4" (
    set LLM_PROVIDER=ollama
    set KEY_FIELD=ollama_api_key
    set API_KEY=ollama
    echo [info] Ollama needs to be running at http://localhost:11434
)

echo.
echo   Voice: by default Jarvis uses the free browser voice.
echo   Optional: paste an ElevenLabs key for premium voice, or press Enter to skip.
set /p ELEVEN_KEY="ElevenLabs API key (optional): "
set /p VOICE_ID=  "ElevenLabs voice ID (optional): "

echo.
set /p USER_NAME="Your name: "
set /p USER_CITY="Your city (for weather): "

REM ---- Write config via Python (safer than batch string replace) ----
python -c "import json; p='config.json'; c=json.load(open(p,encoding='utf-8')); c['llm_provider']='%LLM_PROVIDER%'; c['%KEY_FIELD%']='%API_KEY%'; c['elevenlabs_api_key']='%ELEVEN_KEY%'; c['elevenlabs_voice_id']='%VOICE_ID%'; c['user_name']='%USER_NAME%'; c['city']='%USER_CITY%'; c['tts_provider']='auto'; json.dump(c, open(p,'w',encoding='utf-8'), indent=2); print('[ok] config.json written')"

REM ---- Detect NeuroLinked Brain ----
echo.
echo [4/4] Checking for NeuroLinked Brain...
curl -s -m 2 http://localhost:8000/ >nul 2>&1
if errorlevel 1 (
    echo [info] NeuroLinked Brain not detected at http://localhost:8000
    echo [info] Jarvis will auto-connect when the Brain comes online.
) else (
    echo [ok] NeuroLinked Brain detected - Jarvis will auto-connect on startup.
)

echo.
echo ========================================================
echo   Installation complete.
echo ========================================================
echo.
echo   Start with: start.bat
echo   Or edit settings in the UI via the gear icon.
echo.
pause
