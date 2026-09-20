"""Persistencia de la API mediante SQLAlchemy Core.

La API usa MySQL en producción. Las URLs SQLite solo se utilizan en tests para
ejecutar la misma capa de almacenamiento sin levantar un servidor local.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    desc,
    func,
    select,
    update,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Engine


def normalizar_database_url(database_url: str) -> str:
    """Normaliza el alias de MySQL que Railway puede entregar."""
    if not database_url or not database_url.strip():
        raise ValueError("DATABASE_URL es obligatorio para la persistencia de la API.")
    database_url = database_url.strip()
    if database_url.startswith("mysql://"):
        return "mysql+pymysql://" + database_url[len("mysql://") :]
    return database_url


def _utc(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00")).astimezone(timezone.utc)


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AlmacenMySQL:
    """Estado persistente para MySQL y el adaptador SQLite aislado de tests."""

    def __init__(self, database_url: str, max_paquetes_por_nodo: int = 50_000) -> None:
        self.max_paquetes_por_nodo = max_paquetes_por_nodo
        self.lock = RLock()
        self.database_url = normalizar_database_url(database_url)
        connect_args = {"check_same_thread": False} if self.database_url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(
            self.database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        self.metadata = MetaData()
        self.telemetria = Table(
            "telemetria",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("node_id", String(64), nullable=False),
            Column("seq", Integer, nullable=False),
            Column("timestamp", String(40), nullable=False),
            Column("recibido_en", String(40), nullable=False),
            Column("payload", Text, nullable=False),
        )
        Index("idx_telemetria_node_ts", self.telemetria.c.node_id, self.telemetria.c.timestamp)
        self.variables = Table(
            "variables_operativas",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("registrado_en", String(40), nullable=False),
            Column("payload", Text, nullable=False),
        )
        self.predicciones = Table(
            "predicciones",
            self.metadata,
            Column("node_id", String(64), primary_key=True),
            Column("gas", String(16), primary_key=True),
            Column("horizonte_h", Integer, primary_key=True),
            Column("payload", Text, nullable=False),
        )
        self.mapas = Table(
            "mapas",
            self.metadata,
            Column("id", String(32), primary_key=True),
            Column("actualizado_en", String(40), nullable=False),
            Column("payload", Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False),
        )
        self.configuraciones = Table(
            "configuraciones",
            self.metadata,
            Column("id", String(32), primary_key=True),
            Column("actualizado_en", String(40), nullable=False),
            Column("payload", Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False),
        )
        self.metadata.create_all(self.engine)

    def limpiar(self) -> None:
        with self.lock, self.engine.begin() as conexion:
            conexion.execute(delete(self.telemetria))
            conexion.execute(delete(self.variables))
            conexion.execute(delete(self.predicciones))
            conexion.execute(delete(self.mapas))
            conexion.execute(delete(self.configuraciones))

    def guardar_telemetria(self, paquete: dict[str, Any]) -> bool:
        """Guarda una trama, deduplicando retransmisiones y aplicando retención."""
        timestamp = paquete["timestamp"]
        inicio = (_utc(timestamp) - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
        fin = (_utc(timestamp) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
        with self.lock, self.engine.begin() as conexion:
            anteriores = conexion.execute(
                select(self.telemetria.c.timestamp, self.telemetria.c.payload).where(
                    self.telemetria.c.node_id == paquete["node_id"],
                    self.telemetria.c.seq == paquete["seq"],
                    self.telemetria.c.timestamp >= inicio,
                    self.telemetria.c.timestamp <= fin,
                )
            ).mappings()
            for anterior in anteriores:
                previo = json.loads(anterior["payload"])
                if paquete.get("t_ms") is not None:
                    duplicado = previo.get("t_ms") == paquete["t_ms"]
                else:
                    duplicado = anterior["timestamp"] == timestamp
                if duplicado:
                    return False

            conexion.execute(
                self.telemetria.insert().values(
                    node_id=paquete["node_id"],
                    seq=paquete["seq"],
                    timestamp=timestamp,
                    recibido_en=_ahora_iso(),
                    payload=json.dumps(paquete),
                )
            )
            ids = list(
                conexion.execute(
                    select(self.telemetria.c.id)
                    .where(self.telemetria.c.node_id == paquete["node_id"])
                    .order_by(desc(self.telemetria.c.timestamp), desc(self.telemetria.c.id))
                ).scalars()
            )
            antiguos = ids[self.max_paquetes_por_nodo :]
            if antiguos:
                conexion.execute(delete(self.telemetria).where(self.telemetria.c.id.in_(antiguos)))
            return True

    def guardar_variable_operativa(self, registro: dict[str, Any]) -> None:
        with self.lock, self.engine.begin() as conexion:
            conexion.execute(
                self.variables.insert().values(
                    registrado_en=registro["registrado_en"],
                    payload=json.dumps(registro),
                )
            )

    def variables_operativas(self) -> list[dict[str, Any]]:
        with self.lock, self.engine.connect() as conexion:
            filas = list(conexion.execute(
                select(self.variables.c.payload).order_by(self.variables.c.registrado_en)
            ).mappings())
        return [json.loads(fila["payload"]) for fila in filas]

    def publicar_prediccion(self, prediccion: dict[str, Any]) -> None:
        if prediccion["recomienda_evacuar"] != (prediccion["nivel"] == "evacuar"):
            raise ValueError("nivel y recomienda_evacuar son inconsistentes.")
        with self.lock, self.engine.begin() as conexion:
            clave = {
                "node_id": prediccion["node_id"],
                "gas": prediccion["gas"],
                "horizonte_h": prediccion["horizonte_h"],
            }
            actualizado = conexion.execute(
                update(self.predicciones).where(
                    self.predicciones.c.node_id == clave["node_id"],
                    self.predicciones.c.gas == clave["gas"],
                    self.predicciones.c.horizonte_h == clave["horizonte_h"],
                ).values(payload=json.dumps(prediccion))
            )
            if actualizado.rowcount == 0:
                conexion.execute(self.predicciones.insert().values(**clave, payload=json.dumps(prediccion)))

    def ultima_lectura_por_nodo(self) -> dict[str, dict[str, Any]]:
        ordenadas = select(
            self.telemetria.c.node_id,
            self.telemetria.c.payload,
            func.row_number()
            .over(
                partition_by=self.telemetria.c.node_id,
                order_by=(desc(self.telemetria.c.timestamp), desc(self.telemetria.c.id)),
            )
            .label("orden"),
        ).subquery()
        with self.lock, self.engine.connect() as conexion:
            filas = list(conexion.execute(
                select(ordenadas.c.payload).where(ordenadas.c.orden == 1)
            ).mappings())
        lecturas = (json.loads(fila["payload"]) for fila in filas)
        return {lectura["node_id"]: lectura for lectura in lecturas}

    def historial_nodo(self, node_id: str) -> list[dict[str, Any]] | None:
        with self.lock, self.engine.connect() as conexion:
            existe = conexion.execute(
                select(self.telemetria.c.id).where(self.telemetria.c.node_id == node_id).limit(1)
            ).first()
            if existe is None:
                return None
            filas = list(conexion.execute(
                select(self.telemetria.c.payload)
                .where(self.telemetria.c.node_id == node_id)
                .order_by(self.telemetria.c.timestamp, self.telemetria.c.id)
            ).mappings())
        return [json.loads(fila["payload"]) for fila in filas]

    def predicciones_vigentes(self) -> list[dict[str, Any]]:
        with self.lock, self.engine.connect() as conexion:
            filas = list(conexion.execute(select(self.predicciones.c.payload)).mappings())
        ahora = datetime.now(timezone.utc)
        predicciones = [json.loads(fila["payload"]) for fila in filas]
        return [
            prediccion
            for prediccion in predicciones
            if timedelta(0) <= ahora - _utc(prediccion["generada_en"]) <= timedelta(minutes=10)
        ]

    def estado_nodos(self) -> tuple[int, list[str], str | None]:
        with self.lock, self.engine.connect() as conexion:
            filas = conexion.execute(
                select(self.telemetria.c.node_id, func.max(self.telemetria.c.recibido_en).label("ultimo"))
                .group_by(self.telemetria.c.node_id)
            ).mappings()
            filas = list(filas)
        if not filas:
            return 0, [], None
        ahora = datetime.now(timezone.utc)
        sin_datos = sorted(
            fila["node_id"]
            for fila in filas
            if ahora - _utc(fila["ultimo"]) > timedelta(seconds=60)
        )
        ultima_sync = max(fila["ultimo"] for fila in filas)
        return len(filas), sin_datos, ultima_sync

    def total_predicciones(self) -> int:
        return len(self.predicciones_vigentes())

    def obtener_mapa(self) -> dict[str, Any] | None:
        with self.lock, self.engine.connect() as conexion:
            fila = conexion.execute(
                select(self.mapas.c.payload).where(self.mapas.c.id == "activo")
            ).first()
        return json.loads(fila[0]) if fila else None

    def guardar_mapa(self, mapa: dict[str, Any]) -> str:
        actualizado_en = _ahora_iso()
        with self.lock, self.engine.begin() as conexion:
            actualizado = conexion.execute(
                update(self.mapas)
                .where(self.mapas.c.id == "activo")
                .values(actualizado_en=actualizado_en, payload=json.dumps(mapa))
            )
            if actualizado.rowcount == 0:
                conexion.execute(self.mapas.insert().values(
                    id="activo", actualizado_en=actualizado_en, payload=json.dumps(mapa)
                ))
        return actualizado_en

    def obtener_configuracion(self) -> dict[str, Any] | None:
        with self.lock, self.engine.connect() as conexion:
            fila = conexion.execute(
                select(self.configuraciones.c.payload).where(self.configuraciones.c.id == "activa")
            ).first()
        return json.loads(fila[0]) if fila else None

    def guardar_configuracion(self, parcial: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        actualizado_en = _ahora_iso()
        actual = self.obtener_configuracion() or {}
        configuracion = {**actual, **parcial}
        with self.lock, self.engine.begin() as conexion:
            actualizado = conexion.execute(
                update(self.configuraciones)
                .where(self.configuraciones.c.id == "activa")
                .values(actualizado_en=actualizado_en, payload=json.dumps(configuracion))
            )
            if actualizado.rowcount == 0:
                conexion.execute(self.configuraciones.insert().values(
                    id="activa", actualizado_en=actualizado_en, payload=json.dumps(configuracion)
                ))
        return actualizado_en, configuracion

    def close(self) -> None:
        self.engine.dispose()
