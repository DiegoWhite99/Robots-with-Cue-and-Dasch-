"""
Experimento Dot - Luces RGB y sonido.
Ejecutar: python dot/luces.py
"""
import os
import sys
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.base_robot import BaseRobotDelegate, run


class DotLuces(BaseRobotDelegate):

    def setup(self, robot):
        print("Dot conectado. Ejecutando show de luces...")
        colores = [
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 1.0),
        ]
        for r, g, b in colores:
            robot.cmds.RGB.stage_all(r, g, b)
            robot.send_staged()
            time.sleep(0.8)

        robot.cmds.RGB.stage_all(0, 0, 0)
        robot.send_staged()


if __name__ == '__main__':
    run(DotLuces(), connect_type='dot')
