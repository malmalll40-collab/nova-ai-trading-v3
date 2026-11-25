@echo off
chcp 65001 > nul
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo Python install or network error. See README.
  pause
  exit /b 1
)
echo.
echo NOVA mobile dashboard: http://127.0.0.1:8000
echo Stop with Ctrl+C. Phone access requires start_lan_windows.bat and same Wi-Fi.
start "" http://127.0.0.1:8000
py server.py
pause
