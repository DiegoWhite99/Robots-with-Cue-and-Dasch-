@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Control del Robot - Servidor Web (HTTPS para celular)
echo ============================================================
echo    CONTROL DEL ROBOT  -  modo HTTPS (microfono en celular)
echo ============================================================
echo.
if "%ROBOT_API_KEY%"=="" (
  set /p ROBOT_API_KEY=API key opcional para proteger /api (Enter = sin clave): 
)

echo    Cerrando cualquier servidor anterior...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*app_web*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 >nul
echo.
echo    En el CELULAR (misma WiFi) abre:
if "%ROBOT_API_KEY%"=="" (
  echo        https://192.168.1.99:5000
) else (
  echo        https://192.168.1.99:5000/?api_key=TU_CLAVE
)
echo    Aparecera un aviso de "sitio no seguro" por el
echo    certificado temporal: toca "Avanzado" y "Continuar".
echo.
if "%ROBOT_API_KEY%"=="" (
  echo    En este PC abre:  https://localhost:5000
) else (
  echo    En este PC abre:  https://localhost:5000/?api_key=TU_CLAVE
)
echo.
echo    Para CERRAR la app: cierra esta ventana negra.
echo ============================================================
echo.
if "%ROBOT_API_KEY%"=="" (
  start "" cmd /c "timeout /t 5 >nul & start "" https://localhost:5000"
  python app_web.py --https
) else (
  start "" cmd /c "timeout /t 5 >nul & start "" https://localhost:5000/?api_key=%ROBOT_API_KEY%"
  python app_web.py --https --api-key "%ROBOT_API_KEY%"
)
echo.
echo ------------------------------------------------------------
echo  El servidor se detuvo. Si ves un error arriba, copialo.
echo  (Si fallo por 'cryptography', ejecuta:  pip install cryptography)
echo ------------------------------------------------------------
pause
