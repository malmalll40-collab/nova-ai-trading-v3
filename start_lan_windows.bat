@echo off
chcp 65001 > nul
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 (pause & exit /b 1)
echo.
echo =================================================
echo LAN ONLY: connect phone to the same Wi-Fi as PC.
echo Visit http://YOUR_PC_LOCAL_IP:8000 from phone.
echo No public port forwarding. HTTP LAN is not PWA installable.
echo =================================================
ipconfig | findstr /i "IPv4"
set NOVA_BIND_HOST=0.0.0.0
py server.py
pause
