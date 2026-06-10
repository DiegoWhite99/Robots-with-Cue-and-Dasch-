"""
DEMO 1 - Desplazamiento
Muestra: avance, retroceso, giros y velocidad variable.
Ejecutar: python demo_desplazamiento.py
"""
import threading, time, argparse
import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr


class DemoDesplazamiento:

    def on_connect(self, robot):
        print(f"Conectado: {robot.name} ({robot.robot_type_name})")
        threading.Thread(target=self._run, args=(robot,), daemon=True).start()

    def on_sensors(self, robot):
        pass

    def _run(self, robot):
        b = robot.cmds.body

        print(">> Adelante 60 cm a 60 cm/s")
        b.do_forward(60, 60)
        time.sleep(0.3)

        print(">> Girar 90° derecha")
        b.do_turn(-90, 90)
        time.sleep(0.3)

        print(">> Adelante 60 cm a 100 cm/s (rápido)")
        b.do_forward(60, 100)
        time.sleep(0.3)

        print(">> Girar 180°")
        b.do_turn(180, 90)
        time.sleep(0.3)

        print(">> Retroceder 60 cm")
        b.do_forward(-60, 60)
        time.sleep(0.3)

        print(">> Giro completo 360°")
        b.do_turn(360, 120)
        time.sleep(0.3)

        print(">> Parar")
        b.stage_stop()
        robot.send_staged()
        time.sleep(0.5)

        print("Demo desplazamiento completada.")
        mgr.WWBTLEManager.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mgr.WWBTLEManager.setup_argument_parser(parser)
    wwMain.start(DemoDesplazamiento(), parser.parse_args(['--connect-eager']))
