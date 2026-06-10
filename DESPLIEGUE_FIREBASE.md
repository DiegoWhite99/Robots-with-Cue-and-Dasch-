# Análisis de despliegue: subir todo a Firebase (Web Bluetooth)

> Documento de análisis para migrar la web app a Firebase Hosting, con el robot
> controlado **directamente desde el navegador** vía Web Bluetooth (sin servidor
> Python). Rama: `despliegue`.

## 1. Resumen ejecutivo

Subir el proyecto **tal como está** a Firebase/Cloud Run **no es posible**: el
backend (`app_web.py` + `WonderPy`) usa la librería Python `bleak`, que habla por
el **Bluetooth del PC** donde corre. En la nube no hay adaptador BLE ni cercanía
física al robot.

Para tener "todo en Firebase" hay **una sola vía válida**: reescribir el control
del robot usando la **Web Bluetooth API** del navegador (`navigator.bluetooth`).
Así el celular/PC del usuario se conecta directo al robot, y Firebase solo sirve
archivos estáticos. **Esto es una reimplementación, no un despliegue.**

### Restricciones duras de Web Bluetooth (decidir antes de empezar)
- ✅ Funciona en **Chrome/Edge** en Android, Windows, macOS, Linux, ChromeOS.
- ❌ **NO funciona en iPhone/iPad** (Safari ni Chrome iOS). Requiere apps tipo Bluefy.
- ❌ **NO funciona en Firefox**.
- ✅ Requiere **HTTPS** (contexto seguro). Firebase Hosting ya lo da.
- ⚠️ El escaneo (`requestDevice`) debe dispararse desde un **gesto del usuario**
  (un clic), no automáticamente.

## 2. Qué hay que portar de Python a JavaScript

El núcleo es el **protocolo binario reverse-engineered** que hoy vive en
`WonderPy/`. Esto es lo que hay que reescribir en JS:

### 2.1 Capa BLE (`wwBTLEMgr.py` → `bleClient.js`)
UUIDs ya identificados (reutilizables tal cual):

| Elemento | UUID |
|---|---|
| Servicio Dash/Dot | `AF237777-879D-6186-1F49-DECA0E85D9C1` |
| Servicio Cue | `AF237778-879D-6186-1F49-DECA0E85D9C1` |
| Característica COMANDO | `AF230002-879D-6186-1F49-DECA0E85D9C1` |
| Característica SENSOR 0 | `AF230003-879D-6186-1F49-DECA0E85D9C1` |
| Característica SENSOR 1 | `AF230006-879D-6186-1F49-DECA0E85D9C1` |

- `requestDevice({ filters: [{ services: [SERVICE_UUID] }] })`
- `device.gatt.connect()`, `getPrimaryService`, `getCharacteristic`
- `characteristic.writeValueWithoutResponse(bytes)` para comandos
- `characteristic.startNotifications()` + evento para sensores
- Lógica de **doble paquete** (sensor0 + sensor1) del Dash/Cue.

### 2.2 Encoder de comandos (`packet_data_converter.py: encode_cmd` → `encode.js`)
Portar el empaquetado de bits (es lo más delicado):
- `_encode_linear_angular` (conducción) — packet `0x25`, bits de vel/acel.
- `_encode_pose` (avanzar/girar `do_forward`/`do_turn`) — packet `0x23`, 14/12-bit.
- Luces RGB pecho — `0x03` + 3 bytes (R,G,B escalados 0..255).
- Cabeza pan/tilt — `0x06`/`0x07` + ángulo `int(round(deg*-100))` big-endian.
- Parlante — `0x0e`+volumen y `0x18`+nombre ASCII.
- Agregación de paquetes (`MAX_PACKET_LEN = 20`).

### 2.3 Decoder de sensores (`dash_sensor_decode`/`dot_sensor_decode` → `decode.js`)
- Giroscopio (`GYRO_SCALE`), pose del cuerpo (x,y,θ), cabeza pan/tilt.
- Distancias frente-izq / frente-der / atrás (incluye `DoubleExponentialDistanceCalc`).
- Encoders de rueda con manejo de **wrap de 16 bits** (`EncoderExtender`).
- Botones, picked-up, bump, sonido/animación tocando.

### 2.4 Lógica de alto nivel (`robot_core.py` → módulos JS)
- Dead-reckoning (rumbo giroscopio + encoders) y mapa de calor.
- Modo explorador (3 perfiles) y anti-bucle.
- Evita-obstáculos.
- Grabador y reproductor de rutas (temporización por timestamps).

### 2.5 Datos y IA (cambian de hogar)
- **Rutas**: `rutas.db` (SQLite) → **Firestore** o **IndexedDB** (offline local).
- **Agente IA** (`agent.py`, OpenAI): la API key **no puede ir en el navegador**.
  Se mueve a **Cloud Functions for Firebase** (proxy que guarda la key como secreto).
- **Rutina dúo**: Web Bluetooth permite varias conexiones desde una pestaña, así
  que es viable, pero hay que reescribir la coordinación (hoy usa multiprocessing).

## 3. Arquitectura objetivo

```
                Firebase Hosting (HTTPS, estático)
                ┌───────────────────────────────┐
   Navegador →  │  index.html + JS (BLE en JS)  │
   (Chrome)     └───────────────┬───────────────┘
        │                       │ HTTPS (solo para IA)
        │ Web Bluetooth         ▼
        │ (directo)     Cloud Functions ── OpenAI API
        ▼               (oculta la key)
     [Robot Dash/Cue]
                       Firestore ← rutas guardadas
```

## 4. Paso a paso de despliegue

### Fase A — Reescritura del control (lo grueso del trabajo)  ✅ NÚCLEO HECHO
1. ✅ `webapp/public/js/protocol.js` — UUIDs + encode + decode (puerto byte-a-byte).
2. ✅ `webapp/public/js/robot.js` — cliente Web Bluetooth (`WonderRobot`) con
   `drive/stop/head/lights/forward/turn/playSound` y decodificación de sensores.
3. ✅ `webapp/public/index.html` — panel demo funcional (conectar, D-pad, luces,
   cabeza, sonido, telemetría de distancia/rumbo).
4. ✅ Validación byte-a-byte: `webapp/test/check_js.mjs` compara la salida JS contra
   el codificador Python real (`ref_python.py`). **16/16 vectores idénticos.**
   Correr con: `node webapp/test/check_js.mjs`.
5. ✅ Lógica de alto nivel portada (`webapp/public/js/controller.js`):
   dead-reckoning (rumbo giroscopio + encoders), mapa de calor, evita-obstáculos,
   explorador autónomo con 3 perfiles y anti-bucle, grabador y reproductor de rutas.
6. ✅ Rutas en `webapp/public/js/routes-db.js` (IndexedDB, reemplaza SQLite, offline).
7. ✅ UI integrada: el demo usa el `Controller` y añade explorador, anti-choque,
   grabación y lista de rutas (reproducir/borrar).
8. ✅ Validación determinista con robot simulado: `webapp/test/controller_check.mjs`
   (21/21 OK — dead-reckoning, grabador, replay, decisión de giro del explorador).
   Correr con: `node webapp/test/controller_check.mjs`.

> Estado: Fase A completa a nivel de lógica (protocolo + alto nivel), toda
> verificada contra Python y con pruebas deterministas. Falta únicamente la
> prueba final contra el robot real en Chrome, y luego Fases B/C/D (IA en Cloud
> Functions, hosting Firebase).

### Fase B — IA en la nube  ✅ HECHA
6. ✅ `webapp/functions/index.js` — Cloud Function `agent` (proxy OpenAI, puerto de
   `agent.py` con el SYSTEM_PROMPT verbatim). Key como secreto `OPENAI_API_KEY`.
7. ✅ `controller.runPlan()` — ejecuta el plan `{steps}` en el robot (puerto de
   `_run_plan`); el frontend lee el `say` con `speechSynthesis`.
8. ✅ UI con tarjeta IA (input + enviar + parar). `firebase.json` reescribe `/agent`.

### Fase C — Datos  ✅ HECHA (IndexedDB)
9. ✅ `webapp/public/js/routes-db.js` — rutas en IndexedDB (offline, sin servidor).
   No se usó Firestore: las rutas son locales del dispositivo y no necesitan nube.

### Fase D — Hosting y publicación  ✅ SCAFFOLD LISTO
10. ✅ `webapp/firebase.json` (hosting + functions + rewrite) y `.firebaserc`.
11. ⏳ Pasos finales (los ejecuta el usuario, requieren login y project id) — ver
    `webapp/README.md`:
    ```
    npm install -g firebase-tools
    firebase login
    npm --prefix functions install
    firebase functions:secrets:set OPENAI_API_KEY
    firebase deploy --only hosting,functions
    ```

> Estado global: Fases A, B, C completas en código y verificadas con pruebas; Fase D
> con todo el scaffold listo. Falta solo ejecutar el `firebase deploy` (necesita la
> cuenta y el project id del usuario) y la prueba final con el robot real en Chrome.

## 5. Esfuerzo y riesgos

| Riesgo | Impacto |
|---|---|
| Reescritura completa del protocolo BLE | Alto — es el grueso; semanas de trabajo |
| Sin soporte iPhone/iPad | Alto si el público usa iOS |
| Empaquetado de bits sutil (pose/linear-angular) | Medio — requiere validación byte-a-byte |
| Conexión BLE menos estable en navegador que en bleak | Medio — probar en el robot real |
| Key de OpenAI: obligatorio Cloud Functions | Bajo — solucionado con secretos |

## 6. Recomendación del desarrollador de despliegue

Si el objetivo es **publicar ya** sin reescribir, lo correcto es dejar el backend
en el PC (junto al robot) y exponerlo con **Cloudflare Tunnel** (HTTPS gratis).

La vía Firebase + Web Bluetooth es legítima y elegante, pero es un **proyecto de
reimplementación**: presupuestar el porte completo de `WonderPy` a JavaScript y
asumir la pérdida de compatibilidad con iPhone.
