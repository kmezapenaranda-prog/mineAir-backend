import os
import tempfile
from pathlib import Path

# Debe fijarse antes de importar src.servicio.api (lee la ruta al importarse),
# para no compartir base de datos con el servicio real que pueda estar corriendo.
_directorio_tests = tempfile.TemporaryDirectory(prefix="mineair-tests-")
os.environ["MINEAIR_DB_PATH"] = str(Path(_directorio_tests.name) / "estado.db")


def pytest_sessionfinish(session, exitstatus):
    from src.servicio.api import almacen
    almacen.conexion.close()
    _directorio_tests.cleanup()
