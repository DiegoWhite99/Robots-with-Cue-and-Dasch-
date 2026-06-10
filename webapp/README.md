# Web app del robot (Web Bluetooth + Firebase)

Versión de la web app que controla el robot Dash/Cue **directo desde el navegador**
por Web Bluetooth, sin servidor Python. Pensada para desplegarse en Firebase Hosting,
con la IA en Cloud Functions.

> ⚠️ Requiere **Chrome o Edge** (Android, Windows, macOS, Linux) y contexto seguro
> (HTTPS o localhost). **No funciona en iPhone/iPad ni Firefox** (no soportan Web Bluetooth).

## Estructura

```
webapp/
├── public/                 # sitio estático (lo que sirve Firebase Hosting)
│   ├── index.html          # panel de control
│   └── js/
│       ├── protocol.js     # protocolo Wonder Workshop (encode/decode) — byte-a-byte
│       ├── robot.js        # cliente Web Bluetooth (WonderRobot)
│       ├── controller.js   # dead-reckoning, explorador, grabador, replay, agente
│       └── routes-db.js    # rutas en IndexedDB (offline)
├── functions/              # Cloud Functions (agente IA / proxy OpenAI)
│   ├── index.js            # función "agent"
│   └── package.json
├── test/                   # validación (node)
├── firebase.json
└── .firebaserc             # pon aquí tu project id
```

## Probar en local (sin desplegar)

Solo el control del robot (sin IA), sirviendo los archivos estáticos:

```powershell
cd webapp/public
python -m http.server 8000
# abre http://localhost:8000 en Chrome, enciende el robot y pulsa Conectar
```

Con IA incluida, usando el emulador de Firebase:

```powershell
cd webapp
npm --prefix functions install
firebase emulators:start
```

## Validar la lógica (sin robot)

```powershell
node webapp/test/check_js.mjs           # protocolo: 16/16 idéntico a Python
node webapp/test/controller_check.mjs   # alto nivel: 27/27
```

## Desplegar a Firebase

1. Instalar herramientas y entrar:
   ```powershell
   npm install -g firebase-tools
   firebase login
   ```
2. Poner tu project id en `.firebaserc` (campo `default`).
3. Instalar dependencias de la función:
   ```powershell
   npm --prefix functions install
   ```
4. Guardar la API key de OpenAI como secreto (no va al navegador):
   ```powershell
   firebase functions:secrets:set OPENAI_API_KEY
   ```
5. Desplegar:
   ```powershell
   firebase deploy --only hosting,functions
   ```
6. Abrir `https://TU-PROYECTO.web.app` en Chrome/Android, conectar y probar.

## Notas

- El frontend llama a `/agent`, que `firebase.json` reescribe a la Cloud Function.
- Las rutas grabadas se guardan en el navegador (IndexedDB); no se suben a la nube.
- El modelo de OpenAI se puede cambiar con la variable de entorno `OPENAI_MODEL`.
