// routes-db.js — Almacén de rutas en IndexedDB (reemplaza el SQLite de robot_core.py).
//
// Una ruta = { name, robot_type, created_at, duration_ms, steps:[[t_ms, action, params]] }.
// Funciona 100% local en el navegador, sin servidor ni Firestore (offline-first).

const DB_NAME = 'wonder_routes';
const STORE = 'routes';
const DB_VERSION = 1;

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db, mode) {
  return db.transaction(STORE, mode).objectStore(STORE);
}

export async function saveRoute(name, robotType, steps, durationMs) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const store = tx(db, 'readwrite');
    const rec = {
      name,
      robot_type: robotType,
      created_at: new Date().toISOString().slice(0, 19),
      duration_ms: Math.round(durationMs),
      steps, // [[t_ms, action, params], ...]
    };
    const req = store.add(rec);
    req.onsuccess = () => resolve(req.result); // id
    req.onerror = () => reject(req.error);
  });
}

export async function listRoutes() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const req = tx(db, 'readonly').getAll();
    req.onsuccess = () => {
      const rows = req.result || [];
      rows.sort((a, b) => b.id - a.id); // más recientes primero
      resolve(rows.map(r => ({
        id: r.id,
        name: r.name,
        created: (r.created_at || '').replace('T', ' '),
        dur_ms: r.duration_ms,
        type: r.robot_type,
        steps: (r.steps || []).length,
      })));
    };
    req.onerror = () => reject(req.error);
  });
}

export async function getRouteSteps(id) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const req = tx(db, 'readonly').get(id);
    req.onsuccess = () => resolve(req.result ? req.result.steps : []);
    req.onerror = () => reject(req.error);
  });
}

export async function deleteRoute(id) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const req = tx(db, 'readwrite').delete(id);
    req.onsuccess = () => resolve(true);
    req.onerror = () => reject(req.error);
  });
}
