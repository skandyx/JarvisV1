@echo off
REM Kill any Python processes listening on the three NeuroLinked Ops Center ports.
echo Stopping NeuroLinked Ops Center services...
for %%P in (8010 8020 8340) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING"') do (
        taskkill /F /PID %%a >nul 2>&1
    )
)
echo Done.
timeout /t 2 /nobreak >nul
