import os
import tempfile
from pathlib import Path

# Debe fijarse antes de importar src.servicio.api. SQLite se usa únicamente
# como motor efímero de tests; el servicio desplegado exige una URL MySQL.
_directorio_tests = tempfile.TemporaryDirectory(prefix="mineair-tests-")
_ruta_db = (Path(_directorio_tests.name) / "estado.db").as_posix()
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_ruta_db}"


def pytest_sessionfinish(session, exitstatus):
    from src.servicio.api import almacen
    almacen.close()
    _directorio_tests.cleanup()
