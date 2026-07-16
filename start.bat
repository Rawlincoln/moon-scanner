@echo off
cd /d "%~dp0"
echo Moon Scanner — http://127.0.0.1:8765
echo Press Ctrl+C to stop. Server auto-restarts if it crashes.
:loop
echo [%date% %time%] Starting server...
py -3 -m uvicorn main:app --host 127.0.0.1 --port 8765
echo [%date% %time%] Server stopped — restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop