"""
Experimento Dash - Control de luces RGB.
Ejecutar: python dash/luces.py
"""
import os
import sys
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.base_robot import BaseRobotDelegate, run


class DashLuces(BaseRobotDelegate):

    def setup(self, robot):
        print("Cambiando luces de Dash...")
        robot.cmds.RGB.stage_all(1.0, 0.0, 0.0)   # rojo
        robot.send_staged()
        time.sleep(1)
        robot.cmds.RGB.stage_all(0.0, 1.0, 0.0)   # verde
        robot.send_staged()
        time.sleep(1)
        robot.cmds.RGB.stage_all(0.0, 0.0, 1.0)   # azul
        robot.send_staged()


if __name__ == '__main__':
    run(DashLuces(), connect_type='dash')
