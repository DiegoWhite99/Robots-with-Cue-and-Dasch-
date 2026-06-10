"""
Secuencia de movimiento para Dash, Dot o Cue.
Ejecutar: python movimiento.py
"""
import sys
import time
import threading
import argparse

import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr


class MovimientoDelegate:

    def on_connect(self, robot):
        print(f"\nConectado a: {robot.name} ({robot.robot_type_name})")
        print("Iniciando secuencia de movimiento...\n")
        # do_forward / do_turn no pueden llamarse desde on_connect: se ejecutan en un hilo aparte
        t = threading.Thread(target=self._secuencia, args=(robot,), daemon=True)
        t.start()

    def on_sensors(self, robot):
        pass

    def _secuencia(self, robot):
        VEL_CM  = 100   # cm/s  (velocidad lineal)
        VEL_DEG = 90   # °/s   (velocidad de giro)

        pasos = [
            ("Adelante 100 cm",      lambda: robot.cmds.body.do_forward( 100, VEL_CM)),
            ("Girar 90° derecha",   lambda: robot.cmds.body.do_turn(   -90, VEL_DEG)),
            ("Adelante 100 cm",      lambda: robot.cmds.body.do_forward( 100, VEL_CM)),
            ("Girar 90° izquierda", lambda: robot.cmds.body.do_turn(    90, VEL_DEG)),
            ("Retroceder 100 cm",    lambda: robot.cmds.body.do_forward(-100, VEL_CM)),
            ("Giro completo 360°",  lambda: robot.cmds.body.do_turn(   360, VEL_DEG)),
            ("Giro completo 180°",  lambda: robot.cmds.body.do_turn(   180, VEL_DEG)),
        ]

        for descripcion, accion in pasos:
            print(f"  -> {descripcion}")
            accion()
            time.sleep(0.3)

        print("\nParando...")
        robot.cmds.body.stage_stop()
        robot.send_staged()
        time.sleep(1)

        print("Secuencia completada. Desconectando...")
        mgr.WWBTLEManager.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Secuencia de movimiento")
    mgr.WWBTLEManager.setup_argument_parser(parser)
    args = parser.parse_args(['--connect-eager'])
    wwMain.start(MovimientoDelegate(), args)
