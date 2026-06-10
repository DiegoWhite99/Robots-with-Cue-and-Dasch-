"""
Experimento Dash - Movimiento básico.
Ejecutar: python dash/mover.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.base_robot import BaseRobotDelegate, run


class DashMover(BaseRobotDelegate):

    def setup(self, robot):
        print("Dash conectado. Ejecutando secuencia de movimiento...")
        # do_forward no debe correrse dentro de on_connect; se lanza en un hilo aparte.
        threading.Thread(target=self._run_once, args=(robot,), daemon=True).start()

    def _run_once(self, robot):
        robot.cmds.body.do_forward(30, 50)

    def loop(self, robot):
        # Imprimir posición en cada ciclo
        pose = robot.sensors.pose
        if pose.valid:
            print(f"Pose -> x: {pose.x:.2f}  y: {pose.y:.2f}  heading: {pose.degrees:.1f}°")


if __name__ == '__main__':
    run(DashMover(), connect_type='dash')
