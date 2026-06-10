# -*- coding: utf-8 -*-
"""
app_web.py  -  Servidor web para controlar Dash / Dot / Cue desde el navegador.

Arquitectura:
    [Navegador / celular]  --HTTP/WiFi-->  [este servidor Python]  --Bluetooth-->  Robot

El servidor corre en el PC que tiene el Bluetooth y el robot. Cualquier dispositivo
en la misma red WiFi abre la pagina y maneja al robot. El microfono lo aporta el
navegador (Web Speech API, en espanol).

Ejecutar:
    python app_web.py                 # http://localhost:5000  (y http://IP-DEL-PC:5000)
    python app_web.py --type cue
    python app_web.py --name Fede
    python app_web.py --port 8080
    python app_web.py --https         # https con certificado temporal (mic en el celular)

Notas:
    - El microfono del navegador solo funciona en "localhost" o por HTTPS. Si vas a
      usar la voz desde el CELULAR, arranca con --https y acepta el aviso de seguridad.
    - La conduccion, grabacion y rutas funcionan por HTTP normal en cualquier dispositivo.
"""

import os
import sys
import argparse
import threading
import subprocess
import time
import ipaddress
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory

import robot_core as core

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web")
AUDIO_DIR = os.path.join(BASE_DIR, "audios")
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a")
DUO_SCRIPT = os.path.join(BASE_DIR, "rutinas", "rutina_duo_agente.py")
DUO_LOG = os.path.join(BASE_DIR, "duo_rutina.log")

app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
ctl = None  # se crea en main()
duo_proc = None
duo_lock = threading.Lock()
duo_started_at = None
duo_last_exit = None
duo_last_cmd = []
SEC_API_KEY = ""
SEC_ALLOW_PUBLIC = False
SEC_TRUST_PROXY = False
SEC_ALLOW_NETS = []


# ------------------------------- pagina ------------------------------- #
@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/explorer")
def explorer_page():
    return send_from_directory(WEB_DIR, "explorer.html")


@app.get("/healthz")
def healthz():
    """Healthcheck rapido para supervision."""
    if ctl is None:
        return jsonify(ok=True, state="booting", connected=False, ts=_iso_now())
    st = ctl.status()
    return jsonify(
        ok=True,
        state=st.get("state"),
        connected=bool(st.get("connected")),
        ts=_iso_now(),
    )


def _iso_now():
    return datetime.now().isoformat(timespec="seconds")


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v or "").strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def _read_tail(path, max_lines=90, max_chars=12000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = "".join(f.readlines()[-max_lines:])
            if len(text) > max_chars:
                text = text[-max_chars:]
            return text
    except OSError:
        return ""


def _setup_logging():
    """Activa log rotativo en server.log para no crecer infinito."""
    try:
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
            log_path = os.path.join(BASE_DIR, "server.log")
            fh = RotatingFileHandler(
                log_path,
                maxBytes=2_000_000,
                backupCount=4,
                encoding="utf-8",
            )
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            root.addHandler(fh)
        app.logger.propagate = True
    except Exception as e:
        print("[WARN] logging rotativo no activado:", e)


def _float_in_range(raw, default=0.0, min_v=None, max_v=None):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(default)
    if min_v is not None:
        v = max(float(min_v), v)
    if max_v is not None:
        v = min(float(max_v), v)
    return v


def _client_ip():
    if SEC_TRUST_PROXY:
        xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if xff:
            return xff
    return request.remote_addr or ""


def _client_ip_allowed(ip_text):
    try:
        ip_obj = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    if ip_obj.is_loopback:
        return True
    if SEC_ALLOW_PUBLIC:
        return True
    if SEC_ALLOW_NETS:
        return any(ip_obj in n for n in SEC_ALLOW_NETS)
    return ip_obj.is_private


@app.before_request
def _guard_api():
    path = request.path or ""
    if not path.startswith("/api/"):
        return None
    ip_txt = _client_ip()
    if not _client_ip_allowed(ip_txt):
        return jsonify(ok=False, error="IP no permitida para este servidor."), 403
    if SEC_API_KEY:
        provided = (
            request.headers.get("X-API-Key")
            or request.args.get("api_key")
            or request.args.get("k")
            or ""
        )
        if provided != SEC_API_KEY:
            return jsonify(ok=False, error="API key invalida o ausente."), 401
    return None


@app.after_request
def _resp_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    if (request.path or "").startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _resume_single_controller():
    if ctl is None:
        return
    try:
        ctl.reconnect()
    except Exception:
        try:
            ctl.start_ble()
        except Exception:
            pass


def _duo_cleanup_if_finished():
    global duo_proc, duo_started_at, duo_last_exit
    must_resume = False
    with duo_lock:
        if duo_proc is None:
            return
        code = duo_proc.poll()
        if code is None:
            return
        duo_last_exit = {"code": int(code), "at": _iso_now()}
        duo_proc = None
        duo_started_at = None
        must_resume = True
    if must_resume:
        _resume_single_controller()


def _duo_status(include_log=False):
    _duo_cleanup_if_finished()
    with duo_lock:
        running = duo_proc is not None and duo_proc.poll() is None
        data = {
            "running": running,
            "pid": duo_proc.pid if running else None,
            "started_at": duo_started_at,
            "last_exit": dict(duo_last_exit) if duo_last_exit else None,
            "command": list(duo_last_cmd),
        }
    if include_log:
        data["log_tail"] = _read_tail(DUO_LOG)
    return data


# ------------------------------- API ------------------------------- #
@app.get("/api/status")
def api_status():
    s = ctl.status()
    s["duo"] = _duo_status(include_log=False)
    return jsonify(s)


@app.post("/api/drive")
def api_drive():
    if ctl.avoiding:           # bloqueado: el robot está frenando/esquivando un obstáculo
        return jsonify(ok=True, blocked=True)
    d = request.get_json(force=True, silent=True) or {}
    linear = _float_in_range(d.get("linear", 0), 0.0, -80.0, 80.0)
    angular = _float_in_range(d.get("angular", 0), 0.0, -220.0, 220.0)
    ctl.bridge.drive(linear, angular)
    return jsonify(ok=True, linear=linear, angular=angular)


@app.post("/api/stop")
def api_stop():
    ctl.motion.stop_now()
    return jsonify(ok=True)


@app.post("/api/reconnect")
def api_reconnect():
    """Reconexión forzada: corta lo actual (aunque parezca conectado) y reescanea."""
    state = ctl.reconnect()
    return jsonify(ok=True, state=state)


@app.post("/api/avoid")
def api_avoid():
    """Activa/desactiva el modo de evitar obstáculos con los sensores."""
    d = request.get_json(force=True, silent=True) or {}
    on = ctl.set_avoid(bool(d.get("on", False)))
    return jsonify(ok=True, avoid=on)


@app.post("/api/select_robot")
def api_select_robot():
    """Elige el robot (dash/cue/any) y reconecta a ese tipo."""
    d = request.get_json(force=True, silent=True) or {}
    pref = ctl.set_robot_type(d.get("type", ""))
    return jsonify(ok=True, type=pref)


@app.post("/api/explore")
def api_explore():
    """Modo explorador autónomo: avanza solo evitando obstáculos."""
    d = request.get_json(force=True, silent=True) or {}
    if d.get("on") and not ctl.bridge.connected:
        return jsonify(ok=False, error="Conecta el robot primero.")
    on = ctl.set_explore(bool(d.get("on", False)))
    return jsonify(ok=True, explore=on)


@app.get("/api/explore/status")
def api_explore_status():
    return jsonify(
        ok=True,
        running=bool(ctl.explore_enabled),
        profile=ctl.explore_profile,
        learning=ctl.explore_snapshot(),
    )


@app.post("/api/explore/reset")
def api_explore_reset():
    d = request.get_json(force=True, silent=True) or {}
    snap = ctl.reset_explore_learning(clear_map=bool(d.get("clear_map", False)))
    return jsonify(ok=True, learning=snap)


@app.post("/api/explore/profile")
def api_explore_profile():
    d = request.get_json(force=True, silent=True) or {}
    profile = ctl.set_explore_profile(d.get("mode", "normal"))
    return jsonify(ok=True, profile=profile)


@app.post("/api/agent")
def api_agent():
    """Recibe una orden en lenguaje natural, la interpreta con GPT y la ejecuta."""
    d = request.get_json(force=True, silent=True) or {}
    text = (d.get("text") or "").strip()
    if not text:
        return jsonify(ok=False, error="Dime algo para hacer.")
    duo = _duo_status(include_log=False)
    if duo.get("running"):
        return jsonify(ok=False, error="La rutina dúo está activa. Deténla antes de usar IA individual.")
    if not ctl.bridge.connected:
        return jsonify(ok=False, error="Conecta un robot primero (Dash, Cue o cualquiera).")
    if ctl.agent_busy:
        return jsonify(ok=False, error="La IA ya está ejecutando una rutina.")
    try:
        import agent
        plan = agent.plan_from_text(text)
    except Exception as e:
        return jsonify(ok=False, error="Agente IA: %s" % str(e)[:200])
    started = ctl.run_plan_async(plan["steps"])
    if not started:
        return jsonify(ok=False, error="No se pudo iniciar la rutina IA (¿robot conectado u ocupado?).")
    return jsonify(ok=True, say=plan["say"], steps=plan["steps"], running=started)


@app.post("/api/agent/stop")
def api_agent_stop():
    ctl.stop_agent()
    ctl.motion.stop_now()
    return jsonify(ok=True)


@app.get("/api/duo/status")
def api_duo_status():
    return jsonify(ok=True, duo=_duo_status(include_log=True))


@app.post("/api/duo/start")
def api_duo_start():
    """Arranca la rutina dual Dash+Cue en un proceso aparte."""
    global duo_proc, duo_started_at, duo_last_exit, duo_last_cmd
    if not os.path.isfile(DUO_SCRIPT):
        return jsonify(ok=False, error="No existe rutina_duo_agente.py"), 404

    d = request.get_json(force=True, silent=True) or {}
    prompt = (d.get("prompt") or "").strip()
    dash_name = (d.get("dash_name") or "").strip()
    cue_name = (d.get("cue_name") or "").strip()
    model = (d.get("model") or "").strip()
    sin_agente = _as_bool(d.get("sin_agente", False))

    timeout_arg = None
    timeout_raw = d.get("timeout")
    if timeout_raw not in (None, ""):
        try:
            timeout_arg = max(10, min(120, int(timeout_raw)))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="timeout debe ser un número entre 10 y 120."), 400

    _duo_cleanup_if_finished()
    with duo_lock:
        if duo_proc is not None and duo_proc.poll() is None:
            return jsonify(ok=False, error="La rutina duo ya está en ejecución."), 409

    # liberamos la conexión BLE del controlador "normal" antes de abrir 2 procesos en paralelo
    try:
        ctl.stop_agent()
        ctl.stop_replay()
        ctl.set_explore(False)
        ctl.motion.stop_now()
    except Exception:
        pass
    try:
        ctl.shutdown()
    except Exception:
        pass
    time.sleep(0.25)

    cmd = [sys.executable, DUO_SCRIPT]
    if dash_name:
        cmd += ["--dash-name", dash_name]
    if cue_name:
        cmd += ["--cue-name", cue_name]
    if prompt:
        cmd += ["--prompt", prompt]
    if model:
        cmd += ["--model", model]
    if sin_agente:
        cmd += ["--sin-agente"]
    if timeout_arg is not None:
        cmd += ["--timeout", str(timeout_arg)]

    with duo_lock:
        if duo_proc is not None and duo_proc.poll() is None:
            return jsonify(ok=False, error="La rutina duo ya está en ejecución."), 409
        try:
            log_fp = open(DUO_LOG, "w", encoding="utf-8")
        except OSError as e:
            return jsonify(ok=False, error="No se pudo abrir log duo: %s" % str(e)[:200]), 500
        try:
            duo_proc = subprocess.Popen(
                cmd,
                cwd=BASE_DIR,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            log_fp.close()
            return jsonify(ok=False, error="No se pudo iniciar rutina duo: %s" % str(e)[:200]), 500
        finally:
            try:
                log_fp.close()
            except Exception:
                pass
        duo_started_at = _iso_now()
        duo_last_exit = None
        duo_last_cmd = list(cmd)
    return jsonify(ok=True, duo=_duo_status(include_log=True))


@app.post("/api/duo/stop")
def api_duo_stop():
    """Detiene la rutina dual y vuelve al modo de control web normal."""
    global duo_proc, duo_started_at, duo_last_exit
    _duo_cleanup_if_finished()
    with duo_lock:
        proc = duo_proc
    if proc is None or proc.poll() is not None:
        return jsonify(ok=True, stopped=False, duo=_duo_status(include_log=True))

    code = None
    try:
        proc.terminate()
        proc.wait(timeout=5.0)
        code = proc.poll()
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except Exception:
            pass
        code = proc.poll()
    except Exception:
        code = proc.poll()

    with duo_lock:
        if duo_proc is proc:
            duo_proc = None
            duo_started_at = None
            duo_last_exit = {"code": int(code if code is not None else -1), "at": _iso_now()}
    _resume_single_controller()
    return jsonify(ok=True, stopped=True, duo=_duo_status(include_log=True))


@app.post("/api/sound")
def api_sound():
    """Reproduce un clip por el parlante del robot. {slot:1..10} = voz grabada, o {name:...}."""
    d = request.get_json(force=True, silent=True) or {}
    name = d.get("name")
    slot = d.get("slot")
    if slot is not None:
        try:
            n = int(slot)
            if 1 <= n <= 10:
                name = "SYSTVOICE%d" % (n - 1)
        except (TypeError, ValueError):
            name = None
    if not name:
        return jsonify(ok=False, error="Falta 'slot' (1-10) o 'name'.")
    if not ctl.bridge.connected:
        return jsonify(ok=False, error="Conecta el robot primero.")
    ctl.bridge.play_sound(name)
    return jsonify(ok=True, name=name)


@app.get("/api/audios")
def api_audios():
    """Lista los archivos de audio que el usuario puso en la carpeta 'audios'."""
    files = []
    if os.path.isdir(AUDIO_DIR):
        for f in sorted(os.listdir(AUDIO_DIR)):
            if f.lower().endswith(AUDIO_EXTS):
                files.append(f)
    return jsonify(files)


@app.get("/audios/<path:filename>")
def serve_audio(filename):
    """Sirve un archivo de audio de la carpeta 'audios' (para reproducir en el navegador)."""
    return send_from_directory(AUDIO_DIR, filename)


@app.get("/api/track")
def api_track():
    """Traza del recorrido + mapa de calor + pose actual (para el mapa)."""
    return jsonify(ctl.track_snapshot())


@app.post("/api/track/clear")
def api_track_clear():
    ctl.clear_track()
    return jsonify(ok=True)


@app.post("/api/head")
def api_head():
    d = request.get_json(force=True, silent=True) or {}
    pan = _float_in_range(d.get("pan", 0), 0.0, -120.0, 120.0)
    tilt = _float_in_range(d.get("tilt", 0), 0.0, -10.0, 22.0)
    ctl.bridge.head(pan, tilt)
    return jsonify(ok=True, pan=pan, tilt=tilt)


@app.post("/api/lights")
def api_lights():
    d = request.get_json(force=True, silent=True) or {}
    r = _float_in_range(d.get("r", 0), 0.0, 0.0, 1.0)
    g = _float_in_range(d.get("g", 0), 0.0, 0.0, 1.0)
    b = _float_in_range(d.get("b", 0), 0.0, 0.0, 1.0)
    ctl.bridge.lights(r, g, b)
    return jsonify(ok=True, r=r, g=g, b=b)


@app.post("/api/voice")
def api_voice():
    d = request.get_json(force=True, silent=True) or {}
    action = ctl.voice(d.get("text", ""),
                       float(d.get("linear", 30)), float(d.get("turn", 90)))
    return jsonify(ok=True, text=d.get("text", ""), action=action)


# --- grabacion ---
@app.post("/api/record/start")
def api_rec_start():
    if not ctl.bridge.connected:
        return jsonify(ok=False, error="Conecta el robot primero.")
    ctl.recorder.start()
    return jsonify(ok=True)


@app.post("/api/record/stop")
def api_rec_stop():
    d = request.get_json(force=True, silent=True) or {}
    events, duration = ctl.recorder.stop()
    if not events:
        return jsonify(ok=False, error="No se grabó ningún movimiento.")
    name = (d.get("name") or "").strip() or datetime.now().strftime("Ruta %Y-%m-%d %H:%M")
    rid = core.db_save_route(name, ctl.bridge.robot_type_name, events, duration)
    return jsonify(ok=True, id=rid, name=name, steps=len(events), dur_ms=duration)


# --- rutas ---
@app.get("/api/routes")
def api_routes():
    rows = core.db_list_routes()
    return jsonify([
        {"id": r[0], "name": r[1], "created": r[2].replace("T", " "),
         "dur_ms": r[3], "type": r[4], "steps": r[5]}
        for r in rows
    ])


@app.post("/api/routes/<int:rid>/play")
def api_route_play(rid):
    ok = ctl.play_route(rid)
    return jsonify(ok=ok, error=None if ok else "No se pudo reproducir (¿robot conectado?, ¿ya reproduciendo?).")


@app.post("/api/replay/stop")
def api_replay_stop():
    ctl.stop_replay()
    return jsonify(ok=True)


@app.delete("/api/routes/<int:rid>")
def api_route_delete(rid):
    core.db_delete_route(rid)
    return jsonify(ok=True)


# ------------------------------- arranque ------------------------------- #
def main():
    global ctl, SEC_API_KEY, SEC_ALLOW_PUBLIC, SEC_TRUST_PROXY, SEC_ALLOW_NETS
    p = argparse.ArgumentParser(description="Servidor web de control del robot")
    p.add_argument("--type", choices=["dash", "dot", "cue"], help="solo este tipo")
    p.add_argument("--name", help="solo un robot con este nombre")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--prod", action="store_true",
                   help="usar Waitress (HTTP) en vez del servidor dev de Flask")
    p.add_argument("--threads", type=int, default=8,
                   help="hilos para Waitress en modo --prod")
    p.add_argument("--api-key", default=os.environ.get("ROBOT_API_KEY", ""),
                   help="si se define, exige X-API-Key en todas las rutas /api")
    p.add_argument("--allow-public", action="store_true",
                   help="permitir acceso /api desde IP publica (NO recomendado)")
    p.add_argument("--allow-net", action="append", default=[],
                   help="subred permitida CIDR (ej: --allow-net 10.8.0.0/24). Repetible.")
    p.add_argument("--trust-proxy", action="store_true",
                   help="usar X-Forwarded-For como IP cliente (solo detras de proxy confiable)")
    p.add_argument("--https", action="store_true",
                   help="HTTPS con certificado temporal (necesario para el micrófono en el celular)")
    a = p.parse_args()

    _setup_logging()
    SEC_API_KEY = (a.api_key or "").strip()
    SEC_ALLOW_PUBLIC = bool(a.allow_public)
    SEC_TRUST_PROXY = bool(a.trust_proxy)
    SEC_ALLOW_NETS = []
    for raw in a.allow_net:
        try:
            SEC_ALLOW_NETS.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            print("[WARN] subred invalida ignorada:", raw)

    connect = ["--connect-eager"]
    if a.type:
        connect += ["--connect-type", a.type]
    if a.name:
        connect += ["--connect-name", a.name]

    core.db_init()
    ctl = core.Controller(connect)
    ctl.start_ble()

    ssl_context = "adhoc" if a.https else None
    scheme = "https" if a.https else "http"
    print("=" * 60)
    print(" Control del robot - servidor web")
    print(" Abre en este PC:     %s://localhost:%d" % (scheme, a.port))
    print(" Desde el celular:    %s://IP-DE-ESTE-PC:%d   (misma WiFi)" % (scheme, a.port))
    print(" Modo servidor:       %s" % ("Waitress (--prod)" if a.prod and not a.https else "Flask"))
    print(" API key:             %s" % ("ACTIVA" if SEC_API_KEY else "desactivada"))
    if SEC_ALLOW_PUBLIC:
        print(" Red /api:            acceso publico habilitado (no recomendado)")
    elif SEC_ALLOW_NETS:
        print(" Red /api:            subredes permitidas: %s" %
              ", ".join(str(n) for n in SEC_ALLOW_NETS))
    else:
        print(" Red /api:            solo localhost y red privada")
    if not a.https:
        print(" (Para usar el micrófono desde el celular, arranca con --https)")
    print("=" * 60)

    if a.prod and not a.https:
        try:
            from waitress import serve
        except Exception:
            print("[WARN] waitress no esta instalada. Usa: pip install waitress")
            print("[WARN] arrancando con Flask de forma temporal.")
        else:
            serve(app, host=a.host, port=a.port, threads=max(2, int(a.threads)))
            return
    elif a.prod and a.https:
        print("[WARN] --prod no soporta --https adhoc. Se usara Flask con HTTPS temporal.")

    app.run(host=a.host, port=a.port, threaded=True, use_reloader=False, ssl_context=ssl_context)


if __name__ == "__main__":
    main()
