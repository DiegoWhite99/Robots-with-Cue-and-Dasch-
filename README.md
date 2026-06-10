# Robots with Cue and Dash

Proyecto para controlar robots Wonder Workshop Dash/Cue desde Python, una app web
local y rutinas con IA.

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

## Estructura

- `experimentos/`: app web, rutinas, scripts de prueba y control.
- `WonderPy/`: libreria BLE local para robots Wonder Workshop.
- `requirements.txt`: dependencias principales.
- `requirements-optional.txt`: voz por microfono y empaquetado.
- `requirements-dev.txt`: herramientas de desarrollo/test.
