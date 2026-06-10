import asyncio
import WonderPy.core.wwMain as wwMain


class BaseRobotDelegate:
    """Clase base para experimentos. Heredar e implementar on_connect y on_sensors."""

    def on_connect(self, robot):
        print(f"Conectado a: {robot.name}")
        self.robot = robot
        self.setup(robot)

    def on_sensors(self, robot):
        self.loop(robot)

    def setup(self, robot):
        """Ejecutado una vez al conectar. Sobreescribir en subclase."""
        pass

    def loop(self, robot):
        """Ejecutado en cada ciclo de sensores. Sobreescribir en subclase."""
        pass


def run(delegate, connect_type=None, connect_name=None, eager=True):
    """Inicia la conexión y el loop principal."""
    import argparse
    parser = argparse.ArgumentParser()
    wwMain.WWBTLEManager_args(parser) if hasattr(wwMain, 'WWBTLEManager_args') else None

    import WonderPy.core.wwBTLEMgr as mgr
    parser2 = argparse.ArgumentParser()
    mgr.WWBTLEManager.setup_argument_parser(parser2)

    args_list = []
    if connect_type:
        args_list += ['--connect-type', connect_type]
    if connect_name:
        args_list += ['--connect-name', connect_name]
    if eager:
        args_list += ['--connect-eager']

    arguments = parser2.parse_args(args_list)
    wwMain.start(delegate, arguments)
