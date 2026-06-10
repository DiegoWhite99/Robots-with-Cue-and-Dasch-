# -*- coding: utf-8 -*-
"""
rutina_duo_agente.py

Rutina para conectar Dash y Cue al mismo tiempo (en paralelo) e
interactuar con un plan coordinado generado por un agente.

Uso:
    python rutinas/rutina_duo_agente.py
    python rutinas/rutina_duo_agente.py --dash-name Dasch --cue-name Cue
    python rutinas/rutina_duo_agente.py --prompt "haz que se saluden y bailen"
    python rutinas/rutina_duo_agente.py --sin-agente

Notas:
    - WonderPy maneja una conexion BLE por proceso; por eso aqui usamos
      2 procesos (uno por robot) para conectarlos en paralelo.
    - Si OPENAI_API_KEY no existe o falla la API, usa una rutina default.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr


COLOR_MAP = {
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.3, 1.0),
    "yellow": (1.0, 0.9, 0.0),
    "magenta": (1.0, 0.0, 0.7),
    "cyan": (0.0, 0.9, 1.0),
    "white": (1.0, 1.0, 1.0),
    "off": (0.0, 0.0, 0.0),
}

VALID_ACTORS = {"dash", "cue"}
VALID_ACTIONS = {"lights", "move", "turn", "head", "sound", "spin", "stop"}
BT_TIMEOUT_S = 35


def _safe_status_put(status_q: Any, payload: Dict[str, Any]) -> None:
    try:
        status_q.put(payload)
    except Exception:
        pass


class DualWorkerDelegate:
    """Delegate WonderPy que ejecuta comandos de una cola para un robot."""

    def __init__(self, role: str, cmd_q: mp.Queue, status_q: mp.Queue):
        self.role = role
        self.cmd_q = cmd_q
        self.status_q = status_q
        self._stop = threading.Event()

    def on_connect(self, robot):
        _safe_status_put(self.status_q, {
            "kind": "connected",
            "role": self.role,
            "name": robot.name,
            "type": robot.robot_type_name,
        })
        th = threading.Thread(target=self._command_loop, args=(robot,), daemon=True)
        th.start()

    def on_sensors(self, robot):
        pass

    def _stage_stop(self, robot):
        try:
            robot.cmds.body.stage_stop(320)
            robot.send_staged()
        except Exception:
            pass

    def _set_lights(self, robot, color_name: str):
        rgb = COLOR_MAP.get((color_name or "white").lower(), COLOR_MAP["white"])
        robot.cmds.RGB.stage_all(*rgb)
        robot.send_staged()

    def _set_head(self, robot, pos: str):
        p = (pos or "center").lower()
        if p == "up":
            robot.cmds.head.stage_pan_tilt_angle(0, 20)
        elif p == "down":
            robot.cmds.head.stage_pan_tilt_angle(0, -8)
        else:
            robot.cmds.head.stage_pan_tilt_angle(0, 0)
        robot.send_staged()

    def _play_slot(self, robot, slot: int):
        try:
            if 1 <= slot <= 10:
                robot.cmds.media.stage_audio("SYSTVOICE%d" % (slot - 1), 1.0)
                robot.send_staged()
        except Exception:
            pass

    def _short_spin(self, robot):
        # Giro corto en dos sentidos + color para dar "interaccion".
        self._set_lights(robot, "cyan")
        robot.cmds.body.do_turn(120, 120)
        robot.cmds.body.do_turn(-120, 120)
        self._set_lights(robot, "off")

    def _exec_step(self, robot, step: Dict[str, Any]):
        act = str(step.get("action", "")).lower()
        if act == "lights":
            self._set_lights(robot, str(step.get("color", "white")))
            return
        if act == "move":
            cm = max(-200.0, min(200.0, float(step.get("cm", 25))))
            speed = max(8.0, min(120.0, float(step.get("speed", 35))))
            robot.cmds.body.do_forward(cm, speed)
            return
        if act == "turn":
            deg = max(-360.0, min(360.0, float(step.get("deg", 90))))
            speed = max(20.0, min(180.0, float(step.get("speed", 90))))
            robot.cmds.body.do_turn(deg, speed)
            return
        if act == "head":
            self._set_head(robot, str(step.get("pos", "center")))
            return
        if act == "sound":
            slot = int(step.get("slot", 0))
            self._play_slot(robot, slot)
            return
        if act == "spin":
            self._short_spin(robot)
            return
        if act == "stop":
            self._stage_stop(robot)
            return

    def _command_loop(self, robot):
        try:
            while not self._stop.is_set():
                try:
                    cmd = self.cmd_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                except Exception:
                    continue

                action = str(cmd.get("action", "")).lower()
                if action == "shutdown":
                    break

                try:
                    self._exec_step(robot, cmd)
                    _safe_status_put(self.status_q, {
                        "kind": "step_done",
                        "role": self.role,
                        "action": action,
                    })
                except Exception as e:
                    _safe_status_put(self.status_q, {
                        "kind": "step_error",
                        "role": self.role,
                        "action": action,
                        "error": str(e)[:200],
                    })
        finally:
            self._stage_stop(robot)
            self._stop.set()
            try:
                mgr.WWBTLEManager.stop()
            except Exception:
                pass


def worker_main(role: str, robot_type: str, robot_name: str | None,
                cmd_q: mp.Queue, status_q: mp.Queue):
    _safe_status_put(status_q, {"kind": "worker_start", "role": role, "type": robot_type})
    delegate = DualWorkerDelegate(role, cmd_q, status_q)
    parser = argparse.ArgumentParser()
    mgr.WWBTLEManager.setup_argument_parser(parser)
    args_list = ["--connect-eager", "--connect-type", robot_type]
    if robot_name:
        args_list += ["--connect-name", robot_name]
    args = parser.parse_args(args_list)
    try:
        wwMain.start(delegate, args)
    except Exception as e:
        _safe_status_put(status_q, {"kind": "worker_error", "role": role, "error": str(e)[:300]})
    finally:
        _safe_status_put(status_q, {"kind": "worker_exit", "role": role})


def default_duo_plan() -> Dict[str, Any]:
    """Plan de respaldo cuando no hay agente disponible."""
    return {
        "say": "Plan default: saludo sincronizado y mini baile.",
        "steps": [
            {"at_ms": 0,    "actor": "dash", "action": "lights", "color": "blue"},
            {"at_ms": 0,    "actor": "cue",  "action": "lights", "color": "magenta"},
            {"at_ms": 300,  "actor": "dash", "action": "head",   "pos": "up"},
            {"at_ms": 300,  "actor": "cue",  "action": "head",   "pos": "up"},
            {"at_ms": 900,  "actor": "dash", "action": "move",   "cm": 20},
            {"at_ms": 900,  "actor": "cue",  "action": "move",   "cm": 20},
            {"at_ms": 2200, "actor": "dash", "action": "turn",   "deg": -35},
            {"at_ms": 2200, "actor": "cue",  "action": "turn",   "deg": 35},
            {"at_ms": 3200, "actor": "dash", "action": "spin"},
            {"at_ms": 3200, "actor": "cue",  "action": "spin"},
            {"at_ms": 4700, "actor": "dash", "action": "lights", "color": "green"},
            {"at_ms": 4700, "actor": "cue",  "action": "lights", "color": "green"},
            {"at_ms": 5600, "actor": "dash", "action": "stop"},
            {"at_ms": 5600, "actor": "cue",  "action": "stop"},
            {"at_ms": 6000, "actor": "dash", "action": "lights", "color": "off"},
            {"at_ms": 6000, "actor": "cue",  "action": "lights", "color": "off"},
        ],
    }


def _sanitize_steps(raw_steps: Any) -> List[Dict[str, Any]]:
    clean: List[Dict[str, Any]] = []
    if not isinstance(raw_steps, list):
        return clean

    for st in raw_steps:
        if not isinstance(st, dict):
            continue
        actor = str(st.get("actor", "")).lower().strip()
        action = str(st.get("action", "")).lower().strip()
        if actor not in VALID_ACTORS or action not in VALID_ACTIONS:
            continue

        try:
            at_ms = int(float(st.get("at_ms", 0)))
        except Exception:
            at_ms = 0
        at_ms = max(0, min(180000, at_ms))

        item: Dict[str, Any] = {"at_ms": at_ms, "actor": actor, "action": action}

        if action == "lights":
            item["color"] = str(st.get("color", "white")).lower()
        elif action == "move":
            try:
                item["cm"] = max(-200.0, min(200.0, float(st.get("cm", 25))))
            except Exception:
                item["cm"] = 25.0
            try:
                item["speed"] = max(8.0, min(120.0, float(st.get("speed", 35))))
            except Exception:
                item["speed"] = 35.0
        elif action == "turn":
            try:
                item["deg"] = max(-360.0, min(360.0, float(st.get("deg", 90))))
            except Exception:
                item["deg"] = 90.0
            try:
                item["speed"] = max(20.0, min(180.0, float(st.get("speed", 90))))
            except Exception:
                item["speed"] = 90.0
        elif action == "head":
            pos = str(st.get("pos", "center")).lower()
            item["pos"] = pos if pos in {"up", "down", "center"} else "center"
        elif action == "sound":
            try:
                slot = int(st.get("slot", 1))
            except Exception:
                slot = 1
            item["slot"] = max(1, min(10, slot))

        clean.append(item)

    clean.sort(key=lambda x: x["at_ms"])
    return clean


def plan_from_agent(prompt: str, model: str | None = None) -> Dict[str, Any]:
    """
    Genera plan dual con OpenAI. Si falla, lanza excepcion para que caller use fallback.
    """
    project_root = Path(__file__).resolve().parents[1]
    env_path = project_root / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(str(env_path))
        except Exception:
            pass

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada.")

    from openai import OpenAI
    client = OpenAI()
    chosen_model = model or os.environ.get("OPENAI_MODEL_DUO", "gpt-4o-mini")

    system_prompt = """\
Devuelves SOLO JSON valido con esta forma exacta:
{
  "say": "frase corta en espanol",
  "steps": [
    {"at_ms": 0, "actor":"dash|cue", "action":"lights|move|turn|head|sound|spin|stop", ...}
  ]
}

Reglas:
- La rutina es para DOS robots al mismo tiempo: Dash y Cue.
- 'at_ms' es tiempo absoluto desde inicio.
- Mantener segura la distancia: movimientos cortos (move <= 40cm por paso).
- Duracion total 6 a 14 segundos.
- Debe haber acciones de ambos robots en paralelo (mismos tiempos en varios pasos).
- Parametros por accion:
  - lights: {"color":"red|green|blue|yellow|magenta|cyan|white|off"}
  - move: {"cm": -40..40, "speed": 8..80}
  - turn: {"deg": -180..180, "speed": 20..150}
  - head: {"pos":"up|down|center"}
  - sound: {"slot":1..10}
  - spin: sin parametros
  - stop: sin parametros
- No agregues texto fuera del JSON.
"""

    resp = client.chat.completions.create(
        model=chosen_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt or "haz que Dash y Cue se saluden y bailen juntos"},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
        max_tokens=700,
    )

    data = json.loads(resp.choices[0].message.content or "{}")
    steps = _sanitize_steps(data.get("steps"))
    if not steps:
        raise RuntimeError("El agente devolvio plan vacio.")
    return {"say": str(data.get("say", "")).strip(), "steps": steps}


def _print_status(msg: Dict[str, Any]) -> None:
    kind = msg.get("kind", "")
    role = msg.get("role", "?")
    if kind == "connected":
        print("[OK] %-4s conectado: %s (%s)" % (role, msg.get("name"), msg.get("type")))
    elif kind == "worker_error":
        print("[ERR] %-4s %s" % (role, msg.get("error")))
    elif kind == "step_error":
        print("[ERR] %-4s paso '%s': %s" % (role, msg.get("action"), msg.get("error")))
    elif kind == "worker_exit":
        print("[END] %-4s proceso terminado" % role)


def wait_two_connected(status_q: mp.Queue, procs: Dict[str, mp.Process], timeout_s: int) -> Dict[str, Dict[str, Any]]:
    connected: Dict[str, Dict[str, Any]] = {}
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for role, proc in procs.items():
            if role not in connected and not proc.is_alive():
                raise RuntimeError("El proceso de %s termino antes de conectar." % role)

        try:
            msg = status_q.get(timeout=0.25)
        except queue.Empty:
            continue

        if isinstance(msg, dict):
            _print_status(msg)
            if msg.get("kind") == "connected":
                connected[str(msg.get("role"))] = msg
                if len(connected) == 2:
                    return connected

    raise TimeoutError("Timeout esperando conexion de ambos robots (%ss)." % timeout_s)


def run_synchronized_plan(steps: List[Dict[str, Any]], cmd_dash: mp.Queue, cmd_cue: mp.Queue):
    send_q = {"dash": cmd_dash, "cue": cmd_cue}
    t0 = time.monotonic()
    for st in sorted(steps, key=lambda s: s["at_ms"]):
        target_t = t0 + (float(st["at_ms"]) / 1000.0)
        while True:
            remain = target_t - time.monotonic()
            if remain <= 0:
                break
            time.sleep(min(remain, 0.02))
        actor = st["actor"]
        send_q[actor].put(st)


def shutdown_workers(cmd_dash: mp.Queue, cmd_cue: mp.Queue, procs: Dict[str, mp.Process]):
    for q in (cmd_dash, cmd_cue):
        try:
            q.put({"action": "shutdown"})
        except Exception:
            pass
    for role, p in procs.items():
        p.join(timeout=5.0)
        if p.is_alive():
            print("[WARN] Terminando proceso %s por timeout..." % role)
            p.terminate()
            p.join(timeout=2.0)


def _build_plan_from_args(args) -> Dict[str, Any]:
    if args.sin_agente:
        plan = default_duo_plan()
        print("[Plan] usando rutina default (sin agente).")
    else:
        try:
            plan = plan_from_agent(args.prompt, model=args.model)
            print("[Plan] generado por agente IA.")
        except Exception as e:
            print("[WARN] agente no disponible, usando fallback: %s" % str(e)[:200])
            plan = default_duo_plan()

    steps = _sanitize_steps(plan.get("steps"))
    if not steps:
        steps = default_duo_plan()["steps"]
    if plan.get("say"):
        print("[Agent] %s" % plan["say"])
    print("[Plan] pasos:", len(steps))
    return {"say": plan.get("say", ""), "steps": steps}


class PrintStatusQueue:
    """Compatible con _safe_status_put: imprime eventos en vez de encolarlos."""

    def put(self, payload):
        if isinstance(payload, dict):
            _print_status(payload)


class ScriptWorkerDelegate(DualWorkerDelegate):
    """Worker local (subproceso) que usa una cola normal en memoria."""

    def __init__(self, role: str, cmd_q: queue.Queue, status_q: Any):
        super().__init__(role, cmd_q, status_q)
        self.connected_evt = threading.Event()

    def on_connect(self, robot):
        self.connected_evt.set()
        super().on_connect(robot)


def _load_worker_steps(path: str, role: str) -> List[Dict[str, Any]]:
    if not path:
        return []
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    steps = data.get(role, [])
    return _sanitize_steps(steps)


def worker_mode_main(args) -> int:
    """Modo worker: conecta un robot y ejecuta su lista de pasos programados."""
    role = str(args.worker_role or "").lower()
    if role not in VALID_ACTORS:
        print("[ERR] worker-role inválido:", role)
        return 2

    steps = _load_worker_steps(args.steps_file, role)
    if not steps:
        print("[WARN] %s sin pasos válidos para ejecutar." % role)

    cmd_q: queue.Queue = queue.Queue()
    status_q = PrintStatusQueue()
    delegate = ScriptWorkerDelegate(role, cmd_q, status_q)

    parser = argparse.ArgumentParser()
    mgr.WWBTLEManager.setup_argument_parser(parser)
    args_list = ["--connect-eager", "--connect-type", role]
    if args.robot_name:
        args_list += ["--connect-name", args.robot_name]
    bt_args = parser.parse_args(args_list)

    def scheduler():
        # Espera la conexión para alinear tiempos absolutos del plan.
        if not delegate.connected_evt.wait(timeout=max(5, int(args.timeout or BT_TIMEOUT_S))):
            print("[ERR] %-4s timeout esperando conexión (%ss)." % (role, args.timeout))
            try:
                mgr.WWBTLEManager.stop()
            except Exception:
                pass
            return

        t0 = time.monotonic()
        for st in sorted(steps, key=lambda s: s["at_ms"]):
            target_t = t0 + (float(st["at_ms"]) / 1000.0)
            while True:
                remain = target_t - time.monotonic()
                if remain <= 0:
                    break
                time.sleep(min(remain, 0.02))
            try:
                cmd_q.put(st)
            except Exception:
                break

        time.sleep(0.8)
        try:
            cmd_q.put({"action": "shutdown"})
        except Exception:
            pass

    threading.Thread(target=scheduler, daemon=True).start()

    try:
        wwMain.start(delegate, bt_args)
    except Exception as e:
        print("[ERR] %-4s worker fallo: %s" % (role, str(e)[:300]))
        return 1

    return 0 if delegate.connected_evt.is_set() else 2


def run_duo_subprocess_fallback(args) -> int:
    """
    Fallback para entornos donde multiprocessing.Queue falla (WinError 5).
    Lanza 2 subprocesos de este mismo script en modo worker (dash y cue).
    """
    print("[WARN] multiprocessing.Queue no disponible; usando fallback por subprocesos.")
    plan = _build_plan_from_args(args)
    steps = plan["steps"]
    split = {"dash": [], "cue": []}
    for st in steps:
        actor = st.get("actor")
        if actor in split:
            split[actor].append(st)

    tmp_file = Path(tempfile.gettempdir()) / (
        "duo_steps_%d_%d.json" % (os.getpid(), int(time.time() * 1000))
    )
    tmp_file.write_text(json.dumps(split, ensure_ascii=False), encoding="utf-8")

    procs: Dict[str, subprocess.Popen] = {}
    script_path = str(Path(__file__).resolve())
    cwd = str(Path(__file__).resolve().parents[1])
    rc = 0
    try:
        for role in ("dash", "cue"):
            cmd = [
                sys.executable,
                script_path,
                "--worker-role",
                role,
                "--steps-file",
                str(tmp_file),
                "--timeout",
                str(max(10, int(args.timeout or BT_TIMEOUT_S))),
            ]
            if role == "dash" and args.dash_name:
                cmd += ["--robot-name", args.dash_name]
            if role == "cue" and args.cue_name:
                cmd += ["--robot-name", args.cue_name]
            print("[RUN] %s worker:" % role, " ".join(cmd))
            procs[role] = subprocess.Popen(cmd, cwd=cwd)

        for role, proc in procs.items():
            code = proc.wait()
            print("[END] %-4s rc=%s" % (role, code))
            if code != 0:
                rc = 1
    finally:
        for role, proc in procs.items():
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        try:
            tmp_file.unlink()
        except Exception:
            pass
    return rc


def parse_args():
    p = argparse.ArgumentParser(description="Rutina dual Dash+Cue con agente")
    p.add_argument("--dash-name", help="nombre exacto del Dash (opcional)")
    p.add_argument("--cue-name", help="nombre exacto del Cue (opcional)")
    p.add_argument("--prompt", default="haz que Dash y Cue se saluden y celebren juntos",
                   help="orden para el agente")
    p.add_argument("--model", help="modelo OpenAI para plan dual (opcional)")
    p.add_argument("--sin-agente", action="store_true",
                   help="usar rutina default sin llamar IA")
    p.add_argument("--timeout", type=int, default=BT_TIMEOUT_S,
                   help="timeout de conexion BLE para ambos robots")
    # Modo interno worker (usado por fallback de subprocesos)
    p.add_argument("--worker-role", choices=["dash", "cue"], help=argparse.SUPPRESS)
    p.add_argument("--steps-file", help=argparse.SUPPRESS)
    p.add_argument("--robot-name", help=argparse.SUPPRESS)
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # Worker interno del fallback (1 robot por proceso).
    if args.worker_role:
        return worker_mode_main(args)

    # Cola de comandos por robot + cola de estado compartida.
    try:
        cmd_dash: mp.Queue = mp.Queue()
        cmd_cue: mp.Queue = mp.Queue()
        status_q: mp.Queue = mp.Queue()
    except PermissionError as e:
        print("[WARN] multiprocessing no disponible (%s)." % str(e)[:220])
        return run_duo_subprocess_fallback(args)

    procs = {
        "dash": mp.Process(
            target=worker_main,
            args=("dash", "dash", args.dash_name, cmd_dash, status_q),
            daemon=True,
        ),
        "cue": mp.Process(
            target=worker_main,
            args=("cue", "cue", args.cue_name, cmd_cue, status_q),
            daemon=True,
        ),
    }

    print("=" * 64)
    print(" Rutina Duo Agente: conectando Dash y Cue en paralelo...")
    print("=" * 64)
    for p in procs.values():
        p.start()

    rc = 0
    try:
        connected = wait_two_connected(status_q, procs, timeout_s=args.timeout)
        print("-" * 64)
        print("Conectados:")
        print("  Dash -> %s" % connected["dash"]["name"])
        print("  Cue  -> %s" % connected["cue"]["name"])
        print("-" * 64)

        plan = _build_plan_from_args(args)
        steps = plan["steps"]

        run_synchronized_plan(steps, cmd_dash, cmd_cue)
        time.sleep(1.2)
    except KeyboardInterrupt:
        print("\n[INFO] cancelado por usuario.")
        rc = 130
    except Exception as e:
        print("[ERR] rutina dual falló:", str(e)[:240])
        rc = 1
    finally:
        shutdown_workers(cmd_dash, cmd_cue, procs)
        print("[INFO] rutina finalizada.")
    return rc


if __name__ == "__main__":
    # En Windows el start method por defecto ya es "spawn".
    raise SystemExit(main())
