@echo off
cd /d "%~dp0"
echo QLDeviceCheck demo: http://127.0.0.1:8080
echo Leave this window open while using the workbench. Ctrl+C stops the station.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_demo.ps1" %*
if errorlevel 1 pause
