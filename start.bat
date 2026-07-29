@echo off
cd /d "%~dp0"
title Moon Scanner
echo Moon Scanner — http://127.0.0.1:8765
if not exist ".env" if exist ".env.example" (
  copy /Y ".env.example" ".env" >nul
  echo Created .env from .env.example — set HELIUS_API_KEY to stop Solana 429s.
)
if exist ".env" (
  echo Loading .env ^(HELIUS_API_KEY / SOLANA_RPC_* if set^)
)
echo Freeing port 8765...

REM Kill listeners on 8765 (PID is last column of netstat -ano)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING" 2^>nul') do (
  echo   taskkill PID %%P
  taskkill /F /PID %%P >nul 2>&1
)
REM Brief wait without "timeout" (breaks under some redirected shells)
ping -n 2 127.0.0.1 >nul 2>&1

echo Starting. Press Ctrl+C to stop. Auto-restarts on crash.
:loop
echo [%date% %time%] uvicorn...
py -3 -m uvicorn main:app --host 127.0.0.1 --port 8765
set EXITCODE=%ERRORLEVEL%
echo [%date% %time%] exited %EXITCODE% — restart in 3s...
ping -n 4 127.0.0.1 >nul 2>&1
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING" 2^>nul') do (
  taskkill /F /PID %%P >nul 2>&1
)
goto loop
