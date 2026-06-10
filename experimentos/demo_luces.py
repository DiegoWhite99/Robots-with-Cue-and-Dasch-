"""
DEMO 3 - Luces RGB
Muestra: oreja izquierda, oreja derecha, frente, top (Cue) y efectos combinados.
Valores RGB van de 0.0 a 1.0
Ejecutar: python demo_luces.py
"""
import threading, time, argparse
import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr


def envia(robot, r, g, b, zona='all'):
    rgb = robot.cmds.RGB
    if zona == 'all':
        rgb.stage_all(r, g, b)
    elif zona == 'left':
        rgb.stage_ear_left(r, g, b)
    elif zona == 'right':
        rgb.stage_ear_right(r, g, b)
    elif zona == 'front':
        rgb.stage_front(r, g, b)
    elif zona == 'top':
        rgb.stage_top(r, g, b)
    robot.send_staged()


class DemoLuces:

    def on_connect(self, robot):
        print(f"Conectado: {robot.name} ({robot.robot_type_name})")
        threading.Thread(target=self._run, args=(robot,), daemon=True).start()

    def on_sensors(self, robot):
        pass

    def _run(self, robot):

        print(">> Rojo en todas las luces")
        envia(robot, 1, 0, 0)
        time.sleep(0.8)

        print(">> Verde en todas las luces")
        envia(robot, 0, 1, 0)
        time.sleep(0.8)

        print(">> Azul en todas las luces")
        envia(robot, 0, 0, 1)
        time.sleep(0.8)

        print(">> Colores por zona: oreja izq=rojo, oreja der=azul, frente=verde")
        envia(robot, 1, 0, 0, 'left')
        envia(robot, 0, 0, 1, 'right')
        envia(robot, 0, 1, 0, 'front')
        robot.send_staged()
        time.sleep(1.0)

        print(">> Pulso blanco (fade rápido)")
        for i in range(5):
            for v in [0.2, 0.5, 0.8, 1.0, 0.8, 0.5, 0.2, 0.0]:
                envia(robot, v, v, v)
                time.sleep(0.06)

        print(">> Arcoíris")
        arcoiris = [
            (1.0, 0.0, 0.0),
            (1.0, 0.5, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.5, 1.0),
            (0.0, 0.0, 1.0),
            (0.5, 0.0, 1.0),
        ]
        for _ in range(2):
            for r, g, b in arcoiris:
                envia(robot, r, g, b)
                time.sleep(0.3)

        print(">> Apagar luces")
        envia(robot, 0, 0, 0)
        time.sleep(0.3)

        print("Demo luces completada.")
        mgr.WWBTLEManager.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mgr.WWBTLEManager.setup_argument_parser(parser)
    wwMain.start(DemoLuces(), parser.parse_args(['--connect-eager']))
