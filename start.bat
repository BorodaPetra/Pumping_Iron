@echo off
rem Start Pumping Iron and open it in the browser. Close this window to stop the server.
cd /d "%~dp0"
start "" /b cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000"
uv run pumping-iron
