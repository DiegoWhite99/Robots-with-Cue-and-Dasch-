"""
DEMO COMPLETO - Integración de desplazamiento, cabeza y luces
El robot ejecuta una coreografía combinando todos los sistemas.
Ejecutar: python demo_completo.py
"""
import threading, time, argparse
import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr


class DemoCompleto:

    def on_connect(self, robot):
        print(f"Conectado: {robot.name} ({robot.robot_type_name})")
        threading.Thread(target=self._run, args=(robot,), daemon=True).start()

    def on_sensors(self, robot):
        pass

    def _run(self, robot):
        b = robot.cmds.body
        h = robot.cmds.head
        rgb = robot.cmds.RGB

        def luz(r, g, b_val):
            rgb.stage_all(r, g, b_val)
            robot.send_staged()

        # ── FASE 1: Saludo ──────────────────────────────────────────────
        print("\n[1/4] Saludo inicial")
        luz(0, 1, 0)                       # verde = listo
        h.do_pan_tilt_angle(0, 20)         # cabeza arriba
        time.sleep(0.4)
        h.do_pan_tilt_angle(0, -5)         # cabeza abajo (asiente)
        time.sleep(0.3)
        h.do_pan_tilt_angle(0, 10)
        time.sleep(0.4)

        # ── FASE 2: Mirar y avanzar ─────────────────────────────────────
        print("[2/4] Explorar: mirar y avanzar")
        luz(0, 0.5, 1)                     # azul claro = explorando

        h.do_pan_angle(90)                 # mirar izquierda
        time.sleep(0.3)
        h.do_pan_angle(-90)                # mirar derecha
        time.sleep(0.3)
        h.do_pan_angle(0)                  # centro

        b.do_forward(50, 60)               # avanzar mientras explora
        luz(0, 1, 0.5)

        h.do_pan_angle(45)
        b.do_turn(-90, 90)
        h.do_pan_angle(0)

        b.do_forward(50, 60)

        # ── FASE 3: Celebración en el lugar ─────────────────────────────
        print("[3/4] Celebración")
        for _ in range(3):
            luz(1, 1, 0)                   # amarillo
            h.do_pan_angle(60)
            b.do_turn(90, 150)
            luz(1, 0, 0.5)                 # magenta
            h.do_pan_angle(-60)
            b.do_turn(-90, 150)

        # ── FASE 4: Regreso y apagado ────────────────────────────────────
        print("[4/4] Regreso a posición neutral")
        luz(0.5, 0, 1)                     # violeta = finalizando

        b.do_turn(180, 90)
        b.do_forward(100, 80)
        b.do_turn(180, 90)

        h.do_pan_tilt_angle(0, 0)          # cabeza al centro
        b.stage_stop()
        robot.send_staged()
        time.sleep(0.5)

        luz(0, 0, 0)                       # apagar luces
        print("\nDemo completo finalizado.")
        mgr.WWBTLEManager.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mgr.WWBTLEManager.setup_argument_parser(parser)
    wwMain.start(DemoCompleto(), parser.parse_args(['--connect-eager']))
