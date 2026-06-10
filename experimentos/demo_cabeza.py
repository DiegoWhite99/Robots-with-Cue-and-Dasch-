"""
DEMO 2 - Movimientos de cabeza
Muestra: pan (izquierda/derecha), tilt (arriba/abajo) y combinados.
Rango pan: -120° a +120° | tilt: -10° a +22°
Ejecutar: python demo_cabeza.py
"""
import threading, time, argparse
import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr


class DemoCabeza:

    def on_connect(self, robot):
        print(f"Conectado: {robot.name} ({robot.robot_type_name})")
        threading.Thread(target=self._run, args=(robot,), daemon=True).start()

    def on_sensors(self, robot):
        pass

    def _run(self, robot):
        h = robot.cmds.head

        print(">> Centro (posición neutral)")
        h.do_pan_tilt_angle(0, 0)
        time.sleep(0.5)

        print(">> Mirar izquierda (pan +90°)")
        h.do_pan_angle(90)
        time.sleep(0.5)

        print(">> Mirar derecha (pan -90°)")
        h.do_pan_angle(-90)
        time.sleep(0.5)

        print(">> Centro")
        h.do_pan_angle(0)
        time.sleep(0.5)

        print(">> Mirar arriba (tilt +20°)")
        h.do_tilt_angle(20)
        time.sleep(0.5)

        print(">> Mirar abajo (tilt -8°)")
        h.do_tilt_angle(-8)
        time.sleep(0.5)

        print(">> Diagonal: izquierda + arriba")
        h.do_pan_tilt_angle(60, 18)
        time.sleep(0.6)

        print(">> Diagonal: derecha + abajo")
        h.do_pan_tilt_angle(-60, -8)
        time.sleep(0.6)

        print(">> Barrido suave de lado a lado")
        for angulo in [90, 60, 30, 0, -30, -60, -90, -60, -30, 0, 30, 60, 90]:
            h.do_pan_angle(angulo)
            time.sleep(0.15)

        print(">> Centro final")
        h.do_pan_tilt_angle(0, 0)
        time.sleep(0.4)

        print("Demo cabeza completada.")
        mgr.WWBTLEManager.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mgr.WWBTLEManager.setup_argument_parser(parser)
    wwMain.start(DemoCabeza(), parser.parse_args(['--connect-eager']))
