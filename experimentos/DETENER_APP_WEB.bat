@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Detener servidor app_web
echo Cerrando procesos de app_web.py...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*app_web*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Listo.
timeout /t 1 >nul
