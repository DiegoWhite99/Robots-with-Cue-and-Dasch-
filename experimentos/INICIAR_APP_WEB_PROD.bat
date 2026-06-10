@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Control del Robot - Servidor Web (PROD)
echo ============================================================
echo    CONTROL DEL ROBOT  -  modo PRODUCCION (Waitress)
echo ============================================================
echo.

if "%ROBOT_API_KEY%"=="" (
  set /p ROBOT_API_KEY=Ingresa API key para proteger /api (ej: MiClave123): 
)

if "%ROBOT_API_KEY%"=="" (
  echo [ERROR] Debes definir una API key.
  pause
  exit /b 1
)

echo    Cerrando cualquier servidor anterior...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*app_web*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 >nul

python -c "import waitress" >nul 2>&1
if errorlevel 1 (
  echo    Waitress no esta instalada. Instalando...
  pip install waitress
  if errorlevel 1 (
    echo [ERROR] No se pudo instalar waitress.
    pause
    exit /b 1
  )
)

echo.
echo    Se abrira el navegador en:
echo       http://localhost:5000/?api_key=TU_CLAVE
echo    (La clave real queda guardada solo en esta ventana)
echo.
echo    Para CERRAR la app: cierra esta ventana negra
echo    o ejecuta DETENER_APP_WEB.bat
echo ============================================================
echo.

start "" cmd /c "timeout /t 4 >nul & start "" http://localhost:5000/?api_key=%ROBOT_API_KEY%"
python app_web.py --prod --api-key "%ROBOT_API_KEY%"

echo.
echo ------------------------------------------------------------
echo  El servidor se detuvo. Si ves un error arriba, copialo.
echo ------------------------------------------------------------
pause
