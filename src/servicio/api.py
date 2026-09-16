"""API local de MineAIr conforme al contrato compartido v1.6."""

from __future__ import annotations

import json
import os
import sqlite3
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import Draft202012Validator, FormatChecker

from src.config.umbrales import UMBRALES, nivel_proximidad, porcentaje_limite


RAIZ = Path(__file__).resolve().parents[2]
RUTAS_SCHEMA = {
    "telemetria": RAIZ / "telemetria.schema.json",
    "variables_operativas": RAIZ / "variables_operativas.schema.json",
    "prediccion": RAIZ / "prediccion.schema.json",
}
RUTA_DB = Path(os.environ.get("MINEAIR_DB_PATH", str(RAIZ / "estado_servicio.db")))
# Frontera de acceso para POST /predicciones (A14 auditoría): sin token
# configurado, el endpoint queda abierto como antes — aceptable en
# desarrollo local, no en la Pi expuesta a la red de la mina. Configurar
# MINEAIR_INTERNAL_TOKEN al desplegar y que solo el propio servicio (o un
# publicador de confianza) lo conozca. Se lee en cada solicitud (no al
# importar el módulo) para que los tests puedan activarla/desactivarla.
def _verificar_token_interno(x_internal_token: str | None = Header(default=None)) -> None:
    token = os.environ.get("MINEAIR_INTERNAL_TOKEN")
    if token and x_internal_token != token:
        raise HTTPException(status_code=401, detail="Token interno inválido o ausente.")


def _validador(nombre: str) -> Draft202012Validator:
    schema = json.loads(RUTAS_SCHEMA[nombre].read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


VALIDADORES = {nombre: _validador(nombre) for nombre in RUTAS_SCHEMA}


def _validar(nombre: str, payload: dict[str, Any]) -> None:
    errores = sorted(VALIDADORES[nombre].iter_errors(payload), key=lambda error: list(error.path))
    if errores:
        error = errores[0]
        ruta = ".".join(map(str, error.absolute_path)) or "$"
        raise HTTPException(status_code=422, detail=f"Contrato {nombre} inválido en {ruta}: {error.message}")


def _utc(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00")).astimezone(timezone.utc)


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# El gateway actual (ESP32) no anula los gases según node_type ni resuelve
# ubicacion/frente por tabla local, como exige MINEAIR-SHARED.md §2 y §7.5.
# Mientras eso no se corrija en el firmware, el servicio sanea el paquete
# aquí para que respete el contrato antes de validar y persistir.
GASES_APLICABLES_POR_TIPO: dict[str, set[str]] = {
    "fijo": {"ch4_pct", "co_ppm", "h2s_ppm", "o2_pct", "co2_pct"},
    "casco": {"ch4_pct", "co_ppm", "h2s_ppm", "o2_pct", "co2_pct"},
    "repetidor": set(),
    "contador": set(),
    "superficie": set(),
}

GAS_KEY_A_UMBRAL: dict[str, str] = {"ch4_pct": "ch4", "co_ppm": "co", "h2s_ppm": "h2s", "co2_pct": "co2"}
# o2_pct queda fuera: O2 usa un rango permisible (Sentido.RANGO), no un
# límite único al que "acercarse" — ver nivel_proximidad().


def _proximidad_gases(gases: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Qué tan cerca está AHORA cada gas de su límite normativo — reactivo,
    a partir de la última lectura, no una predicción. Complementa (no
    reemplaza) la probabilidad a futuro que expone /predicciones."""
    if not gases:
        return {}
    salida: dict[str, dict[str, Any]] = {}
    for clave, gas in GAS_KEY_A_UMBRAL.items():
        valor = gases.get(clave)
        if valor is None or not UMBRALES[gas].monitoreado:
            continue
        salida[gas] = {
            "valor": valor,
            "porcentaje_limite": round(porcentaje_limite(gas, valor), 3),
            "nivel": nivel_proximidad(gas, valor),
        }
    return salida


UBICACION_POR_NODO: dict[str, tuple[str, str | None]] = {
    "S1": ("Retorno frente A", "A"),
    "S2": ("Vía principal de entrada", "A"),
    "SUP1": ("Estación de superficie", None),
}


def _sanear_telemetria(payload: dict[str, Any]) -> dict[str, Any]:
    aplicables = GASES_APLICABLES_POR_TIPO.get(payload.get("node_type"))
    gases = payload.get("gases")
    if aplicables is not None and isinstance(gases, dict):
        payload["gases"] = {
            gas: (valor if gas in aplicables else None) for gas, valor in gases.items()
        }
    ubicacion_conocida = UBICACION_POR_NODO.get(payload.get("node_id"))
    if ubicacion_conocida is not None:
        ubicacion, frente = ubicacion_conocida
        if payload.get("ubicacion") is None:
            payload["ubicacion"] = ubicacion
        if payload.get("frente") is None and frente is not None:
            payload["frente"] = frente
    return payload


class AlmacenSQLite:
    """Estado persistente (sobrevive a reinicios) para el servicio offline de la demo."""

    def __init__(self, ruta_db: Path = RUTA_DB, max_paquetes_por_nodo: int = 50_000) -> None:
        self.max_paquetes_por_nodo = max_paquetes_por_nodo
        self.lock = RLock()
        self.conexion = sqlite3.connect(str(ruta_db), check_same_thread=False)
        self.conexion.row_factory = sqlite3.Row
        with self.lock, self.conexion:
            # Migración aditiva: conservar la tabla anterior como respaldo.
            columnas = self.conexion.execute("PRAGMA table_info(telemetria)").fetchall()
            migrar = bool(columnas) and not any(c["name"] == "id" for c in columnas)
            if migrar:
                self.conexion.execute("BEGIN IMMEDIATE")
                self.conexion.execute("ALTER TABLE telemetria RENAME TO telemetria_legacy")
                self.conexion.execute("DROP INDEX IF EXISTS idx_telemetria_node_ts")
            self.conexion.execute(
                """CREATE TABLE IF NOT EXISTS telemetria (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    recibido_en TEXT NOT NULL,
                    payload TEXT NOT NULL
                )"""
            )
            if migrar:
                self.conexion.execute(
                    "INSERT INTO telemetria (node_id, seq, timestamp, recibido_en, payload) "
                    "SELECT node_id, seq, timestamp, recibido_en, payload FROM telemetria_legacy"
                )
            self.conexion.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetria_node_ts ON telemetria (node_id, timestamp)"
            )
            self.conexion.execute(
                """CREATE TABLE IF NOT EXISTS variables_operativas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    registrado_en TEXT NOT NULL,
                    payload TEXT NOT NULL
                )"""
            )
            self.conexion.execute(
                """CREATE TABLE IF NOT EXISTS predicciones (
                    node_id TEXT NOT NULL,
                    gas TEXT NOT NULL,
                    horizonte_h INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (node_id, gas, horizonte_h)
                )"""
            )

    def limpiar(self) -> None:
        with self.lock, self.conexion:
            self.conexion.execute("DELETE FROM telemetria")
            self.conexion.execute("DELETE FROM variables_operativas")
            self.conexion.execute("DELETE FROM predicciones")

    def guardar_telemetria(self, paquete: dict[str, Any]) -> bool:
        with self.lock, self.conexion:
            # La trama no incluye boot_id. Limitar deduplicación a 60 s del
            # timestamp UTC de recepción y comparar también el uptime. Fuera
            # de esa ventana, reutilizar seq nunca bloquea un nuevo arranque.
            anteriores = self.conexion.execute(
                "SELECT timestamp, payload FROM telemetria WHERE node_id = ? AND seq = ? "
                "AND timestamp >= ? AND timestamp <= ?",
                (paquete["node_id"], paquete["seq"],
                 (_utc(paquete["timestamp"]) - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
                 (_utc(paquete["timestamp"]) + timedelta(seconds=60)).isoformat().replace("+00:00", "Z")),
            ).fetchall()
            for anterior in anteriores:
                previo = json.loads(anterior["payload"])
                if paquete.get("t_ms") is not None:
                    duplicado = previo.get("t_ms") == paquete["t_ms"]
                else:
                    duplicado = anterior["timestamp"] == paquete["timestamp"]
                if duplicado:
                    return False
            cursor = self.conexion.execute(
                "INSERT INTO telemetria (node_id, seq, timestamp, recibido_en, payload) "
                "VALUES (?, ?, ?, ?, ?)",
                (paquete["node_id"], paquete["seq"], paquete["timestamp"], _ahora_iso(), json.dumps(paquete)),
            )
            guardado = cursor.rowcount > 0
            if guardado:
                self.conexion.execute(
                    """DELETE FROM telemetria WHERE node_id = ? AND id NOT IN (
                        SELECT id FROM telemetria WHERE node_id = ? ORDER BY timestamp DESC, id DESC LIMIT ?
                    )""",
                    (paquete["node_id"], paquete["node_id"], self.max_paquetes_por_nodo),
                )
            return guardado

    def guardar_variable_operativa(self, registro: dict[str, Any]) -> None:
        with self.lock, self.conexion:
            self.conexion.execute(
                "INSERT INTO variables_operativas (registrado_en, payload) VALUES (?, ?)",
                (registro["registrado_en"], json.dumps(registro)),
            )

    def publicar_prediccion(self, prediccion: dict[str, Any]) -> None:
        """Publica una salida del motor solo si cumple el contrato #3."""
        _validar("prediccion", prediccion)
        if prediccion["recomienda_evacuar"] != (prediccion["nivel"] == "evacuar"):
            raise HTTPException(status_code=422, detail="nivel y recomienda_evacuar son inconsistentes.")
        with self.lock, self.conexion:
            self.conexion.execute(
                "INSERT OR REPLACE INTO predicciones (node_id, gas, horizonte_h, payload) VALUES (?, ?, ?, ?)",
                (prediccion["node_id"], prediccion["gas"], prediccion["horizonte_h"], json.dumps(prediccion)),
            )

    def ultima_lectura_por_nodo(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            filas = self.conexion.execute(
                """SELECT payload FROM (
                    SELECT payload, node_id,
                           ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY timestamp DESC) AS orden
                    FROM telemetria
                ) WHERE orden = 1"""
            ).fetchall()
        lecturas = (json.loads(fila["payload"]) for fila in filas)
        return {lectura["node_id"]: lectura for lectura in lecturas}

    def historial_nodo(self, node_id: str) -> list[dict[str, Any]] | None:
        with self.lock:
            existe = self.conexion.execute("SELECT 1 FROM telemetria WHERE node_id = ? LIMIT 1", (node_id,)).fetchone()
            if not existe:
                return None
            filas = self.conexion.execute(
                "SELECT payload FROM telemetria WHERE node_id = ? ORDER BY timestamp ASC", (node_id,)
            ).fetchall()
        return [json.loads(fila["payload"]) for fila in filas]

    def predicciones_vigentes(self) -> list[dict[str, Any]]:
        with self.lock:
            filas = self.conexion.execute("SELECT payload FROM predicciones").fetchall()
        ahora = datetime.now(timezone.utc)
        predicciones = [json.loads(fila["payload"]) for fila in filas]
        return [p for p in predicciones
                if timedelta(0) <= ahora - _utc(p["generada_en"]) <= timedelta(minutes=10)]

    def estado_nodos(self) -> tuple[int, list[str], str | None]:
        """(total de nodos, nodos sin datos hace >60s, timestamp de la última sincronía)."""
        with self.lock:
            filas = self.conexion.execute(
                "SELECT node_id, MAX(recibido_en) AS ultimo FROM telemetria GROUP BY node_id"
            ).fetchall()
        if not filas:
            return 0, [], None
        ahora = datetime.now(timezone.utc)
        sin_datos = sorted(fila["node_id"] for fila in filas if ahora - _utc(fila["ultimo"]) > timedelta(seconds=60))
        ultima_sync = max(fila["ultimo"] for fila in filas)
        return len(filas), sin_datos, ultima_sync

    def total_predicciones(self) -> int:
        return len(self.predicciones_vigentes())


almacen = AlmacenSQLite()
router = APIRouter()
predictor = None


@asynccontextmanager
async def ciclo_vida(app):
    global predictor
    from src.model.inferencia import MotorInferencia
    from src.servicio.predictor import Predictor
    try:
        motor = MotorInferencia(RAIZ / 'models' / 'evacuar')
    except Exception:
        logging.getLogger(__name__).exception('No se pudo cargar el artefacto ML')
        motor = MotorInferencia(RAIZ / 'models' / 'no_disponible')
    predictor = Predictor(almacen, motor)
    detener = asyncio.Event()
    async def ejecutar():
        while not detener.is_set():
            await asyncio.to_thread(predictor.ejecutar)
            try:
                await asyncio.wait_for(detener.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass
    tarea = asyncio.create_task(ejecutar())
    try:
        yield
    finally:
        detener.set()
        await tarea
        predictor = None


@router.get('/riesgo-conjunto')
def obtener_riesgo_conjunto() -> list[dict[str, Any]]:
    return predictor.vigentes() if predictor else []


@router.post("/telemetria")
def recibir_telemetria(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    payload = _sanear_telemetria(payload)
    _validar("telemetria", payload)
    payload["timestamp"] = _utc(payload["timestamp"]).isoformat().replace("+00:00", "Z")
    guardado = almacen.guardar_telemetria(payload)
    return {
        "ok": True,
        "duplicado": not guardado,
        "node_id": payload["node_id"],
        "seq": payload["seq"],
        "recibido_en": _ahora_iso(),
    }


@router.post("/variables-operativas")
def recibir_variables_operativas(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _validar("variables_operativas", payload)
    registro = {**payload, "registrado_en": _ahora_iso()}
    almacen.guardar_variable_operativa(registro)
    return {"ok": True, "registro": registro}


@router.get("/nodos")
def obtener_nodos() -> list[dict[str, Any]]:
    nodos = [
        {
            "node_id": node_id,
            "node_type": ultima["node_type"],
            "ubicacion": ultima.get("ubicacion"),
            "frente": ultima.get("frente"),
            "ultima_lectura": ultima,
            "proximidad_normativa": (_proximidad_gases(ultima.get("gases"))
                                     if ultima["estado"]["sensor_ok"] else {}),
        }
        for node_id, ultima in almacen.ultima_lectura_por_nodo().items()
    ]
    return sorted(nodos, key=lambda nodo: nodo["node_id"])


@router.get("/telemetria/{node_id}")
def obtener_telemetria(
    node_id: str,
    desde: datetime | None = Query(default=None),
    hasta: datetime | None = Query(default=None),
) -> list[dict[str, Any]]:
    if any(fecha is not None and fecha.tzinfo is None for fecha in (desde, hasta)):
        raise HTTPException(status_code=422, detail="Las fechas deben incluir zona horaria (Z o desplazamiento UTC).")
    if desde and hasta and desde > hasta:
        raise HTTPException(status_code=422, detail="`desde` no puede ser posterior a `hasta`.")
    historial = almacen.historial_nodo(node_id)
    if historial is None:
        raise HTTPException(status_code=404, detail=f"Nodo desconocido: {node_id}")
    desde_utc = desde.astimezone(timezone.utc) if desde else None
    hasta_utc = hasta.astimezone(timezone.utc) if hasta else None
    salida = []
    for paquete in historial:
        momento = _utc(paquete["timestamp"])
        if desde_utc and momento < desde_utc:
            continue
        if hasta_utc and momento > hasta_utc:
            continue
        salida.append(paquete)
    return salida


@router.get("/predicciones")
def obtener_predicciones(
    nodeId: str | None = None,
    node_id: str | None = None,
    gas: Literal["ch4", "co"] | None = None,
    recomienda_evacuar: bool | None = None,
    horizonte_h: Literal[1, 6, 12, 24] | None = None,
) -> list[dict[str, Any]]:
    nodo = nodeId or node_id
    predicciones = almacen.predicciones_vigentes()
    if nodo:
        predicciones = [p for p in predicciones if p["node_id"] == nodo]
    if gas:
        predicciones = [p for p in predicciones if p["gas"] == gas]
    if recomienda_evacuar is not None:
        predicciones = [p for p in predicciones if p["recomienda_evacuar"] == recomienda_evacuar]
    if horizonte_h:
        predicciones = [p for p in predicciones if p["horizonte_h"] == horizonte_h]
    return sorted(predicciones, key=lambda prediccion: prediccion["probabilidad"], reverse=True)


@router.post("/predicciones", include_in_schema=False, dependencies=[Depends(_verificar_token_interno)])
def publicar_prediccion(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Entrada interna para el motor ML y el simulador de demostración."""
    almacen.publicar_prediccion(payload)
    return {
        "ok": True,
        "node_id": payload["node_id"],
        "gas": payload["gas"],
        "horizonte_h": payload["horizonte_h"],
        "publicada_en": _ahora_iso(),
    }


@router.get("/estado")
def obtener_estado() -> dict[str, Any]:
    total_nodos, nodos_sin_datos, ultima_sync = almacen.estado_nodos()
    return {
        "servicio": "ok",
        "contrato_v": "1.8",
        "ultima_sync": ultima_sync,
        "nodos_activos": total_nodos - len(nodos_sin_datos),
        "nodos_sin_datos": nodos_sin_datos,
        "modelo_evacuar_cargado": bool(predictor and predictor.motor.listo),
        "ultimo_ciclo_ml": predictor.ultimo_ciclo if predictor else None,
        "error_ml": predictor.error if predictor else None,
        "riesgos_conjuntos_disponibles": len(predictor.vigentes()) if predictor else 0,
        "predicciones_disponibles": almacen.total_predicciones(),
    }


app = FastAPI(title="MineAIr ML", version="1.8.0", lifespan=ciclo_vida)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("MINEAIR_CORS_ORIGINS", "").split(",") if os.environ.get("MINEAIR_CORS_ORIGINS") else [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")
app.include_router(router, include_in_schema=False)
