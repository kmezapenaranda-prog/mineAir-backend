"""Puente USB a API ML. Conserva una cola SQLite para reintentos HTTP."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

LOG = logging.getLogger("gateway")


def decodificar_linea(linea: str, ahora: datetime | None = None) -> dict | None:
    """El host asigna UTC una sola vez; banners seriales no son telemetría."""
    try:
        paquete = json.loads(linea)
    except (ValueError, TypeError):
        return None
    if not isinstance(paquete, dict) or "node_id" not in paquete:
        return None
    momento = ahora or datetime.now(timezone.utc)
    if momento.tzinfo is None:
        raise ValueError("El reloj de recepción debe incluir zona horaria.")
    paquete["timestamp"] = momento.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return paquete


class ColaGateway:
    def __init__(self, ruta: str | Path):
        self.db = sqlite3.connect(str(ruta))
        self.db.execute("CREATE TABLE IF NOT EXISTS pendientes (id INTEGER PRIMARY KEY, payload TEXT, error TEXT)")
        self.db.commit()

    def agregar(self, paquete: dict):
        with self.db:
            self.db.execute("INSERT INTO pendientes (payload) VALUES (?)", (json.dumps(paquete),))

    def enviar(self, cliente: httpx.Client, url: str, limite: int = 20):
        filas = self.db.execute("SELECT id, payload FROM pendientes WHERE error IS NULL ORDER BY id LIMIT ?", (limite,)).fetchall()
        for identidad, texto in filas:
            try:
                respuesta = cliente.post(url, json=json.loads(texto))
                if respuesta.status_code == 422:
                    with self.db:
                        self.db.execute("UPDATE pendientes SET error = ? WHERE id = ?", (respuesta.text, identidad))
                    LOG.error("Paquete %s incompatible; conservado en la cola: %s", identidad, respuesta.text)
                    continue
                respuesta.raise_for_status()
            except httpx.HTTPError as error:
                LOG.warning("API no disponible; datos pendientes conservados: %s", error)
                break
            with self.db:
                self.db.execute("DELETE FROM pendientes WHERE id = ?", (identidad,))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puerto", required=True, help="COM8 en Windows o /dev/ttyUSB0 en Linux")
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/telemetria")
    parser.add_argument("--cola", default="gateway_pendientes.db")
    args = parser.parse_args()
    import serial
    logging.basicConfig(level=logging.INFO)
    cola = ColaGateway(args.cola)
    with httpx.Client(timeout=3) as cliente:
        try:
            while True:
                try:
                    with serial.Serial(args.puerto, 115200, timeout=1) as puerto:
                        LOG.info("Escuchando %s a 115200 baudios", args.puerto)
                        pendiente = b""
                        while True:
                            pendiente += puerto.read_until(b"\n")
                            if b"\n" in pendiente:
                                linea, pendiente = pendiente.split(b"\n", 1)
                                paquete = decodificar_linea(linea.decode("utf-8", errors="replace"))
                                if paquete is not None:
                                    cola.agregar(paquete)
                            if len(pendiente) > 16384:
                                LOG.warning("Línea serial demasiado larga; descartada")
                                pendiente = b""
                            cola.enviar(cliente, args.api)
                except serial.SerialException as error:
                    LOG.warning("Puerto desconectado: %s", error)
                    cola.enviar(cliente, args.api)
                    time.sleep(2)
        except KeyboardInterrupt:
            LOG.info("Puente detenido; cola persistida")
        finally:
            cola.db.close()


if __name__ == "__main__":
    main()
