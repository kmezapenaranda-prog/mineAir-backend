"""API local de MineAIr conforme al contrato compartido v1.6."""

from __future__ import annotations

import json
import os
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import Draft202012Validator, FormatChecker

from src.config.umbrales import UMBRALES, nivel_proximidad, porcentaje_limite
from src.servicio.almacen import AlmacenMySQL


RAIZ = Path(__file__).resolve().parents[2]
RUTAS_SCHEMA = {
    "telemetria": RAIZ / "telemetria.schema.json",
    "variables_operativas": RAIZ / "variables_operativas.schema.json",
    "prediccion": RAIZ / "prediccion.schema.json",
}
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL es obligatorio para iniciar la API.")
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


almacen = AlmacenMySQL(DATABASE_URL)
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
    _validar("prediccion", payload)
    if payload["recomienda_evacuar"] != (payload["nivel"] == "evacuar"):
        raise HTTPException(status_code=422, detail="nivel y recomienda_evacuar son inconsistentes.")
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
