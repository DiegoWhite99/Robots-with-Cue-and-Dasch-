# Informe de cambios — Despliegue web del robot en Firebase

Rama: `despliegue` · Sitio en vivo: **https://cue-and-dash.web.app**

## 1. Resumen general

Se creó una **segunda versión de la app web** que controla el robot Dash/Cue
**directamente desde el navegador** usando la **Web Bluetooth API**, eliminando la
necesidad de un PC con Python corriendo junto al robot. Esto permitió **desplegarla
en Firebase Hosting** (sitio estático) con la **IA en Cloud Functions**.

El proyecto Python original (`experimentos/` + `WonderPy/`) **no se modificó**: sigue
funcionando igual. Todo lo nuevo vive en `webapp/`.

### Decisión técnica de fondo
La versión Python usa la librería `bleak`, que habla por el Bluetooth **del PC**. En
la nube no hay Bluetooth, así que el backend Python no se puede subir a Firebase/Cloud
Run. La única vía para "todo en Firebase" era reescribir el control del robot en
JavaScript con Web Bluetooth (el navegador del usuario habla BLE directo). Eso se hizo.

### Limitación heredada de la plataforma
Web Bluetooth funciona en **Chrome/Edge** (Android, Windows, macOS, Linux). **No
funciona en iPhone/iPad ni Firefox** (Apple/Mozilla no lo implementan). El panel avisa
de esto automáticamente según el dispositivo.

## 2. Cambios específicos por fase

### Fase A — Núcleo del protocolo (lo más delicado)
Puerto **byte-a-byte** del protocolo Wonder Workshop de Python a JavaScript.

- `webapp/public/js/protocol.js`: UUIDs BLE + codificador de comandos (conducir,
  pose/avanzar/girar, luces RGB, cabeza pan/tilt, parlante) + decodificador de sensores
  (giroscopio, distancias, encoders con wrap de 16 bits, pose, botones).
- **Verificación:** `webapp/test/check_js.mjs` compara la salida JS contra el
  codificador Python real de WonderPy. **16/16 vectores idénticos.**

### Fase A — Cliente BLE y lógica de alto nivel
- `webapp/public/js/robot.js`: clase `WonderRobot` (Web Bluetooth): conectar, detectar
  tipo (Dash/Cue), suscripción a sensores con lógica de doble paquete, comandos con
  dedupe y cola de escritura GATT.
- `webapp/public/js/controller.js`: puerto de `robot_core.Controller`:
  - Dead-reckoning (rumbo del giroscopio + encoders) y mapa de calor.
  - Evita-obstáculos.
  - Explorador autónomo con 3 perfiles (suave/normal/agresivo) y anti-bucle.
  - Grabador y reproductor de rutas.
  - Ejecutor de planes del agente (`runPlan`, puerto de `_run_plan`) y baile.
- `webapp/public/js/routes-db.js`: almacén de rutas en **IndexedDB** (reemplaza el
  SQLite, 100% local/offline).
- **Verificación:** `webapp/test/controller_check.mjs` con robot simulado. **27/27 OK**
  (dead-reckoning, grabador, replay, ejecución de planes, decisión de giro).

### Fase B — IA en la nube
- `webapp/functions/index.js`: Cloud Function `agent` (proxy a OpenAI). El
  `SYSTEM_PROMPT` es copia verbatim de `agent.py`. La API key vive como **secreto de
  Firebase** (`OPENAI_API_KEY`), nunca llega al navegador.
- `webapp/functions/package.json`: dependencias (`firebase-functions`, `openai`).
- Frontend: tarjeta de IA en `index.html`; lee el `say` con `speechSynthesis`.

### Fase C — Datos
- Resuelta con IndexedDB (no se usó Firestore: las rutas son locales del dispositivo).

### Fase D — Hosting y despliegue
- `webapp/firebase.json`: hosting (sitio `cue-and-dash`) + functions + rewrite
  `/agent` → función.
- `webapp/.firebaserc`: proyecto `desarrollo-investigaciones`.
- `webapp/README.md`: guía paso a paso de despliegue.

### Interfaz de usuario
- `webapp/public/index.html`: panel completo (conexión, conducir con D-pad/WASD,
  luces, cabeza, explorador, anti-choque, grabar/reproducir rutas, IA, telemetría).

## 3. Ajustes posteriores al primer despliegue

- **Node.js 20 → 22:** se actualizó `functions/package.json` (engines `22`,
  `firebase-functions@^6`) y `firebase.json` (`runtime: nodejs22`), porque Node 20
  quedaba deprecado. Función redesplegada y probada.
- **Mensaje según dispositivo:** el aviso de "no soportado" ahora distingue
  iPhone/iPad, Firefox y otros navegadores.
- **`.gitignore`:** se ignora el caché `.firebase/` y `node_modules`.

## 4. Estado del despliegue (verificado en vivo)

| Componente | Estado |
|---|---|
| Control (conducir, luces, cabeza) | ✅ desplegado |
| Explorador autónomo + anti-choque | ✅ desplegado |
| Grabar/reproducir rutas (IndexedDB) | ✅ desplegado |
| IA (Cloud Function `agent` + OpenAI, Node 22) | ✅ desplegado y probado |
| Otras 4 funciones del proyecto Firebase | ✅ intactas (no se tocaron) |

Prueba end-to-end real (POST a `/agent`):
```
"ponte azul y avanza 20 cm"
→ {"ok":true,"steps":[{"action":"lights","color":"blue"},{"action":"forward","cm":20}],
   "say":"¡Listo, me pongo azul y avanzo un poquito!"}
```

## 5. Pasos de Firebase ejecutados (en orden)

1. `firebase projects:list` — listar proyectos.
2. (Se canceló un `firebase init` en la raíz; se usó el setup ya hecho en `webapp/`.)
3. `firebase hosting:sites:list` — se confirmó que el sitio `cue-and-dash` está en el
   proyecto `desarrollo-investigaciones`.
4. `firebase deploy --only hosting` — subió el panel a `cue-and-dash.web.app`.
5. `firebase functions:secrets:set OPENAI_API_KEY` — guardó la key de OpenAI como
   secreto (lo hizo el usuario; la key no pasó por el chat).
6. `firebase deploy --only functions:agent` — desplegó SOLO la función `agent`
   (con `:agent` para no borrar las otras 4 funciones del proyecto).
7. `firebase deploy --only hosting` — re-deploy para conectar el rewrite `/agent`.
8. Verificación con `curl` al endpoint → respuesta correcta de la IA.
9. Upgrade a Node 22 y re-deploy de la función + hosting.

### Comandos clave para recordar
```powershell
cd webapp
firebase deploy --only hosting                  # actualizar el panel
firebase deploy --only functions:agent          # actualizar SOLO la IA
firebase functions:secrets:set OPENAI_API_KEY   # cambiar la key de OpenAI
```
> Importante: usar siempre `functions:agent` (con el nombre) para **no** borrar las
> otras funciones (`api`, `chatEvaluationGPT`, `chatProposalGPT`, `processPDFGPT`)
> que viven en el mismo proyecto Firebase.

## 6. Pendiente

- **Subir la rama `despliegue` a GitHub:** el push debe hacerlo el usuario, porque las
  credenciales de la máquina (`Diegojedap`) no tienen permiso de escritura sobre el
  repo de `DiegoWhite99` (error 403). Comando: `git push -u origin despliegue`.
- **IA:** requiere plan Blaze (ya activo) por las llamadas a OpenAI.
- **Prueba con robot físico** en Chrome (no se pudo hacer sin el robot/BLE en este
  entorno).
