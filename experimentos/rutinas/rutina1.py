" generamos rutina uno de movimiento en el pasillo del piso 3."

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'WonderPy'))

import threading, time, argparse
import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr

import subprocess, tempfile

MAX_CM             = 800
OBSTACLE_UMBRAL_CM = 35
OBSTACLE_ESPERA_S  = 0.2

# Colores (r, g, b) en rango 0-1
COLOR_ADELANTE  = (0,   1,   0)     # verde
COLOR_GIRO      = (1,   0.5, 0)     # naranja
COLOR_OBSTACULO = (1,   0,   0)     # rojo
COLOR_LISTO     = (0,   0,   1)     # azul
COLOR_APAGADO   = (0,   0,   0)


def hablar(texto):
    def _speak():
        try:
            script = (
                "Add-Type -AssemblyName System.Speech\n"
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                "$s.Rate = -2\n"
                f"$s.Speak('{texto.replace(chr(39), '')}')\n"
            )
            with tempfile.NamedTemporaryFile(mode='w', suffix='.ps1',
                                             delete=False, encoding='utf-8') as f:
                f.write(script)
                fname = f.name
            subprocess.run(['powershell', '-ExecutionPolicy', 'Bypass', '-File', fname],
                           capture_output=True, timeout=15)
            os.unlink(fname)
        except Exception:
            pass
    threading.Thread(target=_speak, daemon=True).start()


class Rutina1:

    def __init__(self):
        # Distancias de avance (cm)
        self.A  = 100 * 10
        self.B2 = 100 * 9
        self.C2 = 100 * 17
        self.D2 = 100 * 9
        self.E2 = 100 * 6

        # Giros (negativo = derecha en WonderPy)
        self.B = -90
        self.C = -90
        self.D = -90
        self.E = -90

        # Velocidades (cm/s)
        self.vel      = 80
        self.vel_giro = 60

        self._obstaculo = False

    # ------------------------------------------------------------------ luces

    def _luz(self, robot, color):
        r, g, b = color
        robot.cmds.RGB.stage_all(r, g, b)
        robot.send_staged()

    def _parpadear(self, robot, color, veces=3):
        r, g, b = color
        for _ in range(veces):
            robot.cmds.RGB.stage_all(r, g, b)
            robot.send_staged()
            time.sleep(0.2)
            robot.cmds.RGB.stage_all(*COLOR_APAGADO)
            robot.send_staged()
            time.sleep(0.2)

    # ------------------------------------------------------------------ sensores

    def _hay_obstaculo(self, robot):
        try:
            s = robot.sensors
            izq = s.distance_front_left_facing.distance_approximate
            der = s.distance_front_right_facing.distance_approximate
            for d in (izq, der):
                if 0 < d < OBSTACLE_UMBRAL_CM:
                    return True
            return False
        except Exception:
            return False

    def _esperar_libre(self, robot):
        while self._hay_obstaculo(robot):
            if not self._obstaculo:
                self._obstaculo = True
                print("!! Obstáculo detectado — esperando camino libre...")
                hablar("Obstacle detected. Waiting for clear path.")
                self._parpadear(robot, COLOR_OBSTACULO, veces=5)
                robot.cmds.body.do_forward(0, self.vel)
            time.sleep(OBSTACLE_ESPERA_S)
        if self._obstaculo:
            self._obstaculo = False
            print(">> Camino libre — reanudando ruta")
            hablar("Path clear. Resuming route.")

    # ------------------------------------------------------------------ movimiento

    def _avanzar(self, robot, dist_cm, anuncio):
        print(f">> {anuncio}")
        hablar(anuncio)
        self._luz(robot, COLOR_ADELANTE)
        b = robot.cmds.body
        restante = dist_cm
        while restante > 0:
            self._esperar_libre(robot)
            self._luz(robot, COLOR_ADELANTE)
            chunk = min(restante, MAX_CM)
            b.do_forward(chunk, self.vel)
            time.sleep(chunk / self.vel + 0.2)
            restante -= chunk

    def _girar(self, robot, angulo, anuncio):
        print(f">> {anuncio}")
        hablar(anuncio)
        self._luz(robot, COLOR_GIRO)
        robot.cmds.body.do_turn(angulo, self.vel_giro)
        time.sleep(abs(angulo) / self.vel_giro * 2 + 0.4)

    # ------------------------------------------------------------------ WonderPy callbacks

    def on_connect(self, robot):
        print(f"Conectado: {robot.name} ({robot.robot_type_name})")
        hablar("Robot connected. Starting routine.")
        threading.Thread(target=self._run, args=(robot,), daemon=True).start()

    def on_sensors(self, robot):
        pass

    # ------------------------------------------------------------------ rutina principal

    def _run(self, robot):
        self._avanzar(robot, self.A,  "Moving forward, ten meters.")

        self._girar(robot,  self.B,   "Turning right.")
        self._avanzar(robot, self.B2, "Moving forward, nine meters.")

        self._girar(robot,  self.C,   "Turning right.")
        self._avanzar(robot, self.C2, "Moving forward, seventeen meters.")

        self._girar(robot,  self.D,   "Turning right.")
        self._avanzar(robot, self.D2, "Moving forward, nine meters.")

        self._girar(robot,  self.E,   "Turning right.")
        self._avanzar(robot, self.E2, "Moving forward, six meters.")

        print(">> Routine complete.")
        hablar("Route complete. Shutting down.")
        self._luz(robot, COLOR_LISTO)
        robot.cmds.body.stage_stop()
        robot.send_staged()
        time.sleep(1)

        self._luz(robot, COLOR_APAGADO)
        mgr.WWBTLEManager.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mgr.WWBTLEManager.setup_argument_parser(parser)
    wwMain.start(Rutina1(), parser.parse_args(['--connect-eager']))
