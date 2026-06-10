"""
Experimento Dot - Lectura de sensores (botones, acelerómetro).
Ejecutar: python dot/sensores.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.base_robot import BaseRobotDelegate, run


class DotSensores(BaseRobotDelegate):

    def setup(self, robot):
        print("Dot conectado. Leyendo sensores en tiempo real. Ctrl+C para salir.")

    def loop(self, robot):
        acc  = robot.sensors.accelerometer
        btn1 = robot.sensors.button_1.pressed
        btn2 = robot.sensors.button_2.pressed
        btn3 = robot.sensors.button_3.pressed
        print(
            f"Accel -> x:{acc.x:6.2f}  y:{acc.y:6.2f}  z:{acc.z:6.2f} | "
            f"Botones -> 1:{btn1}  2:{btn2}  3:{btn3}"
        )


if __name__ == '__main__':
    run(DotSensores(), connect_type='dot')
