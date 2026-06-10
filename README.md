# Robots with Cue and Dash

Proyecto para controlar robots Wonder Workshop Dash/Cue desde Python, una app web
local y rutinas con IA.

> 🌐 **App web en la nube:** existe una segunda versión que controla el robot
> **directo desde el navegador** por Web Bluetooth (sin servidor Python), desplegada
> en Firebase: **https://cue-and-dash.web.app** (abrir en Chrome/Edge; no funciona en
> iPhone/iPad). Ver [`webapp/README.md`](webapp/README.md).

## Instalacion rapida

```powershell
cd d:\Documentos\robots
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Opcionales para voz de escritorio y empaquetado:

```powershell
pip install -r requirements-optional.txt
```

## Arranque web

```powershell
cd experimentos
python app_web.py --prod --api-key TU_CLAVE
```

Abre:

```text
http://localhost:5000/?api_key=TU_CLAVE
```

Tambien puedes usar los scripts:

- `experimentos\INICIAR_APP_WEB.bat`
- `experimentos\INICIAR_APP_WEB_PROD.bat`
- `experimentos\INICIAR_APP_WEB_celular_https.bat`
- `experimentos\DETENER_APP_WEB.bat`

## Tests

```powershell
python -m compileall -q experimentos WonderPy
python experimentos\smoke_test_app_web.py
python -m unittest discover -s WonderPy\test -p "test_*.py"
```

El test fisico de Dash se ejecuta con el robot encendido:

```powershell
cd experimentos
python dash_test.py
```

## App web en Firebase (Web Bluetooth)

Versión que no necesita PC con Python: el navegador habla BLE directo con el robot.
Está en `webapp/` y desplegada en https://cue-and-dash.web.app.

Probar la lógica sin robot (puerto del protocolo verificado contra Python):

```powershell
node webapp\test\check_js.mjs          # protocolo: 16/16 identico a Python
node webapp\test\controller_check.mjs  # alto nivel: 27/27
```

Desplegar (resumen — detalle en `webapp/README.md`):

```powershell
cd webapp
firebase deploy --only hosting                 # control del robot (plan gratis)
firebase functions:secrets:set OPENAI_API_KEY  # solo para la IA (plan Blaze)
firebase deploy --only functions:agent         # despliega la IA sin tocar otras funciones
```

## Estructura

- `experimentos/`: app web local (Flask), rutinas, scripts de prueba y control.
- `WonderPy/`: libreria BLE local para robots Wonder Workshop (Python).
- `webapp/`: app web Web Bluetooth para Firebase (control en JavaScript, sin Python).
  - `webapp/public/js/`: protocolo, cliente BLE, controlador y rutas en JS.
  - `webapp/functions/`: Cloud Function de IA (proxy OpenAI).
- `DESPLIEGUE_FIREBASE.md`: analisis de despliegue y estado de las fases.
- `requirements.txt`: dependencias principales.
- `requirements-optional.txt`: voz por microfono y empaquetado.
- `requirements-dev.txt`: herramientas de desarrollo/test.
