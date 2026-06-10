@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Control del Robot - Servidor Web
echo ============================================================
echo    CONTROL DEL ROBOT  -  servidor web
echo ============================================================
echo.
echo    Cerrando cualquier servidor anterior...
REM Evita tener dos servidores peleando por el robot y el puerto.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*app_web*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 >nul
echo.
echo    Se abrira el navegador en:  http://localhost:5000
echo    Espera a que diga "Conectado" en verde (unos 20-30s).
echo.
echo    Para CERRAR la app: cierra esta ventana negra.
echo ============================================================
echo.
start "" cmd /c "timeout /t 5 >nul & start "" http://localhost:5000"
python app_web.py
echo.
echo ------------------------------------------------------------
echo  El servidor se detuvo. Si ves un error arriba, copialo.
echo ------------------------------------------------------------
pause
