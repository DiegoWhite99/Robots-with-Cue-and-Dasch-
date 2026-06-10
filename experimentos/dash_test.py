# -*- coding: utf-8 -*-
"""
PRUEBA AISLADA DEL DASH (sin la app web).
Conecta SOLO a un Dash, cambia colores y lo mueve usando comandos de VELOCIDAD
(stage_linear_angular), que NO dependen de los sensores.

Ejecutar:
    python dash_test.py

Objetivo: comprobar si el Dash conecta y obedece comandos a nivel de librería.
"""
import threading
import time
import argparse

import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr


class DashTest:

    def on_connect(self, robot):
        print("\n==============================")
        print(" CONECTADO: %s  (%s)" % (robot.name, robot.robot_type_name))
        print("==============================\n")
        threading.Thread(target=self._run, args=(robot,), daemon=True).start()

    def on_sensors(self, robot):
        pass

    def _run(self, robot):
        b = robot.cmds.body
        rgb = robot.cmds.RGB

        def luz(r, g, bl, nombre):
            print("  luz:", nombre)
            rgb.stage_all(r, g, bl)
            robot.send_staged()

        def conducir(lin, ang, segundos, msg):
            print("  mover:", msg)
            b.stage_linear_angular(lin, ang)   # velocidad, NO bloquea (no usa sensores)
            robot.send_staged()
            time.sleep(segundos)
            b.stage_stop()
            robot.send_staged()
            time.sleep(0.3)

        try:
            print(">>> Empezando prueba (mira al Dash)...")
            luz(1, 0, 0, "ROJO");   time.sleep(1.0)
            luz(0, 1, 0, "VERDE");  time.sleep(1.0)
            luz(0, 0, 1, "AZUL");   time.sleep(1.0)

            conducir(20, 0,  1.3, "ADELANTE")
            conducir(0, 90,  1.0, "GIRAR izquierda")
            conducir(-20, 0, 1.3, "ATRAS")
            conducir(0, -90, 1.0, "GIRAR derecha")

            luz(1, 1, 0, "AMARILLO"); time.sleep(0.8)
            luz(0, 0, 0, "APAGADO")

            print("\n>>> PRUEBA TERMINADA. Si el Dash se movió y cambió de color, ¡funciona!\n")
        except Exception as e:
            print(">>> ERROR durante la prueba:", e)
        finally:
            mgr.WWBTLEManager.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mgr.WWBTLEManager.setup_argument_parser(parser)
    # solo Dash, conectar apenas lo encuentre
    args = parser.parse_args(["--connect-eager", "--connect-type", "dash"])
    print("Buscando un DASH... (ten encendido SOLO el Dash; apaga el Cue)")
    wwMain.start(DashTest(), args)
