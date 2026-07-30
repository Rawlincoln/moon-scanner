@echo off
cd /d "%~dp0"
title Moon Scanner
echo.
echo  Moon Scanner launcher
echo  Keep this window open — closing it can stop restarts.
echo.

REM Prefer PowerShell durable runner (single instance + health watch)
where powershell >nul 2>&1
if %ERRORLEVEL%==0 (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-server.ps1"
  if errorlevel 1 (
    echo PowerShell runner failed — falling back to simple loop...
    goto simple
  )
  goto :eof
)

:simple
if not exist "data" mkdir data
echo Freeing port 8765...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING" 2^>nul') do (
  taskkill /F /PID %%P >nul 2>&1
)
ping -n 2 127.0.0.1 >nul 2>&1

set DISABLE_SOLANA_WS=1
set REALTIME_PUMP_POLL_SEC=5
set LEARNING_ACTIVE_CAP_PUBLIC=20
set SOLANA_WS_MODE=logs

:loop
echo [%date% %time%] starting uvicorn...
py -3 -m uvicorn main:app --host 127.0.0.1 --port 8765
echo [%date% %time%] exited %ERRORLEVEL% — restart in 3s
ping -n 4 127.0.0.1 >nul 2>&1
goto loop
