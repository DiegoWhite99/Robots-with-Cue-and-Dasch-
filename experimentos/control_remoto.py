# -*- coding: utf-8 -*-
"""
CONTROL REMOTO  -  Dash / Dot / Cue   (WonderPy + bleak)
========================================================

App de escritorio para manejar el robot como un control remoto, jugar con el,
hablarle por MICROFONO (comandos de voz), GRABAR la ruta y guardarla en una base
de datos SQLite para reproducirla despues.

Ejecutar:
    python control_remoto.py
    python control_remoto.py --type dash       (solo conectar a un Dash)
    python control_remoto.py --name Fede        (solo a un robot con ese nombre)

Controles de teclado (manten presionado para conducir):
    Flechas  o  W A S D   ->  adelante / atras / izquierda / derecha
    Barra espaciadora     ->  parar

Comandos de voz (boton "Escuchar"):
    "adelante", "atras", "izquierda", "derecha", "para"
    "luz roja/verde/azul/amarilla/blanca", "apaga la luz"
    "mira arriba", "mira abajo", "centra", "baila"

Arquitectura:
    - El Bluetooth (bleak) corre en un HILO de fondo (wwMain.start es bloqueante).
    - La GUI (tkinter) corre en el hilo principal.
    - El microfono/reconocimiento corre en OTRO hilo.
    - Los comandos al robot se "stagean" y se envian con send_staged(): es
      thread-safe, asi que se pueden mandar desde cualquier hilo.
    - La comunicacion hilos -> GUI se hace por una cola (queue) que la GUI revisa
      con root.after(), porque tkinter no es thread-safe.
"""

import os
import json
import time
import queue
import argparse
import sqlite3
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, colorchooser

import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr

# Reconocimiento de voz (opcional: si no esta instalado, la app igual funciona).
try:
    import speech_recognition as sr
    VOICE_AVAILABLE = True
except Exception:
    sr = None
    VOICE_AVAILABLE = False

VOICE_LANG = "es-ES"   # idioma para Google Speech (es-ES funciona bien para LatAm)


# --------------------------------------------------------------------------- #
#  Paleta de colores                                                          #
# --------------------------------------------------------------------------- #
BG      = "#15161a"
PANEL   = "#22242b"
FIELD   = "#2b2e37"
TEXT    = "#e8e8ea"
MUTE    = "#9aa0a6"
ACCENT  = "#3b82f6"
OK      = "#22c55e"
WARN    = "#f59e0b"
DANGER  = "#ef4444"


def friendly_type(robot):
    """Convierte WW_ROBOT_CUE -> 'Cue', etc."""
    names = {"WW_ROBOT_DASH": "Dash", "WW_ROBOT_DOT": "Dot", "WW_ROBOT_CUE": "Cue"}
    return names.get(robot.robot_type_name, robot.robot_type_name)


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
    """events: lista de (t_offset_ms, action, params_dict). Devuelve el id."""
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
#  Grabador de ruta (log de eventos con marca de tiempo)                       #
# --------------------------------------------------------------------------- #
class Recorder:
    def __init__(self):
        self.recording = False
        self._t0 = None
        self.events = []  # [(t_offset_ms, action, params_dict), ...]

    def start(self):
        self.events = []
        self._t0 = time.monotonic()
        self.recording = True

    def stop(self):
        self.recording = False
        return self.events, self.duration_ms()

    def duration_ms(self):
        return self.events[-1][0] if self.events else 0

    def elapsed_ms(self):
        if self._t0 is None:
            return 0
        return int((time.monotonic() - self._t0) * 1000)

    def add(self, action, params):
        if not self.recording:
            return
        t = int((time.monotonic() - self._t0) * 1000)
        self.events.append((t, action, params))


# --------------------------------------------------------------------------- #
#  Puente con el robot (delegate de WonderPy + envio de comandos)              #
# --------------------------------------------------------------------------- #
class RobotBridge:
    def __init__(self, recorder):
        self.robot = None
        self.recorder = recorder
        self.on_connect_cb = None
        self._last_vel = None
        self._last_head = None
        self._last_light = None

    # ---- interfaz que llama WonderPy (hilo BLE) ----
    def on_connect(self, robot):
        self.robot = robot
        if self.on_connect_cb:
            self.on_connect_cb(robot)

    def on_sensors(self, robot):
        pass

    @property
    def connected(self):
        return self.robot is not None

    @property
    def robot_label(self):
        if not self.robot:
            return "?"
        return "%s '%s'" % (friendly_type(self.robot), self.robot.name)

    @property
    def robot_type_name(self):
        return self.robot.robot_type_name if self.robot else "?"

    # ---- comandos (seguros desde cualquier hilo) ----
    def drive(self, linear_cm_s, angular_deg_s, record=True):
        r = self.robot
        if r is None:
            return
        vel = (round(linear_cm_s, 2), round(angular_deg_s, 2))
        if vel == self._last_vel:
            return
        self._last_vel = vel
        r.cmds.body.stage_linear_angular(linear_cm_s, angular_deg_s)
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
        r.cmds.body.stage_stop()
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


# --------------------------------------------------------------------------- #
#  Ejecutor de movimientos por rafagas (para voz)                              #
# --------------------------------------------------------------------------- #
class MotionRunner:
    """Conduce durante 'duration' segundos y luego para. Un comando nuevo
    cancela el anterior sin parpadeos. 'para' detiene de inmediato."""

    def __init__(self, bridge):
        self.bridge = bridge
        self._cancel = threading.Event()

    def _spawn(self, fn):
        self._cancel.set()                 # cancela el movimiento anterior
        self._cancel = threading.Event()
        ev = self._cancel
        threading.Thread(target=lambda: fn(ev), daemon=True).start()

    def move(self, linear, angular, duration):
        def work(ev):
            self.bridge.drive(linear, angular)
            t0 = time.monotonic()
            while time.monotonic() - t0 < duration:
                if ev.is_set():
                    return               # reemplazado por otro comando -> no parar
                time.sleep(0.02)
            self.bridge.stop()
        self._spawn(work)

    def stop_now(self):
        self._cancel.set()
        self.bridge.stop()

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
    """Devuelve ('kind', payload) o None segun lo que se dijo.

    Nota: las DIRECCIONES se revisan antes que "para" (detener), porque en
    espanol "para atras"/"para adelante" indican direccion, no detenerse.
    """
    t = " " + text.lower().strip() + " "

    if any(w in t for w in [" atras", "atrás", "reversa", "retrocede"]):
        return ("back", None)
    if "derecha" in t:
        return ("right", None)
    if "izquierda" in t:
        return ("left", None)
    if any(w in t for w in ["adelante", "avanza", "camina", "sigue", "palante"]):
        return ("fwd", None)
    # "para" se interpreta como detener solo si no hubo una direccion arriba.
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
    if any(w in t for w in ["blanc", "blanco", "blanca"]):
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


class VoiceController:
    """Escucha el microfono en un hilo, reconoce con Google y ejecuta comandos."""

    DIST_CM = 25.0     # cuanto avanza al decir "adelante"
    TURN_DEG = 90.0    # cuanto gira al decir "izquierda/derecha"

    def __init__(self, bridge, motion, speeds_provider, ui_cb):
        self.bridge = bridge
        self.motion = motion
        self.speeds = speeds_provider          # () -> (linear, turn)
        self.ui_cb = ui_cb                     # (subkind, payload) -> GUI
        self.listening = False
        self._thread = None
        self.recognizer = sr.Recognizer() if VOICE_AVAILABLE else None

    def start(self):
        if not VOICE_AVAILABLE or self.listening:
            return
        self.listening = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.listening = False

    def _loop(self):
        r = self.recognizer
        try:
            mic = sr.Microphone()
        except Exception as e:
            self.ui_cb("error", "No se pudo abrir el microfono: %s" % e)
            self.listening = False
            self.ui_cb("state", "off")
            return
        try:
            with mic as source:
                self.ui_cb("state", "calibrando")
                r.adjust_for_ambient_noise(source, duration=0.6)
                while self.listening:
                    self.ui_cb("state", "escuchando")
                    try:
                        audio = r.listen(source, timeout=3, phrase_time_limit=4)
                    except sr.WaitTimeoutError:
                        continue
                    if not self.listening:
                        break
                    self.ui_cb("state", "procesando")
                    try:
                        text = r.recognize_google(audio, language=VOICE_LANG)
                    except sr.UnknownValueError:
                        self.ui_cb("heard", ("(no te entendi)", None))
                        continue
                    except sr.RequestError as e:
                        self.ui_cb("error", "Sin internet para Google: %s" % e)
                        continue
                    cmd = parse_voice_command(text)
                    self.ui_cb("heard", (text, cmd))
                    if cmd:
                        self._execute(cmd)
        except Exception as e:
            self.ui_cb("error", "Microfono: %s" % e)
        finally:
            self.ui_cb("state", "off")

    def _execute(self, cmd):
        kind, payload = cmd
        if not self.bridge.connected:
            self.ui_cb("note", "Conecta el robot para que obedezca.")
            return
        lin, turn = self.speeds()
        lin = max(lin, 1.0)
        turn = max(turn, 1.0)
        if kind == "stop":
            self.motion.stop_now()
        elif kind == "fwd":
            self.motion.move(lin, 0, self.DIST_CM / lin)
        elif kind == "back":
            self.motion.move(-lin, 0, self.DIST_CM / lin)
        elif kind == "left":
            self.motion.move(0, turn, self.TURN_DEG / turn)
        elif kind == "right":
            self.motion.move(0, -turn, self.TURN_DEG / turn)
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


# --------------------------------------------------------------------------- #
#  Reproductor de ruta (corre en un hilo aparte)                               #
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
#  Helpers de UI                                                               #
# --------------------------------------------------------------------------- #
def mk_button(parent, text, command=None, bg=FIELD, fg=TEXT, font=("Segoe UI", 10),
              width=None, height=None):
    return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, font=font,
                     activebackground=ACCENT, activeforeground="white",
                     relief="flat", bd=0, width=width, height=height,
                     highlightthickness=0, cursor="hand2", padx=8, pady=4)


def section(parent, title):
    f = tk.LabelFrame(parent, text="  " + title + "  ", fg=TEXT, bg=PANEL,
                      font=("Segoe UI", 10, "bold"), bd=0, relief="flat",
                      labelanchor="nw")
    return f


# tecla -> direccion
DIR_KEYS = {
    "Up": "fwd", "w": "fwd", "W": "fwd",
    "Down": "back", "s": "back", "S": "back",
    "Left": "left", "a": "left", "A": "left",
    "Right": "right", "d": "right", "D": "right",
}

PRESET_COLORS = [
    ("Rojo",     (1.0, 0.0, 0.0), "#ef4444", "white"),
    ("Verde",    (0.0, 1.0, 0.0), "#22c55e", "white"),
    ("Azul",     (0.0, 0.3, 1.0), "#3b82f6", "white"),
    ("Amarillo", (1.0, 0.9, 0.0), "#eab308", "black"),
    ("Magenta",  (1.0, 0.0, 0.7), "#ec4899", "white"),
    ("Cyan",     (0.0, 0.9, 1.0), "#06b6d4", "black"),
    ("Blanco",   (1.0, 1.0, 1.0), "#f3f4f6", "black"),
    ("Apagar",   (0.0, 0.0, 0.0), "#3f3f46", "white"),
]


# --------------------------------------------------------------------------- #
#  Aplicacion                                                                  #
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root, connect_args):
        self.root = root
        self.connect_args = connect_args

        self.recorder = Recorder()
        self.bridge = RobotBridge(self.recorder)
        self.bridge.on_connect_cb = lambda robot: self._post(("connected", robot))
        self.motion = MotionRunner(self.bridge)

        self.voice = None
        if VOICE_AVAILABLE:
            self.voice = VoiceController(
                self.bridge, self.motion,
                lambda: (float(self.speed_lin.get()), float(self.speed_ang.get())),
                lambda sub, payload: self._post(("voice", (sub, payload))),
            )

        self.msg_queue = queue.Queue()
        self.held = set()
        self._pending_release = {}
        self._head_after = None
        self._head_target = (0.0, 0.0)

        self.ble_thread = None
        self.was_connected = False
        self.state = "idle"            # idle | searching | connected
        self._search_t0 = None

        self.replaying = False
        self.replay_stop = threading.Event()

        root.title("Control Remoto  -  Dash / Dot / Cue")
        root.geometry("940x820")
        root.minsize(900, 780)
        root.configure(bg=BG)

        self._init_style()
        self._build_ui()
        self._bind_keys()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.start_ble()
        self.root.after(150, self._poll_queue)
        self.refresh_routes()
        self._tick()

    def _init_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Treeview", background=FIELD, fieldbackground=FIELD,
                     foreground=TEXT, rowheight=24, borderwidth=0)
        st.configure("Treeview.Heading", background=PANEL, foreground=MUTE,
                     relief="flat", font=("Segoe UI", 9, "bold"))
        st.map("Treeview", background=[("selected", ACCENT)],
               foreground=[("selected", "white")])

    # ----------------------------- UI ----------------------------- #
    def _build_ui(self):
        # ---------- cabecera ----------
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(header, text="🤖  Control Remoto", bg=BG, fg=TEXT,
                 font=("Segoe UI", 18, "bold")).pack(side="left")

        self.status_pill = tk.Label(header, text="  Buscando robot...  ", bg=WARN,
                                    fg="white", font=("Segoe UI", 10, "bold"),
                                    padx=10, pady=4)
        self.status_pill.pack(side="right")
        self.btn_reconnect = mk_button(header, "Reconectar", command=self.start_ble)
        self.btn_reconnect.pack(side="right", padx=8)
        self.btn_reconnect.config(state="disabled")

        # ---------- cuerpo: 3 columnas ----------
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=4)
        for c in range(3):
            body.columnconfigure(c, weight=1, uniform="col")

        self._build_drive(body)
        self._build_head_lights(body)
        self._build_voice(body)
        self._build_routes()

    def _build_drive(self, body):
        f = section(body, "🎮 Conducción")
        f.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        pad = tk.Frame(f, bg=PANEL)
        pad.pack(padx=16, pady=(14, 6))
        bf = ("Segoe UI", 20, "bold")
        self.b_fwd = mk_button(pad, "▲", width=4, height=2, font=bf)
        self.b_left = mk_button(pad, "◀", width=4, height=2, font=bf)
        self.b_stop = mk_button(pad, "■", width=4, height=2, font=bf,
                                fg="white", bg=DANGER, command=self.press_stop)
        self.b_right = mk_button(pad, "▶", width=4, height=2, font=bf)
        self.b_back = mk_button(pad, "▼", width=4, height=2, font=bf)
        self.b_fwd.grid(row=0, column=1, padx=4, pady=4)
        self.b_left.grid(row=1, column=0, padx=4, pady=4)
        self.b_stop.grid(row=1, column=1, padx=4, pady=4)
        self.b_right.grid(row=1, column=2, padx=4, pady=4)
        self.b_back.grid(row=2, column=1, padx=4, pady=4)
        for b, tok in [(self.b_fwd, "fwd"), (self.b_back, "back"),
                       (self.b_left, "left"), (self.b_right, "right")]:
            self._bind_hold(b, tok)
        self.drive_buttons = [self.b_fwd, self.b_back, self.b_left, self.b_right, self.b_stop]

        sld = tk.Frame(f, bg=PANEL)
        sld.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(sld, text="Velocidad (cm/s)", fg=MUTE, bg=PANEL,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self.speed_lin = self._slider(sld, 5, 100, 30)
        tk.Label(sld, text="Giro (grados/s)", fg=MUTE, bg=PANEL,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self.speed_ang = self._slider(sld, 15, 220, 90)

        tk.Label(f, text="Teclado: flechas / W A S D   ·   Espacio = parar",
                 fg=MUTE, bg=PANEL, font=("Segoe UI", 8)).pack(pady=(0, 12))

    def _slider(self, parent, lo, hi, val):
        s = tk.Scale(parent, from_=lo, to=hi, orient="horizontal", bg=PANEL,
                     fg=TEXT, highlightthickness=0, troughcolor=FIELD, bd=0,
                     activebackground=ACCENT, command=lambda v: self.update_drive())
        s.set(val)
        s.pack(fill="x", pady=(0, 6))
        return s

    def _build_head_lights(self, body):
        mid = tk.Frame(body, bg=BG)
        mid.grid(row=0, column=1, sticky="nsew", padx=8)

        head = section(mid, "🙂 Cabeza")
        head.pack(fill="x")
        hf = tk.Frame(head, bg=PANEL)
        hf.pack(fill="x", padx=16, pady=12)
        tk.Label(hf, text="Pan  (izquierda ↔ derecha)", fg=MUTE, bg=PANEL,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self.pan_var = tk.DoubleVar(value=0.0)
        self.scale_pan = tk.Scale(hf, from_=-120, to=120, orient="horizontal",
                                  variable=self.pan_var, bg=PANEL, fg=TEXT,
                                  highlightthickness=0, troughcolor=FIELD, bd=0,
                                  activebackground=ACCENT,
                                  command=lambda v: self.on_head_change())
        self.scale_pan.pack(fill="x")
        tk.Label(hf, text="Tilt  (abajo ↔ arriba)", fg=MUTE, bg=PANEL,
                 font=("Segoe UI", 9)).pack(anchor="w")
        self.tilt_var = tk.DoubleVar(value=0.0)
        self.scale_tilt = tk.Scale(hf, from_=-10, to=22, orient="horizontal",
                                   variable=self.tilt_var, bg=PANEL, fg=TEXT,
                                   highlightthickness=0, troughcolor=FIELD, bd=0,
                                   activebackground=ACCENT,
                                   command=lambda v: self.on_head_change())
        self.scale_tilt.pack(fill="x")
        self.btn_head_center = mk_button(hf, "Centrar cabeza", command=self.center_head)
        self.btn_head_center.pack(pady=(8, 0), fill="x")

        lights = section(mid, "💡 Luces")
        lights.pack(fill="x", pady=(10, 0))
        lf = tk.Frame(lights, bg=PANEL)
        lf.pack(padx=16, pady=12)
        self.light_buttons = []
        for i, (name, rgb, hexc, fg) in enumerate(PRESET_COLORS):
            b = mk_button(lf, name, bg=hexc, fg=fg, width=8,
                          command=lambda c=rgb: self.bridge.lights(*c))
            b.grid(row=i // 4, column=i % 4, padx=4, pady=4)
            self.light_buttons.append(b)
        self.btn_pick = mk_button(lights, "Elegir color...", command=self.pick_color)
        self.btn_pick.pack(pady=(0, 12), padx=16, fill="x")

    def _build_voice(self, body):
        f = section(body, "🎤 Voz")
        f.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        inner = tk.Frame(f, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=16, pady=12)

        self.btn_voice = mk_button(inner, "🎤  Escuchar", command=self.toggle_voice,
                                   bg=ACCENT, fg="white",
                                   font=("Segoe UI", 11, "bold"))
        self.btn_voice.pack(fill="x")

        self.voice_state = tk.Label(inner, text="", fg=MUTE, bg=PANEL,
                                    font=("Segoe UI", 9))
        self.voice_state.pack(anchor="w", pady=(8, 2))
        self.voice_heard = tk.Label(inner, text="", fg=TEXT, bg=PANEL,
                                    font=("Segoe UI", 11, "bold"), wraplength=240,
                                    justify="left")
        self.voice_heard.pack(anchor="w", pady=(0, 8))

        if not VOICE_AVAILABLE:
            self.btn_voice.config(state="disabled", bg=FIELD)
            self.voice_state.config(
                text="Voz no disponible.\nInstala:  pip install SpeechRecognition PyAudio",
                fg=WARN)
        else:
            tk.Label(inner, text="Di por ejemplo:", fg=MUTE, bg=PANEL,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w")
            ejemplos = ('"adelante" · "atrás"\n'
                        '"izquierda" · "derecha"\n'
                        '"para"  (detener)\n'
                        '"luz roja/verde/azul"\n'
                        '"apaga la luz"\n'
                        '"mira arriba" · "centra"\n'
                        '"baila"')
            tk.Label(inner, text=ejemplos, fg=MUTE, bg=PANEL, justify="left",
                     font=("Consolas", 9)).pack(anchor="w")
            tk.Label(inner, text="Si grabas una ruta, los comandos de voz\n"
                                 "también quedan guardados en ella.",
                     fg=MUTE, bg=PANEL, font=("Segoe UI", 8),
                     justify="left").pack(anchor="w", pady=(8, 0))

    def _build_routes(self):
        bottom = section(self.root, "🗺 Rutas guardadas")
        bottom.pack(fill="both", expand=True, padx=16, pady=(8, 14))

        recbar = tk.Frame(bottom, bg=PANEL)
        recbar.pack(fill="x", padx=16, pady=(12, 6))
        self.btn_record = mk_button(recbar, "●  Grabar ruta", command=self.toggle_record,
                                    bg=DANGER, fg="white", font=("Segoe UI", 10, "bold"))
        self.btn_record.pack(side="left")
        self.rec_lbl = tk.Label(recbar, text="", fg=WARN, bg=PANEL,
                                font=("Segoe UI", 10, "bold"))
        self.rec_lbl.pack(side="left", padx=14)

        wrap = tk.Frame(bottom, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        cols = ("nombre", "fecha", "dur", "pasos")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=6)
        for c, txt, w, anc in [("nombre", "Nombre", 240, "w"),
                               ("fecha", "Fecha", 150, "center"),
                               ("dur", "Duración", 90, "center"),
                               ("pasos", "Pasos", 60, "center")]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor=anc)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.play_selected())
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        act = tk.Frame(bottom, bg=PANEL)
        act.pack(fill="x", padx=16, pady=(0, 12))
        self.btn_play = mk_button(act, "▶  Reproducir", command=self.play_selected,
                                  bg=OK, fg="white", font=("Segoe UI", 10, "bold"))
        self.btn_play.pack(side="left")
        self.btn_del = mk_button(act, "Borrar", command=self.delete_selected)
        self.btn_del.pack(side="left", padx=8)
        mk_button(act, "Actualizar", command=self.refresh_routes).pack(side="left")
        self.replay_lbl = tk.Label(act, text="", fg=MUTE, bg=PANEL,
                                   font=("Segoe UI", 9))
        self.replay_lbl.pack(side="left", padx=14)

    # ----------------------- teclado / hold ----------------------- #
    def _bind_keys(self):
        self.root.bind("<KeyPress>", self.on_key_press)
        self.root.bind("<KeyRelease>", self.on_key_release)
        self.root.focus_set()

    def _bind_hold(self, button, token):
        button.bind("<ButtonPress-1>", lambda e, t=token: self.btn_press(t))
        button.bind("<ButtonRelease-1>", lambda e, t=token: self.btn_release(t))

    def btn_press(self, token):
        if self.replaying or not self.bridge.connected:
            return
        self.held.add(token)
        self.update_drive()

    def btn_release(self, token):
        self.held.discard(token)
        self.update_drive()

    def press_stop(self):
        self.held.clear()
        self.motion.stop_now()

    def on_key_press(self, event):
        if self.replaying:
            return
        sym = event.keysym
        if sym == "space":
            self.press_stop()
            return
        token = DIR_KEYS.get(sym)
        if token is None:
            return
        aid = self._pending_release.pop(token, None)
        if aid is not None:
            self.root.after_cancel(aid)
        if token not in self.held:
            self.held.add(token)
            self.update_drive()

    def on_key_release(self, event):
        token = DIR_KEYS.get(event.keysym)
        if token is None:
            return
        aid = self.root.after(60, lambda t=token: self._release_token(t))
        self._pending_release[token] = aid

    def _release_token(self, token):
        self._pending_release.pop(token, None)
        if token in self.held:
            self.held.discard(token)
            self.update_drive()

    def update_drive(self):
        if not self.bridge.connected or self.replaying:
            return
        v = float(self.speed_lin.get())
        t = float(self.speed_ang.get())
        lin = (v if "fwd" in self.held else 0.0) - (v if "back" in self.held else 0.0)
        ang = (t if "left" in self.held else 0.0) - (t if "right" in self.held else 0.0)
        if lin == 0.0 and ang == 0.0:
            self.bridge.stop()
        else:
            self.bridge.drive(lin, ang)

    # ----------------------- cabeza / luces ----------------------- #
    def on_head_change(self):
        if not self.bridge.connected or self.replaying:
            return
        self._head_target = (self.pan_var.get(), self.tilt_var.get())
        if self._head_after is None:
            self._head_after = self.root.after(80, self._flush_head)

    def _flush_head(self):
        self._head_after = None
        self.bridge.head(*self._head_target)

    def center_head(self):
        self.pan_var.set(0.0)
        self.tilt_var.set(0.0)
        self.bridge.head(0.0, 0.0)

    def pick_color(self):
        rgb, _ = colorchooser.askcolor(title="Color de las luces")
        if rgb:
            self.bridge.lights(*(c / 255.0 for c in rgb))

    # ----------------------- voz ----------------------- #
    def toggle_voice(self):
        if self.voice is None:
            return
        if not self.voice.listening:
            if not self.bridge.connected:
                messagebox.showwarning("Sin robot", "Conecta el robot primero.")
                return
            self.voice.start()
            self.btn_voice.config(text="■  Dejar de escuchar", bg=DANGER)
        else:
            self.voice.stop()
            self.btn_voice.config(text="🎤  Escuchar", bg=ACCENT)
            self.voice_state.config(text="")

    def _handle_voice(self, sub, payload):
        if sub == "state":
            txt = {"calibrando": "Calibrando micrófono...",
                   "escuchando": "● Escuchando...",
                   "procesando": "Procesando...",
                   "off": ""}.get(payload, "")
            color = OK if payload == "escuchando" else MUTE
            self.voice_state.config(text=txt, fg=color)
            if payload == "off":
                self.btn_voice.config(text="🎤  Escuchar", bg=ACCENT)
        elif sub == "heard":
            text, cmd = payload
            label = self._cmd_label(cmd)
            self.voice_heard.config(text='"%s"%s' % (text, label))
        elif sub == "note":
            self.voice_state.config(text=payload, fg=WARN)
        elif sub == "error":
            self.voice_state.config(text=payload, fg=DANGER)
            self.btn_voice.config(text="🎤  Escuchar", bg=ACCENT)

    def _cmd_label(self, cmd):
        if not cmd:
            return "  →  (sin acción)"
        labels = {"stop": "parar", "fwd": "avanzar", "back": "retroceder",
                  "left": "girar izq.", "right": "girar der.", "light": "luz",
                  "head_up": "mirar arriba", "head_down": "mirar abajo",
                  "head_center": "centrar", "dance": "¡bailar!"}
        return "  →  " + labels.get(cmd[0], cmd[0])

    # ----------------------- BLE ----------------------- #
    def start_ble(self):
        if self.ble_thread is not None and self.ble_thread.is_alive():
            return
        self.state = "searching"
        self.was_connected = False
        self._search_t0 = time.monotonic()
        self._set_status(WARN, "Buscando robot...")
        self.btn_reconnect.config(state="disabled")
        self.ble_thread = threading.Thread(target=self._ble_worker, daemon=True)
        self.ble_thread.start()

    def _ble_worker(self):
        try:
            parser = argparse.ArgumentParser()
            mgr.WWBTLEManager.setup_argument_parser(parser)
            args = parser.parse_args(self.connect_args)
            wwMain.start(self.bridge, args)        # bloquea hasta desconectar
        except Exception as e:
            self._post(("error", str(e)))
        finally:
            self.bridge.robot = None
            self._post(("disconnected", None))

    def _post(self, msg):
        self.msg_queue.put(msg)

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "connected":
                    self.state = "connected"
                    self.was_connected = True
                    self._set_status(OK, "Conectado a %s" % self.bridge.robot_label)
                    self.btn_reconnect.config(state="normal")
                    self._set_controls(True)
                elif kind == "disconnected":
                    self.state = "idle"
                    if self.voice:
                        self.voice.stop()
                    self._set_controls(False)
                    self.btn_reconnect.config(state="normal")
                    if self.was_connected:
                        self._set_status(DANGER, "Desconectado")
                    else:
                        self._set_status(DANGER, "No se encontró robot")
                elif kind == "error":
                    self._set_status(DANGER, "Error: %s" % payload)
                elif kind == "voice":
                    self._handle_voice(*payload)
                elif kind == "replay_progress":
                    i, total = payload
                    self.replay_lbl.config(text="Reproduciendo  %d/%d" % (i, total))
                elif kind == "replay_done":
                    self._end_replay()
        except queue.Empty:
            pass
        self.root.after(150, self._poll_queue)

    def _set_status(self, color, text):
        self.status_pill.config(bg=color, text="  " + text + "  ")

    def _set_controls(self, enabled):
        state = "normal" if enabled else "disabled"
        widgets = list(self.drive_buttons) + self.light_buttons + [
            self.btn_head_center, self.btn_pick, self.scale_pan, self.scale_tilt,
            self.btn_record, self.btn_play, self.btn_del]
        for w in widgets:
            w.config(state=state)
        if self.voice is not None:
            self.btn_voice.config(state=state)

    # ----------------------- grabacion ----------------------- #
    def toggle_record(self):
        if not self.recorder.recording:
            if not self.bridge.connected:
                messagebox.showwarning("Sin robot", "Conecta el robot primero.")
                return
            self.recorder.start()
            self.btn_record.config(text="■  Detener y guardar", bg=WARN)
        else:
            events, duration = self.recorder.stop()
            self.btn_record.config(text="●  Grabar ruta", bg=DANGER)
            self.rec_lbl.config(text="")
            if not events:
                messagebox.showinfo("Ruta vacía", "No se grabó ningún movimiento.")
                return
            name = simpledialog.askstring(
                "Guardar ruta", "Nombre de la ruta:",
                initialvalue=datetime.now().strftime("Ruta %Y-%m-%d %H:%M"))
            if not name:
                return
            db_save_route(name, self.bridge.robot_type_name, events, duration)
            self.refresh_routes()
            messagebox.showinfo("Guardada", "Ruta '%s' guardada (%d pasos, %.1fs)."
                                % (name, len(events), duration / 1000.0))

    # ----------------------- rutas ----------------------- #
    def refresh_routes(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rid, name, created, dur_ms, rtype, n in db_list_routes():
            self.tree.insert("", "end", iid=str(rid),
                             values=(name, created.replace("T", " "),
                                     "%.1fs" % (dur_ms / 1000.0), n))

    def _selected_route_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def delete_selected(self):
        rid = self._selected_route_id()
        if rid is None:
            messagebox.showinfo("Selecciona", "Elige una ruta de la lista.")
            return
        name = self.tree.item(str(rid), "values")[0]
        if messagebox.askyesno("Borrar", "¿Borrar la ruta '%s'?" % name):
            db_delete_route(rid)
            self.refresh_routes()

    def play_selected(self):
        if self.replaying:
            self.replay_stop.set()
            return
        rid = self._selected_route_id()
        if rid is None:
            messagebox.showinfo("Selecciona", "Elige una ruta de la lista.")
            return
        if not self.bridge.connected:
            messagebox.showwarning("Sin robot", "Conecta el robot primero.")
            return
        steps = db_get_steps(rid)
        if not steps:
            messagebox.showinfo("Ruta vacía", "Esa ruta no tiene pasos.")
            return

        self.replaying = True
        self.replay_stop.clear()
        self.held.clear()
        if self.voice:
            self.voice.stop()
        self._set_controls(False)
        self.btn_play.config(state="normal", text="■  Detener", bg=DANGER)
        self.replay_lbl.config(text="Reproduciendo...")

        def progress(i, total):
            self._post(("replay_progress", (i, total)))

        def worker():
            try:
                replay_route(self.bridge, steps, self.replay_stop, progress)
            finally:
                self._post(("replay_done", None))

        threading.Thread(target=worker, daemon=True).start()

    def _end_replay(self):
        self.replaying = False
        self.btn_play.config(text="▶  Reproducir", bg=OK)
        self.replay_lbl.config(text="")
        if self.bridge.connected:
            self._set_controls(True)

    # ----------------------- tick (estado/grabacion) ----------------------- #
    def _tick(self):
        if self.state == "searching" and self._search_t0 is not None:
            secs = int(time.monotonic() - self._search_t0)
            self._set_status(WARN, "Buscando robot...  (%ds, ~20-30s)" % secs)
        if self.recorder.recording:
            self.rec_lbl.config(text="● REC   %.1fs   ·   %d pasos"
                                % (self.recorder.elapsed_ms() / 1000.0,
                                   len(self.recorder.events)))
        self.root.after(250, self._tick)

    # ----------------------- cierre ----------------------- #
    def on_close(self):
        try:
            self.replay_stop.set()
            if self.voice:
                self.voice.stop()
            if self.bridge.connected:
                self.motion.stop_now()
            mgr.WWBTLEManager.stop()
        except Exception:
            pass
        self.root.after(200, self.root.destroy)


def parse_cli():
    p = argparse.ArgumentParser(description="Control remoto gráfico para Dash/Dot/Cue")
    p.add_argument("--type", choices=["dash", "dot", "cue"],
                   help="conectar solo a este tipo de robot")
    p.add_argument("--name", help="conectar solo a un robot con este nombre")
    a = p.parse_args()
    connect = ["--connect-eager"]
    if a.type:
        connect += ["--connect-type", a.type]
    if a.name:
        connect += ["--connect-name", a.name]
    return connect


def main():
    connect_args = parse_cli()
    db_init()
    root = tk.Tk()
    App(root, connect_args)
    root.mainloop()


if __name__ == "__main__":
    main()
