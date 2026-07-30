@echo off
cd /d "%~dp0"
title Moon Scanner (free / no paid RPC)

echo ============================================================
echo  Moon Scanner — FREE MODE (no Helius / paid RPC required)
echo  URL: http://127.0.0.1:8765
echo ============================================================
echo.
echo  What this does:
echo    - Uses public Solana RPC or whatever is already in .env
echo    - Turns OFF Solana websocket (avoids public WS 429 spam)
echo    - Slower pump poll + smaller learning poll cap
echo    - Moon / Snipes still work via pump.fun + Dex + RugCheck
echo.
echo  Better free options (optional):
echo    1. Helius free key → set HELIUS_API_KEY in .env  (start.bat)
echo    2. Alchemy free Solana → set SOLANA_RPC_HTTP + SOLANA_RPC_WSS
echo    See FREE_RPC.md
echo.

if not exist ".env" if exist ".env.example" (
  copy /Y ".env.example" ".env" >nul
  echo Created .env from .env.example
)

REM Process-level free preset (overrides empty .env defaults; does not edit .env file)
REM Clear paid endpoints for this session so public path is used unless user re-sets them
set "HELIUS_API_KEY="
set "SOLANA_RPC_HTTP="
set "SOLANA_RPC_WSS="
set "DISABLE_SOLANA_WS=1"
set "REALTIME_PUMP_POLL_SEC=4"
set "LEARNING_ACTIVE_CAP_PUBLIC=25"
set "SOLANA_WS_MODE=logs"

echo Free preset active: DISABLE_SOLANA_WS=1  POLL=4s  LEARN_CAP=25
echo Freeing port 8765...

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING" 2^>nul') do (
  echo   taskkill PID %%P
  taskkill /F /PID %%P >nul 2>&1
)
ping -n 2 127.0.0.1 >nul 2>&1

echo Starting free mode. Press Ctrl+C to stop. Auto-restarts on crash.
:loop
echo [%date% %time%] uvicorn (free mode)...
py -3 -m uvicorn main:app --host 127.0.0.1 --port 8765
set EXITCODE=%ERRORLEVEL%
echo [%date% %time%] exited %EXITCODE% — restart in 3s...
ping -n 4 127.0.0.1 >nul 2>&1
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING" 2^>nul') do (
  taskkill /F /PID %%P >nul 2>&1
)
goto loop
