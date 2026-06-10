"""
Punto de entrada general. Conecta al primer robot disponible (Dash o Dot)
e imprime su nombre y tipo.

Ejecutar: python main.py
"""
import WonderPy.core.wwMain as wwMain
import WonderPy.core.wwBTLEMgr as mgr
import argparse


class InfoDelegate:

    def on_connect(self, robot):
        print(f"\nRobot encontrado!")
        print(f"  Nombre : {robot.name}")
        print(f"  Tipo   : {robot.robot_type_name}")
        print(f"\nConexion exitosa. Desconectando...")
        raise SystemExit(0)

    def on_sensors(self, robot):
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Detectar robot Dash o Dot")
    mgr.WWBTLEManager.setup_argument_parser(parser)
    args = parser.parse_args(['--connect-eager'])
    wwMain.start(InfoDelegate(), args)
