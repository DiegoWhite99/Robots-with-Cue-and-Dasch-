# -*- coding: utf-8 -*-
"""
robot_core.py  -  Logica compartida para controlar Dash / Dot / Cue.

Sin interfaz grafica ni servidor: aqui vive la conexion Bluetooth (WonderPy),
los comandos al robot, la grabacion de rutas, la base de datos SQLite, el
reproductor de rutas y el interprete de comandos de voz.

Lo usa app_web.py (servidor Flask). Es 100% thread-safe para que el servidor
pueda llamarlo desde varios hilos de peticiones a la vez.
"""

import os
import math
import json
import time
import argparse
import sqlite3
import threading
from datetime import datetime

import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr


def friendly_type(robot):
    names = {"WW_ROBOT_DASH": "Dash", "WW_ROBOT_DOT": "Dot", "WW_ROBOT_CUE": "Cue"}
    return names.get(robot.robot_type_name, robot.robot_type_name)


# Sonido que reproduce el ROBOT (por su parlante) al detectar un obstaculo.
# Son clips que YA viven dentro del robot; aqui solo enviamos su NOMBRE y el
# parlante lo toca. Los de fabrica del Cue son en ingles.
ALERT_SOUNDS = {
    "WW_ROBOT_CUE":  "SNCHWHOA",     # Cue: "Whoa!" (corto)
    "WW_ROBOT_DASH": "SYSTWOAH_NO",  # Dash: "Whoa, no!"
    "WW_ROBOT_DOT":  "SYSTWHOA",     # Dot: "Whoa!"
}

# Para que el robot diga ESPAÑOL por su parlante: graba la frase en la app
# Wonder en un espacio personalizado (slot 1 = "SYSTVOICE0", ... slot 10 =
# "SYSTVOICE9") y pon aqui ese nombre. Si queda en None se usa el clip de fabrica.
ALERT_VOICE_SLOT = None   # p.ej. "SYSTVOICE0" cuando grabes "¡Cuidado!" en el slot 1


# Colores por nombre -> (r, g, b) en 0..1, para el agente.
COLOR_RGB = {
    "red": (1, 0, 0), "rojo": (1, 0, 0),
    "green": (0, 1, 0), "verde": (0, 1, 0),
    "blue": (0, 0.3, 1), "azul": (0, 0.3, 1),
    "yellow": (1, 0.9, 0), "amarillo": (1, 0.9, 0),
    "magenta": (1, 0, 0.7), "morado": (0.6, 0, 1), "purple": (0.6, 0, 1),
    "cyan": (0, 0.9, 1),
    "white": (1, 1, 1), "blanco": (1, 1, 1),
    "off": (0, 0, 0), "apagar": (0, 0, 0), "negro": (0, 0, 0),
}


# Aceleración del robot (cm/s²): subir para que siga la rampa suave del control.
DRIVE_ACCEL_LIN = 120.0   # qué tan ágil acelera al recibir una velocidad
STOP_ACCEL_LIN = 320.0    # frenado rápido al soltar / freno en seco


def _clamp(v, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
#  Base de datos (SQLite)                                                      #
# --------------------------------------------------------------------------- #
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rutas.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    robot_type  TEXT,
    created_at  TEXT    NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    notes       TEXT    DEFAULT ''
);
CREATE TABLE IF NOT EXISTS steps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    route_id    INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    t_offset_ms INTEGER NOT NULL,
    action      TEXT    NOT NULL,
    params      TEXT    NOT NULL,
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_steps_route ON steps(route_id, seq);
"""


def db_connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def db_init():
    con = db_connect()
    con.executescript(SCHEMA)
    con.commit()
    con.close()


def db_save_route(name, robot_type, events, duration_ms, notes=""):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO routes(name, robot_type, created_at, duration_ms, notes) "
        "VALUES (?,?,?,?,?)",
        (name, robot_type, datetime.now().isoformat(timespec="seconds"),
         int(duration_ms), notes),
    )
    rid = cur.lastrowid
    for seq, (t, action, params) in enumerate(events):
        cur.execute(
            "INSERT INTO steps(route_id, seq, t_offset_ms, action, params) "
            "VALUES (?,?,?,?,?)",
            (rid, seq, int(t), action, json.dumps(params)),
        )
    con.commit()
    con.close()
    return rid


def db_list_routes():
    con = db_connect()
    rows = con.execute(
        "SELECT r.id, r.name, r.created_at, r.duration_ms, r.robot_type, "
        "       (SELECT COUNT(*) FROM steps s WHERE s.route_id = r.id) AS n "
        "FROM routes r ORDER BY r.id DESC"
    ).fetchall()
    con.close()
    return rows


def db_get_steps(route_id):
    con = db_connect()
    rows = con.execute(
        "SELECT t_offset_ms, action, params FROM steps "
        "WHERE route_id = ? ORDER BY seq",
        (route_id,),
    ).fetchall()
    con.close()
    return [(t, action, json.loads(p)) for (t, action, p) in rows]


def db_delete_route(route_id):
    con = db_connect()
    con.execute("DELETE FROM routes WHERE id = ?", (route_id,))
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
#  Grabador de ruta (thread-safe)                                              #
# --------------------------------------------------------------------------- #
class Recorder:
    def __init__(self):
        self._lock = threading.Lock()
        self.recording = False
        self._t0 = None
        self.events = []

    def start(self):
        with self._lock:
            self.events = []
            self._t0 = time.monotonic()
            self.recording = True

    def stop(self):
        with self._lock:
            self.recording = False
            return list(self.events), self._duration_ms()

    def _duration_ms(self):
        return self.events[-1][0] if self.events else 0

    def elapsed_ms(self):
        return 0 if self._t0 is None else int((time.monotonic() - self._t0) * 1000)

    def step_count(self):
        return len(self.events)

    def add(self, action, params):
        with self._lock:
            if not self.recording:
                return
            t = int((time.monotonic() - self._t0) * 1000)
            self.events.append((t, action, params))


# --------------------------------------------------------------------------- #
#  Puente con el robot                                                         #
# --------------------------------------------------------------------------- #
class RobotBridge:
    def __init__(self, recorder):
        self.robot = None
        self.recorder = recorder
        self.on_connect_cb = None
        self.sensors_cb = None          # lo usa el Controller para leer sensores
        self._last_vel = None
        self._last_head = None
        self._last_light = None

    # delegate de WonderPy
    def on_connect(self, robot):
        self.robot = robot
        if self.on_connect_cb:
            self.on_connect_cb(robot)

    def on_sensors(self, robot):
        # se llama en CADA paquete de sensores (hilo BLE). Debe ser ligero y
        # nunca bloquear (no llamar do_* aqui).
        if self.sensors_cb:
            try:
                self.sensors_cb(robot)
            except Exception as e:
                print("[sensors] error:", e)

    @property
    def connected(self):
        return self.robot is not None

    @property
    def robot_label(self):
        return "%s '%s'" % (friendly_type(self.robot), self.robot.name) if self.robot else "?"

    @property
    def robot_type_name(self):
        return self.robot.robot_type_name if self.robot else "?"

    def drive(self, linear_cm_s, angular_deg_s, record=True):
        r = self.robot
        if r is None:
            return
        vel = (round(linear_cm_s, 2), round(angular_deg_s, 2))
        if vel == self._last_vel:
            return
        self._last_vel = vel
        r.cmds.body.stage_linear_angular(linear_cm_s, angular_deg_s, DRIVE_ACCEL_LIN)
        r.send_staged()
        if record:
            self.recorder.add("drive", {"linear": vel[0], "angular": vel[1]})

    def stop(self, record=True):
        r = self.robot
        if r is None:
            return
        if self._last_vel == (0.0, 0.0):
            return
        self._last_vel = (0.0, 0.0)
        r.cmds.body.stage_stop(STOP_ACCEL_LIN)
        r.send_staged()
        if record:
            self.recorder.add("stop", {})

    def head(self, pan, tilt, record=True):
        r = self.robot
        if r is None:
            return
        pan = max(r.head_pan_min_deg, min(r.head_pan_max_deg, float(pan)))
        tilt = max(r.head_tilt_min_deg, min(r.head_tilt_max_deg, float(tilt)))
        key = (round(pan, 1), round(tilt, 1))
        if key == self._last_head:
            return
        self._last_head = key
        r.cmds.head.stage_pan_tilt_angle(pan, tilt)
        r.send_staged()
        if record:
            self.recorder.add("head", {"pan": key[0], "tilt": key[1]})

    def lights(self, rr, gg, bb, record=True):
        r = self.robot
        if r is None:
            return
        key = (round(rr, 3), round(gg, 3), round(bb, 3))
        if key == self._last_light:
            return
        self._last_light = key
        r.cmds.RGB.stage_all(rr, gg, bb)
        r.send_staged()
        if record:
            self.recorder.add("lights", {"r": key[0], "g": key[1], "b": key[2]})

    def play_sound(self, name, volume=1.0):
        """Reproduce un sonido por el PARLANTE del robot (no bloquea)."""
        r = self.robot
        if r is None or not name:
            return
        try:
            r.cmds.media.stage_audio(name, volume)
            r.send_staged()
        except Exception as e:
            print("[sound] error:", e)


# --------------------------------------------------------------------------- #
#  Movimientos por rafagas (para voz / comandos discretos)                     #
# --------------------------------------------------------------------------- #
class MotionRunner:
    def __init__(self, bridge):
        self.bridge = bridge
        self._cancel = threading.Event()

    def _spawn(self, fn):
        self._cancel.set()
        self._cancel = threading.Event()
        ev = self._cancel
        threading.Thread(target=lambda: fn(ev), daemon=True).start()

    def move(self, linear, angular, duration):
        def work(ev):
            self.bridge.drive(linear, angular)
            t0 = time.monotonic()
            while time.monotonic() - t0 < duration:
                if ev.is_set():
                    return
                time.sleep(0.02)
            self.bridge.stop()
        self._spawn(work)

    def stop_now(self):
        self._cancel.set()
        self.bridge.stop()

    def avoid(self, turn_left=True, escape_back=True):
        """Maniobra de esquive: frena, se aleja del obstaculo y gira al lado despejado.
        escape_back=True  -> obstaculo AL FRENTE: retrocede.
        escape_back=False -> obstaculo ATRAS:     avanza."""
        away = -20 if escape_back else 20
        def work(ev):
            self.bridge.stop()
            if ev.is_set():
                return
            self.bridge.drive(away, 0)          # alejarse del obstaculo
            t0 = time.monotonic()
            while time.monotonic() - t0 < 0.5:
                if ev.is_set():
                    return
                time.sleep(0.02)
            self.bridge.drive(0, 120 if turn_left else -120)   # girar
            t0 = time.monotonic()
            while time.monotonic() - t0 < 0.7:
                if ev.is_set():
                    return
                time.sleep(0.02)
            self.bridge.stop()
        self._spawn(work)

    def dance(self):
        colors = [(1, 0, 0), (1, 0.6, 0), (1, 1, 0), (0, 1, 0), (0, 0.4, 1), (0.6, 0, 1)]

        def work(ev):
            for i in range(6):
                if ev.is_set():
                    return
                self.bridge.lights(*colors[i % len(colors)])
                self.bridge.drive(0, 180 if i % 2 == 0 else -180)
                time.sleep(0.45)
            self.bridge.stop()
            self.bridge.lights(0, 0, 0)
        self._spawn(work)


# --------------------------------------------------------------------------- #
#  Comandos de voz                                                             #
# --------------------------------------------------------------------------- #
def parse_voice_command(text):
    """Devuelve ('kind', payload) o None. Las DIRECCIONES van antes que "para"
    (detener), porque "para atras"/"para adelante" indican direccion."""
    t = " " + (text or "").lower().strip() + " "

    if any(w in t for w in [" atras", "atrás", "reversa", "retrocede", "patras",
                            "pa atras", "devuelvete", "devuélvete", "echa pa"]):
        return ("back", None)
    if "derecha" in t:
        return ("right", None)
    if "izquierda" in t:
        return ("left", None)
    if any(w in t for w in ["adelante", "avanza", "camina", "sigue", "palante"]):
        return ("fwd", None)
    if any(w in t for w in [" para ", " para", "alto", "deten", "stop",
                            "quieto", "frena", "parate", "párate"]):
        return ("stop", None)

    if "roj" in t:
        return ("light", (1.0, 0.0, 0.0))
    if "verde" in t:
        return ("light", (0.0, 1.0, 0.0))
    if "azul" in t:
        return ("light", (0.0, 0.3, 1.0))
    if "amarill" in t:
        return ("light", (1.0, 0.9, 0.0))
    if "blanc" in t:
        return ("light", (1.0, 1.0, 1.0))
    if any(w in t for w in ["apag", "sin luz", "oscuro"]):
        return ("light", (0.0, 0.0, 0.0))

    if "arriba" in t:
        return ("head_up", None)
    if "abajo" in t:
        return ("head_down", None)
    if any(w in t for w in ["centra", "centro", "centrar"]):
        return ("head_center", None)
    if any(w in t for w in ["baila", "celebra", "fiesta"]):
        return ("dance", None)
    return None


# --------------------------------------------------------------------------- #
#  Reproductor de ruta                                                         #
# --------------------------------------------------------------------------- #
def replay_route(bridge, steps, stop_event, progress_cb):
    start = time.monotonic()
    total = len(steps)
    for i, (t_off, action, params) in enumerate(steps):
        while not stop_event.is_set():
            if (time.monotonic() - start) * 1000.0 >= t_off:
                break
            time.sleep(0.005)
        if stop_event.is_set():
            break
        if action == "drive":
            bridge.drive(params["linear"], params["angular"], record=False)
        elif action == "stop":
            bridge.stop(record=False)
        elif action == "head":
            bridge.head(params["pan"], params["tilt"], record=False)
        elif action == "lights":
            bridge.lights(params["r"], params["g"], params["b"], record=False)
        progress_cb(i + 1, total)
    bridge.stop(record=False)


# --------------------------------------------------------------------------- #
#  Controlador de alto nivel (lo usa el servidor)                              #
# --------------------------------------------------------------------------- #
class Controller:
    DIST_CM = 25.0
    TURN_DEG = 90.0
    OBSTACLE_CM = 20.0     # umbral: obstaculo "cerca" si la distancia frontal es menor
    AVOID_COOLDOWN = 2.5   # segundos entre maniobras de esquive
    EXPLORE_PROFILES = {
        "suave": {
            "speed": 18.0,
            "warn_cm": 50.0,
            "min_speed": 8.0,
            "stuck_s": 4.6,
            "critical_pad": 5.0,
            "visit_soft": 4,
            "visit_hard": 9,
            "turn_speed": 118.0,
            "turn_hold": 0.74,
            "escape_front_cm": 16.0,
            "escape_rear_cm": 16.0,
            "loop_escape_extra_cm": 4.0,
            "loop_turn_extra": 18.0,
            "loop_turn_hold_extra": 0.20,
            "stuck_escape_cm": 18.0,
            "stuck_turn_speed": 132.0,
            "stuck_turn_hold": 0.85,
            "improve_delta": 5.0,
            "improve_safe_cm": 26.0,
            "bias_gain": 0.18,
            "bias_penalty": 0.22,
            "tick_s": 0.28,
        },
        "normal": {
            "speed": 25.0,
            "warn_cm": 42.0,
            "min_speed": 10.0,
            "stuck_s": 3.2,
            "critical_pad": 6.0,
            "visit_soft": 4,
            "visit_hard": 8,
            "turn_speed": 120.0,
            "turn_hold": 0.70,
            "escape_front_cm": 18.0,
            "escape_rear_cm": 18.0,
            "loop_escape_extra_cm": 6.0,
            "loop_turn_extra": 25.0,
            "loop_turn_hold_extra": 0.30,
            "stuck_escape_cm": 20.0,
            "stuck_turn_speed": 150.0,
            "stuck_turn_hold": 1.05,
            "improve_delta": 6.0,
            "improve_safe_cm": 30.0,
            "bias_gain": 0.24,
            "bias_penalty": 0.30,
            "tick_s": 0.25,
        },
        "agresivo": {
            "speed": 34.0,
            "warn_cm": 36.0,
            "min_speed": 12.0,
            "stuck_s": 2.2,
            "critical_pad": 7.0,
            "visit_soft": 3,
            "visit_hard": 7,
            "turn_speed": 132.0,
            "turn_hold": 0.78,
            "escape_front_cm": 22.0,
            "escape_rear_cm": 22.0,
            "loop_escape_extra_cm": 8.0,
            "loop_turn_extra": 35.0,
            "loop_turn_hold_extra": 0.36,
            "stuck_escape_cm": 24.0,
            "stuck_turn_speed": 165.0,
            "stuck_turn_hold": 1.16,
            "improve_delta": 7.0,
            "improve_safe_cm": 32.0,
            "bias_gain": 0.30,
            "bias_penalty": 0.38,
            "tick_s": 0.22,
        },
    }

    def __init__(self, connect_args):
        self.connect_args = connect_args
        # tipo de robot preferido (dash/cue/dot/any) derivado de connect_args
        self.robot_type_pref = "any"
        if "--connect-type" in connect_args:
            i = connect_args.index("--connect-type")
            if i + 1 < len(connect_args):
                self.robot_type_pref = connect_args[i + 1]
        self.recorder = Recorder()
        self.bridge = RobotBridge(self.recorder)
        self.motion = MotionRunner(self.bridge)
        self.bridge.on_connect_cb = self._on_connect
        self.bridge.sensors_cb = self._on_sensors

        self.state = "idle"            # idle | searching | connected
        self.was_connected = False
        self._search_t0 = None
        self._ble_thread = None

        self._replay_stop = threading.Event()
        self.replay = {"playing": False, "i": 0, "total": 0, "route_id": None}

        # agente de IA
        self._agent_stop = threading.Event()
        self.agent_busy = False
        self.agent_last = ""

        # sensores / evitar obstaculos
        self.avoid_enabled = False
        self.avoiding = False            # True mientras hace el freno+esquive (bloquea control manual)
        self.explore_enabled = False     # modo explorador autonomo
        self.explore_profile = "normal"  # suave | normal | agresivo
        self._last_sensor = 0.0          # ultimo paquete de sensores (para detectar 'sin señal')
        self.last_distance = None        # frente
        self.last_distance_rear = None   # atras
        self._avoid_cooldown = 0.0
        self._alert_seq = 0
        self.alert = {"seq": 0, "msg": "", "distance": None}
        # estado del explorador con aprendizaje (anti-bucle)
        self._explore_lock = threading.Lock()
        self.explore_ai = {
            "started_at": None,
            "updated_at": None,
            "escapes": 0,
            "loops_avoided": 0,
            "stuck_events": 0,
            "turn_bias": 0.0,        # +izq / -der según resultados históricos
            "turn_left": 0,
            "turn_right": 0,
            "last_reason": "",
            "last_decision": "",
            "last_improved": None,
            "last_before_cm": None,
            "last_after_cm": None,
            "recent": [],
        }
        self._exp_last_progress_t = 0.0
        self._exp_last_pose = None

        # recorrido / mapa (dead-reckoning: rumbo del GIROSCOPIO + distancia de ENCODERS)
        self._track_lock = threading.Lock()
        self.track = []                  # [(x,y), ...] traza ordenada (cm)
        self._last_track_pt = None
        self.pose = {"x": 0.0, "y": 0.0, "deg": 0.0, "valid": False}
        self.heat = {}                   # {(ix,iy): conteo} mapa de calor por tiempo
        self.HEAT_BIN = 5.0              # cm por celda del mapa de calor
        self._dr_x = 0.0                 # posicion integrada por nosotros
        self._dr_y = 0.0
        self._prev_encL = None           # ultima lectura de encoders (para deltas)
        self._prev_encR = None
        self._deg0 = None                # rumbo de referencia (al limpiar/empezar)

    # ---- sensores (se llama en cada paquete, hilo BLE) ----
    def _on_sensors(self, robot):
        s = robot.sensors
        self._last_sensor = time.monotonic()   # llegó señal del robot

        # --- recorrido por dead-reckoning (rumbo del giroscopio + encoders) ---
        try:
            p = s.pose
            wl = s.wheel_left
            wr = s.wheel_right
            if (p is not None and p.valid and wl.valid and wr.valid):
                el = float(wl.distance)
                er = float(wr.distance)
                deg = float(p.degrees)                 # rumbo (giroscopio, alta frecuencia)
                if self._deg0 is None:
                    self._deg0 = deg
                if self._prev_encL is None:
                    self._prev_encL, self._prev_encR = el, er
                # distancia recorrida desde el ultimo paquete (promedio de ambas ruedas)
                dC = ((el - self._prev_encL) + (er - self._prev_encR)) / 2.0
                self._prev_encL, self._prev_encR = el, er
                heading = math.radians(deg - self._deg0)
                if abs(dC) < 50:                       # descarta saltos raros (ruido/wrap)
                    self._dr_x += dC * math.cos(heading)
                    self._dr_y += dC * math.sin(heading)
                x, y = self._dr_x, self._dr_y
                with self._track_lock:
                    self.pose = {"x": round(x, 1), "y": round(y, 1),
                                 "deg": round(deg - self._deg0, 1), "valid": True}
                    lp = self._last_track_pt
                    if lp is None or (abs(x - lp[0]) + abs(y - lp[1])) >= 1.5:
                        self.track.append((round(x, 1), round(y, 1)))
                        self._last_track_pt = (x, y)
                        if len(self.track) > 2000:
                            del self.track[:len(self.track) - 2000]
                    cell = (int(x // self.HEAT_BIN), int(y // self.HEAT_BIN))
                    if cell in self.heat or len(self.heat) < 5000:
                        self.heat[cell] = self.heat.get(cell, 0) + 1
        except Exception:
            pass

        # --- distancias / evitar obstaculos ---
        dleft = dright = front = rear = None
        try:
            if s.distance_front_left_facing.valid:
                dleft = s.distance_front_left_facing.distance_approximate
            if s.distance_front_right_facing.valid:
                dright = s.distance_front_right_facing.distance_approximate
            if s.distance_rear.valid:
                rear = s.distance_rear.distance_approximate
        except Exception:
            return
        fronts = [d for d in (dleft, dright) if d is not None]
        if fronts:
            front = min(fronts)
            self.last_distance = round(front, 1)
        if rear is not None:
            self.last_distance_rear = round(rear, 1)

        if not self.avoid_enabled or self.replay["playing"] or self.agent_busy:
            return
        if self.avoiding:                 # ya está frenando/esquivando: no re-disparar
            return
        now = time.monotonic()
        if (now - self._avoid_cooldown) <= self.AVOID_COOLDOWN:
            return

        # elegir la amenaza mas cercana (frente o atras) bajo el umbral
        threat = None   # ("front"/"rear", distancia)
        if front is not None and front < self.OBSTACLE_CM:
            threat = ("front", front)
        if rear is not None and rear < self.OBSTACLE_CM:
            if threat is None or rear < threat[1]:
                threat = ("rear", rear)
        if threat is None:
            return

        side, dist = threat
        self._avoid_cooldown = now
        self.avoiding = True              # bloquea el control manual durante el esquive
        # el PARLANTE del robot se activa
        sound = ALERT_VOICE_SLOT or ALERT_SOUNDS.get(self.bridge.robot_type_name, "SNCHWHOA")
        self.bridge.play_sound(sound)
        # aviso para el dispositivo
        donde = "al frente" if side == "front" else "atrás"
        self._alert_seq += 1
        self.alert = {
            "seq": self._alert_seq,
            "msg": "¡Obstáculo %s a %d cm! Freno en seco y ajusto." % (donde, int(dist)),
            "distance": int(dist),
            "side": side,
        }
        # freno forzado + maniobra de escape (en su propio hilo)
        if self.explore_enabled:
            reason = "obstaculo_%s" % side
            threading.Thread(
                target=self._do_escape_learning,
                args=(side, dleft, dright, reason),
                daemon=True,
            ).start()
        else:
            turn_left = (dleft if dleft is not None else 0) >= (dright if dright is not None else 0)
            threading.Thread(target=self._do_escape, args=(side, turn_left), daemon=True).start()

    def _explore_note(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        with self._explore_lock:
            rec = list(self.explore_ai.get("recent", []))
            rec.append("%s · %s" % (ts, msg))
            self.explore_ai["recent"] = rec[-20:]
            self.explore_ai["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def _pose_cell(self):
        with self._track_lock:
            p = dict(self.pose)
            if not p.get("valid"):
                return None, 0
            x = float(p.get("x", 0.0))
            y = float(p.get("y", 0.0))
            cell = (int(x // self.HEAT_BIN), int(y // self.HEAT_BIN))
            visits = int(self.heat.get(cell, 0))
        return cell, visits

    def _predict_cell_visits(self, turn_left):
        with self._track_lock:
            p = dict(self.pose)
            if not p.get("valid"):
                return 0
            heading = math.radians(float(p.get("deg", 0.0)) + (42.0 if turn_left else -42.0))
            x = float(p.get("x", 0.0)) + math.cos(heading) * 26.0
            y = float(p.get("y", 0.0)) + math.sin(heading) * 26.0
            cell = (int(x // self.HEAT_BIN), int(y // self.HEAT_BIN))
            return int(self.heat.get(cell, 0))

    def _choose_explore_turn(self, dleft, dright):
        left_clear = float(dleft if dleft is not None else (self.last_distance or 30.0))
        right_clear = float(dright if dright is not None else (self.last_distance or 30.0))
        left_vis = self._predict_cell_visits(True)
        right_vis = self._predict_cell_visits(False)
        with self._explore_lock:
            bias = float(self.explore_ai.get("turn_bias", 0.0))
        left_score = left_clear - (left_vis * 1.4) + (bias * 4.0)
        right_score = right_clear - (right_vis * 1.4) - (bias * 4.0)
        return (left_score >= right_score), {
            "left_score": round(left_score, 2),
            "right_score": round(right_score, 2),
            "left_visits": left_vis,
            "right_visits": right_vis,
        }

    def _register_explore_escape(self, reason, turn_left, improved, before_cm, after_cm, extra):
        cell, visits = self._pose_cell()
        cfg = self._explore_cfg()
        with self._explore_lock:
            self.explore_ai["escapes"] = int(self.explore_ai.get("escapes", 0)) + 1
            if reason == "stuck":
                self.explore_ai["stuck_events"] = int(self.explore_ai.get("stuck_events", 0)) + 1
            if visits >= int(cfg.get("visit_hard", 8)):
                self.explore_ai["loops_avoided"] = int(self.explore_ai.get("loops_avoided", 0)) + 1
            if turn_left:
                self.explore_ai["turn_left"] = int(self.explore_ai.get("turn_left", 0)) + 1
            else:
                self.explore_ai["turn_right"] = int(self.explore_ai.get("turn_right", 0)) + 1
            gain = float(cfg.get("bias_gain", 0.24))
            penalty = float(cfg.get("bias_penalty", 0.30))
            delta = gain if improved else -penalty
            bias = float(self.explore_ai.get("turn_bias", 0.0)) + (delta if turn_left else -delta)
            self.explore_ai["turn_bias"] = max(-3.0, min(3.0, bias))
            self.explore_ai["last_reason"] = reason
            self.explore_ai["last_decision"] = "left" if turn_left else "right"
            self.explore_ai["last_improved"] = bool(improved)
            self.explore_ai["last_before_cm"] = None if before_cm is None else round(float(before_cm), 1)
            self.explore_ai["last_after_cm"] = None if after_cm is None else round(float(after_cm), 1)
        cell_txt = ("celda %s visitas=%d" % (cell, visits)) if cell else "celda sin pose"
        msg = "escape %s -> %s (%s), %s" % (
            reason,
            "izq" if turn_left else "der",
            "mejoró" if improved else "sin mejora",
            cell_txt,
        )
        if extra:
            msg += " [L%s/R%s]" % (extra.get("left_visits", 0), extra.get("right_visits", 0))
        self._explore_note(msg)

    def explore_snapshot(self):
        with self._explore_lock:
            snap = dict(self.explore_ai)
            snap["recent"] = list(self.explore_ai.get("recent", []))
        snap["running"] = bool(self.explore_enabled)
        snap["profile"] = self.explore_profile
        cell, visits = self._pose_cell()
        snap["cell"] = {"ix": cell[0], "iy": cell[1], "visits": visits} if cell else None
        return snap

    def reset_explore_learning(self, clear_map=False):
        with self._explore_lock:
            started = self.explore_ai.get("started_at")
            self.explore_ai = {
                "started_at": started if self.explore_enabled else None,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "escapes": 0,
                "loops_avoided": 0,
                "stuck_events": 0,
                "turn_bias": 0.0,
                "turn_left": 0,
                "turn_right": 0,
                "last_reason": "",
                "last_decision": "",
                "last_improved": None,
                "last_before_cm": None,
                "last_after_cm": None,
                "recent": [],
            }
        self._exp_last_progress_t = time.monotonic()
        self._exp_last_pose = None
        if clear_map:
            self.clear_track()
            self._explore_note("aprendizaje reiniciado y mapa limpiado.")
        else:
            self._explore_note("aprendizaje reiniciado.")
        return self.explore_snapshot()

    def set_avoid(self, on):
        self.avoid_enabled = bool(on)
        return self.avoid_enabled

    # ---- modo explorador autonomo ----
    def _explore_cfg(self):
        cfg = self.EXPLORE_PROFILES.get(self.explore_profile)
        if cfg is None:
            cfg = self.EXPLORE_PROFILES["normal"]
        return dict(cfg)

    def set_explore_profile(self, mode):
        m = (mode or "").strip().lower()
        aliases = {
            "suave": "suave", "soft": "suave", "safe": "suave",
            "normal": "normal", "medio": "normal", "balanced": "normal",
            "agresivo": "agresivo", "agresiva": "agresivo", "aggressive": "agresivo",
        }
        chosen = aliases.get(m)
        if chosen is None:
            return self.explore_profile
        self.explore_profile = chosen
        self._explore_note("perfil de exploración: %s." % chosen)
        return self.explore_profile

    def set_explore(self, on):
        on = bool(on)
        if on and not self.explore_enabled:
            self.explore_enabled = True
            self.avoid_enabled = True       # explorar SIEMPRE con anti-choque
            self._exp_last_progress_t = time.monotonic()
            self._exp_last_pose = None
            with self._explore_lock:
                self.explore_ai["started_at"] = datetime.now().isoformat(timespec="seconds")
                self.explore_ai["updated_at"] = self.explore_ai["started_at"]
            self._explore_note("explorador inteligente activado (%s)." % self.explore_profile)
            threading.Thread(target=self._explore_loop, daemon=True).start()
        elif not on:
            self.explore_enabled = False
            self.bridge.stop(record=False)
            self._explore_note("explorador detenido.")
        return self.explore_enabled

    def _explore_loop(self):
        def update_progress(now_s):
            with self._track_lock:
                p = dict(self.pose)
            if not p.get("valid"):
                return False
            cur = (float(p.get("x", 0.0)), float(p.get("y", 0.0)))
            if self._exp_last_pose is None:
                self._exp_last_pose = cur
                self._exp_last_progress_t = now_s
                return True
            moved = abs(cur[0] - self._exp_last_pose[0]) + abs(cur[1] - self._exp_last_pose[1])
            self._exp_last_pose = cur
            if moved >= 1.2:
                self._exp_last_progress_t = now_s
                return True
            return False

        while self.explore_enabled and self.bridge.connected:
            cfg = self._explore_cfg()
            if self.replay["playing"] or self.agent_busy:
                time.sleep(0.18)
                continue
            if self.avoiding:
                time.sleep(0.12)
                continue

            now = time.monotonic()
            update_progress(now)
            if now - self._exp_last_progress_t > float(cfg.get("stuck_s", 3.2)):
                self.avoiding = True
                threading.Thread(
                    target=self._do_escape_learning,
                    args=("front", None, None, "stuck"),
                    daemon=True,
                ).start()
                time.sleep(0.15)
                continue

            front = self.last_distance if self.last_distance is not None else 100.0
            if front <= (self.OBSTACLE_CM + float(cfg.get("critical_pad", 6.0))):
                self.avoiding = True
                threading.Thread(
                    target=self._do_escape_learning,
                    args=("front", None, None, "frente_critico"),
                    daemon=True,
                ).start()
                time.sleep(0.15)
                continue

            cell, visits = self._pose_cell()
            speed = float(cfg.get("speed", 25.0))
            warn_cm = float(cfg.get("warn_cm", 42.0))
            min_speed = float(cfg.get("min_speed", 10.0))
            visit_soft = int(cfg.get("visit_soft", 4))
            visit_hard = int(cfg.get("visit_hard", 8))
            if front < warn_cm:
                speed = min(speed, max(min_speed, front * 0.65))
            if visits > visit_soft:
                # si está "pisando" mucho la misma zona, baja velocidad para decidir mejor.
                speed = max(min_speed, speed - min(12.0, (visits - visit_soft) * 1.8))
            if cell is not None and visits >= visit_hard:
                self._explore_note("zona muy repetida %s (visitas=%d), buscando salida." % (cell, visits))

            self.bridge.drive(speed, 0, record=False)
            time.sleep(float(cfg.get("tick_s", 0.25)))
        self.bridge.stop(record=False)

    def _do_escape(self, side, turn_left):
        """Freno EN SECO forzado y maniobra para salir. Mientras corre, self.avoiding
        bloquea el control manual (el servidor ignora /api/drive) para que no lo pise."""
        def hold(seconds):
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds:
                time.sleep(0.03)
        try:
            # 1) FRENO EN SECO (forzado: lo mando dos veces para asegurar el parado)
            self.bridge.stop(record=False)
            hold(0.30)
            self.bridge.stop(record=False)
            hold(0.10)
            # 2) alejarse del obstáculo (atrás si está al frente; adelante si está atrás)
            away = -18 if side == "front" else 18
            self.bridge.drive(away, 0, record=False)
            hold(0.5)
            # 3) girar hacia el lado despejado
            self.bridge.drive(0, 120 if turn_left else -120, record=False)
            hold(0.7)
            self.bridge.stop(record=False)
        except Exception as e:
            print("[avoid] error en escape:", e)
        finally:
            self.avoiding = False         # libera el control manual

    def _do_escape_learning(self, side, dleft, dright, reason):
        """Escape adaptativo en modo explorador: usa sensores + mapa de calor para evitar bucles."""
        def hold(seconds):
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds:
                time.sleep(0.03)

        cfg = self._explore_cfg()
        before = self.last_distance
        turn_left, extra = self._choose_explore_turn(dleft, dright)
        cell, visits = self._pose_cell()
        base_front = float(cfg.get("escape_front_cm", 18.0))
        base_rear = float(cfg.get("escape_rear_cm", 18.0))
        escape_cm = -base_front if side == "front" else base_rear
        turn_speed = float(cfg.get("turn_speed", 120.0))
        turn_hold = float(cfg.get("turn_hold", 0.70))
        if visits >= int(cfg.get("visit_hard", 8)):
            # celda muy visitada => maniobra más agresiva para romper el bucle
            extra_cm = float(cfg.get("loop_escape_extra_cm", 6.0))
            escape_cm = (escape_cm - extra_cm) if side == "front" else (escape_cm + extra_cm)
            turn_speed += float(cfg.get("loop_turn_extra", 25.0))
            turn_hold += float(cfg.get("loop_turn_hold_extra", 0.30))
        if reason == "stuck":
            escape_cm = -float(cfg.get("stuck_escape_cm", 20.0))
            turn_speed = float(cfg.get("stuck_turn_speed", 150.0))
            turn_hold = float(cfg.get("stuck_turn_hold", 1.05))

        try:
            self.bridge.stop(record=False)
            hold(0.28)
            self.bridge.stop(record=False)
            hold(0.10)
            self.bridge.drive(escape_cm, 0, record=False)
            hold(0.55 if abs(escape_cm) < 22 else 0.72)
            self.bridge.drive(0, turn_speed if turn_left else -turn_speed, record=False)
            hold(turn_hold)
            self.bridge.stop(record=False)
            hold(0.10)
            after = self.last_distance
            improved = False
            improve_delta = float(cfg.get("improve_delta", 6.0))
            improve_safe_cm = float(cfg.get("improve_safe_cm", 30.0))
            if after is not None:
                if before is None:
                    improved = after > improve_safe_cm
                else:
                    improved = (after > (before + improve_delta)) or (after > improve_safe_cm)
            self._register_explore_escape(reason, turn_left, improved, before, after, extra)
        except Exception as e:
            print("[explore] error en escape adaptativo:", e)
            self._register_explore_escape(reason, turn_left, False, before, self.last_distance, extra)
        finally:
            self.avoiding = False

    # ---- recorrido / mapa ----
    def track_snapshot(self):
        """Copia segura de la traza + mapa de calor + pose actual."""
        with self._track_lock:
            trail = list(self.track)
            heat = [[ix, iy, c] for (ix, iy), c in self.heat.items()]
            pose = dict(self.pose)
        return {"trail": trail, "heat": heat, "bin": self.HEAT_BIN, "current": pose}

    def clear_track(self):
        with self._track_lock:
            self.track = []
            self._last_track_pt = None
            self.heat = {}
            self._dr_x = 0.0
            self._dr_y = 0.0
            self._prev_encL = None
            self._prev_encR = None
            self._deg0 = None       # nuevo origen y rumbo de referencia aquí

    # ---- Bluetooth ----
    def _on_connect(self, robot):
        self.state = "connected"
        self.was_connected = True

    def start_ble(self):
        if self._ble_thread is not None and self._ble_thread.is_alive():
            return
        self.state = "searching"
        self.was_connected = False
        self._search_t0 = time.monotonic()
        self._ble_thread = threading.Thread(target=self._ble_worker, daemon=True)
        self._ble_thread.start()

    def set_robot_type(self, rtype):
        """Elige a qué robot conectar (dash / cue / dot / duo / any) y reconecta a ese."""
        rtype = (rtype or "").lower()
        args = ["--connect-eager"]
        if rtype in ("dash", "cue", "dot"):
            args += ["--connect-type", rtype]
            self.robot_type_pref = rtype
        elif rtype in ("duo", "dual", "dashcue", "dash_cue"):
            # "duo" se usa para la UI (Dash + Cue). En modo normal dejamos "any"
            # para no forzar un tipo único antes de arrancar la rutina dual.
            self.robot_type_pref = "duo"
        else:
            self.robot_type_pref = "any"
        self.connect_args = args
        self.reconnect()
        return self.robot_type_pref

    def reconnect(self):
        """Reconexión FORZADA: corta la conexión actual (aunque parezca viva) y
        vuelve a escanear. Resuelve las conexiones 'fantasma' (Bluetooth de Windows)."""
        if self.state == "searching":
            return self.state          # ya está reconectando
        self.state = "searching"
        self._search_t0 = time.monotonic()
        old = self._ble_thread
        try:
            mgr.WWBTLEManager.stop()    # cancela scan/conexión actual
        except Exception:
            pass
        self.bridge.robot = None

        def worker():
            if old is not None:
                old.join(timeout=4.0)   # esperar a que el hilo viejo muera
            self._ble_thread = None
            self.start_ble()
        threading.Thread(target=worker, daemon=True).start()
        return self.state

    def _ble_worker(self):
        try:
            parser = argparse.ArgumentParser()
            mgr.WWBTLEManager.setup_argument_parser(parser)
            args = parser.parse_args(self.connect_args)
            wwMain.start(self.bridge, args)
        except Exception as e:
            print("[BLE] error:", e)
        finally:
            self.bridge.robot = None
            self.state = "idle"

    # ---- estado ----
    def status(self):
        search_s = 0
        if self.state == "searching" and self._search_t0 is not None:
            search_s = int(time.monotonic() - self._search_t0)
        signal = self.bridge.connected and (time.monotonic() - self._last_sensor) < 4.0
        return {
            "state": self.state,
            "connected": self.bridge.connected,
            "signal": signal,
            "robot_pref": self.robot_type_pref,
            "robot": self.bridge.robot_label if self.bridge.connected else None,
            "search_s": search_s,
            "recording": self.recorder.recording,
            "rec_ms": self.recorder.elapsed_ms() if self.recorder.recording else 0,
            "rec_steps": self.recorder.step_count() if self.recorder.recording else 0,
            "replay": dict(self.replay),
            "avoid": self.avoid_enabled,
            "avoiding": self.avoiding,
            "explore": self.explore_enabled,
            "explore_profile": self.explore_profile,
            "explore_ai": self.explore_snapshot(),
            "distance": self.last_distance,
            "distance_rear": self.last_distance_rear,
            "alert": dict(self.alert),
            "agent_busy": self.agent_busy,
        }

    # ---- voz ----
    def voice(self, text, linear=30.0, turn=90.0):
        cmd = parse_voice_command(text)
        if cmd and self.bridge.connected and not self.replay["playing"]:
            self._exec(cmd, linear, turn)
        return cmd[0] if cmd else None

    def _exec(self, cmd, lin, turn):
        kind, payload = cmd
        lin = max(float(lin), 1.0)
        turn = max(float(turn), 1.0)
        # duracion acotada para que a baja velocidad no tarde demasiado
        move_t = min(self.DIST_CM / lin, 1.5)
        turn_t = min(self.TURN_DEG / turn, 1.3)
        if kind == "stop":
            self.motion.stop_now()
        elif kind == "fwd":
            self.motion.move(lin, 0, move_t)
        elif kind == "back":
            self.motion.move(-lin, 0, move_t)
        elif kind == "left":
            self.motion.move(0, turn, turn_t)
        elif kind == "right":
            self.motion.move(0, -turn, turn_t)
        elif kind == "light":
            self.bridge.lights(*payload)
        elif kind == "head_up":
            self.bridge.head(0, 20)
        elif kind == "head_down":
            self.bridge.head(0, -8)
        elif kind == "head_center":
            self.bridge.head(0, 0)
        elif kind == "dance":
            self.motion.dance()

    # ---- rutas ----
    def play_route(self, route_id):
        if self.replay["playing"] or not self.bridge.connected:
            return False
        steps = db_get_steps(route_id)
        if not steps:
            return False
        self._replay_stop.clear()
        self.replay = {"playing": True, "i": 0, "total": len(steps), "route_id": route_id}

        def progress(i, total):
            self.replay["i"] = i
            self.replay["total"] = total

        def worker():
            try:
                replay_route(self.bridge, steps, self._replay_stop, progress)
            finally:
                self.replay["playing"] = False

        threading.Thread(target=worker, daemon=True).start()
        return True

    def stop_replay(self):
        self._replay_stop.set()

    # ---- agente de IA: ejecutar un plan de pasos ----
    def run_plan_async(self, steps):
        """Ejecuta un plan (lista de acciones) en un hilo aparte. Devuelve True si arrancó."""
        if not steps or not self.bridge.connected or self.agent_busy:
            return False
        self._agent_stop.clear()
        threading.Thread(target=self._run_plan, args=(steps,), daemon=True).start()
        return True

    def stop_agent(self):
        self._agent_stop.set()

    def _run_plan(self, steps):
        r = self.bridge.robot
        if r is None:
            return
        b = r.cmds.body
        self.agent_busy = True
        try:
            for st in steps:
                if self._agent_stop.is_set():
                    break
                if not isinstance(st, dict):
                    continue
                act = str(st.get("action", "")).lower()

                if act in ("forward", "avanzar", "adelante", "walk", "move"):
                    cm = _clamp(st.get("cm", st.get("distance", 30)), 1, 200)
                    b.do_forward(cm, 35)
                elif act in ("backward", "back", "retroceder", "reverse", "atras"):
                    cm = _clamp(st.get("cm", st.get("distance", 30)), 1, 200)
                    b.do_forward(-cm, 35)
                elif act in ("turn", "girar", "turnleft", "turnright", "rotate"):
                    deg = abs(_clamp(st.get("deg", st.get("degrees", 90)), 1, 360))
                    d = str(st.get("dir", "")).lower()
                    # positivo = izquierda; negativo = derecha
                    if "right" in act or d in ("right", "derecha", "der", "r"):
                        deg = -deg
                    b.do_turn(deg, 90)
                elif act in ("lights", "light", "luz", "color"):
                    rgb = COLOR_RGB.get(str(st.get("color", "white")).lower(), (1, 1, 1))
                    self.bridge.lights(*rgb)
                elif act in ("head",):
                    pos = str(st.get("pos", "center")).lower()
                    self.bridge.head(0, 20 if "up" in pos or "arriba" in pos
                                     else (-8 if "down" in pos or "abajo" in pos else 0))
                elif act in ("dance", "baila", "bailar"):
                    self.motion.dance()
                    t0 = time.monotonic()
                    while time.monotonic() - t0 < 3.2:
                        if self._agent_stop.is_set():
                            break
                        time.sleep(0.05)
                elif act in ("sound", "voz", "voice", "audio"):
                    slot = st.get("slot")
                    if slot is not None:
                        try:
                            n = int(slot)
                            if 1 <= n <= 10:
                                self.bridge.play_sound("SYSTVOICE%d" % (n - 1))
                        except (TypeError, ValueError):
                            pass
                    elif st.get("name"):
                        self.bridge.play_sound(str(st.get("name")))
                    time.sleep(1.0)
                elif act in ("wait", "esperar", "pausa", "pause"):
                    secs = _clamp(st.get("seconds", st.get("s", 1)), 0, 5)
                    t0 = time.monotonic()
                    while time.monotonic() - t0 < secs:
                        if self._agent_stop.is_set():
                            break
                        time.sleep(0.05)
                elif act in ("stop", "parar", "alto", "detener"):
                    self.bridge.stop(record=False)
        except Exception as e:
            print("[agent] error ejecutando plan:", e)
        finally:
            self.bridge.stop(record=False)
            self.agent_busy = False

    def shutdown(self):
        try:
            self._replay_stop.set()
            self._agent_stop.set()
            self.explore_enabled = False
            mgr.WWBTLEManager.stop()
        except Exception:
            pass
