# Experimentos con Dash y Dot

Proyecto de investigación usando Wonder Workshop Dash y Dot robots en Windows con WonderPy (fork axlan) + bleak.

## Requisitos
- Python 3.9+
- Windows 11 con Bluetooth BLE
- WonderPy instalado desde `D:\Documentos\robots\WonderPy`

## Estructura
```
experimentos/
├── dash/          # Scripts exclusivos para Dash
├── dot/           # Scripts exclusivos para Dot
├── shared/        # Utilidades compartidas
└── main.py        # Punto de entrada general
```

## Cómo conectar
1. Encender el robot (botón central hasta que parpadee)
2. Ejecutar el script deseado: `python dash/mover.py`

## Control remoto gráfico (con grabación de rutas)
Interfaz tipo control remoto para manejar el robot, jugar con él, **grabar la ruta**
y guardarla en una base de datos SQLite (`rutas.db`) para reproducirla cuando quieras.

```bash
python control_remoto.py            # conecta al primer robot que encuentre
python control_remoto.py --type dash
python control_remoto.py --name MiDash
```

- **Conducir:** botones en pantalla o teclado (flechas / `W A S D`), mantén
  presionado para avanzar; `Espacio` para parar. Sliders de velocidad y giro.
- **Cabeza:** sliders de pan (-120°…120°) y tilt (-10°…22°) + "Centrar cabeza".
- **Luces:** colores predefinidos o selector de color libre.
- **Voz (micrófono):** botón `🎤 Escuchar`. Reconocimiento online con Google
  (necesita internet). Comandos en español, ver lista abajo.
- **Grabar ruta:** botón `● Grabar`; haz tu recorrido (con botones, teclado o
  voz); `■ Detener y guardar` y ponle nombre. Se almacena en `rutas.db`.
- **Reproducir:** elige una ruta de la lista y pulsa `▶ Reproducir` (el robot
  repite el recorrido con la misma temporización). `Borrar` elimina rutas.

> La conducción usa comandos de velocidad continuos (`stage_linear_angular`),
> que no bloquean, así que la interfaz responde al instante.

### Comandos de voz
| Dices… | Hace |
|--------|------|
| "adelante", "avanza", "para adelante" | avanza ~25 cm |
| "atrás", "reversa", "retrocede" | retrocede ~25 cm |
| "izquierda" / "derecha" | gira ~90° a ese lado |
| "para", "alto", "párate", "frena" | detiene |
| "luz roja / verde / azul / amarilla / blanca" | enciende esa luz |
| "apaga la luz", "oscuro" | apaga luces |
| "mira arriba" / "mira abajo" / "centra" | mueve la cabeza |
| "baila", "celebra" | rutina de fiesta |

> "para adelante"/"para atrás" se interpretan como **dirección** (no como detener);
> "para" a secas sí detiene.

### Dependencias
```bash
cd ..
pip install -r requirements.txt
pip install -r requirements-optional.txt   # opcional: voz de escritorio + empaquetado
```
La voz es opcional: si esas librerías no están, la app abre igual y solo
desactiva el botón del micrófono.

## Empaquetar como app (.exe) para usuario final
Con [PyInstaller](https://pyinstaller.org):
```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name "ControlRobot" ^
  --collect-all WonderPy --collect-all bleak --collect-all speech_recognition ^
  --hidden-import pyaudio control_remoto.py
```
El `.exe` queda en `dist\ControlRobot.exe`. Notas:
- `--noconsole` oculta la terminal (no se ven mensajes de fondo del Bluetooth).
- La base de datos `rutas.db` se crea junto al ejecutable.
- La voz necesita internet (Google) y permiso de micrófono de Windows.
- Para doble clic sin empaquetar, usa `iniciar.bat`.

## Servidor web (modo estable + seguridad básica)
```bash
cd experimentos
python app_web.py --prod
```

Opciones útiles:
- `--api-key TU_CLAVE` o variable `ROBOT_API_KEY=TU_CLAVE`: protege todas las rutas `/api`.
- `--allow-net 10.8.0.0/24` (repetible): permite subredes concretas.
- `--allow-public`: permite IP pública (no recomendado).
- `--https`: usa HTTPS temporal de Flask (útil para micrófono móvil).

Si activas `--api-key`, abre el panel con:
```text
http://localhost:5000/?api_key=TU_CLAVE
```

### Inicio rápido con scripts (Windows)
- `INICIAR_APP_WEB.bat`: arranque clásico.
- `INICIAR_APP_WEB_PROD.bat`: producción con Waitress y API key.
- `INICIAR_APP_WEB_celular_https.bat`: HTTPS para micrófono en celular (aceptando certificado temporal).
- `DETENER_APP_WEB.bat`: detiene cualquier `app_web.py` activo.

## Smoke test rápido (sin pytest)
```bash
cd experimentos
python smoke_test_app_web.py
```
