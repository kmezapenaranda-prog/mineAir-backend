"""Reproduce el dataset sintético contra la API local para la demo integrada."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd


RAIZ = Path(__file__).resolve().parents[1]


def _post(cliente: httpx.Client, base_url: str, ruta: str, payload: dict) -> dict:
    # Reutiliza una sola conexión (keep-alive) para todo el run: con la
    # cadencia real de 15 s, un precarga de 24 h son ~17000 POSTs — abrir un
    # socket por request (como hacía urllib antes) agota los puertos
    # efímeros de Windows a mitad de camino (WinError 10048).
    try:
        respuesta = cliente.post(f"{base_url}{ruta}", json=payload, timeout=10)
        respuesta.raise_for_status()
        return respuesta.json()
    except httpx.HTTPError as error:
        raise RuntimeError(f"No se pudo conectar con {base_url}: {error}") from error


def _iso(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _valor_o_none(valor: float) -> float | None:
    """NaN -> null: la fuente sintetica marca fallas de sensor con NaN
    (mismo criterio que sensor_ok=false), nunca con un numero valido."""
    return None if pd.isna(valor) else float(valor)


def _paquete_fijo(node_id: str, ubicacion: str, fila: pd.Series, momento: datetime, seq: int, factor: float) -> dict:
    sensor_ok = bool(fila.get("sensor_ok", True)) and pd.notna(fila["ch4_pct"])
    ch4 = _valor_o_none(float(fila["ch4_pct"]) * factor) if sensor_ok else None
    co = _valor_o_none(float(fila["co_ppm"]) * factor) if sensor_ok else None
    o2 = _valor_o_none(20.8 - max(float(fila["ch4_pct"]) - 0.7, 0) * 1.5) if sensor_ok else None
    return {
        "schema_v": "1.0",
        "node_id": node_id,
        "node_type": "fijo",
        "ubicacion": ubicacion,
        "frente": "A",
        "timestamp": _iso(momento),
        "seq": seq % 65536,
        "gases": {
            "ch4_pct": round(ch4, 4) if ch4 is not None else None,
            "co_ppm": round(co, 3) if co is not None else None,
            "h2s_ppm": 0.2 if sensor_ok else None,
            "o2_pct": round(o2, 3) if o2 is not None else None,
            "co2_pct": 0.08 if sensor_ok else None,
        },
        "ambiente": {"temp_c": 27.5, "humedad_pct": 81.0, "presion_hpa": None},
        "alarma_local": {
            "activa": bool(ch4 is not None and ch4 > 1.0),
            "gases": ["ch4_pct"] if ch4 is not None and ch4 > 1.0 else [],
        },
        "estado": {"bateria_pct": 82.0, "rssi_dbm": -84.0, "sensor_ok": sensor_ok},
    }


def _paquete_superficie(fila: pd.Series, momento: datetime, seq: int) -> dict:
    return {
        "schema_v": "1.0",
        "node_id": "SUP1",
        "node_type": "superficie",
        "ubicacion": "Estación de superficie",
        "frente": None,
        "timestamp": _iso(momento),
        "seq": seq % 65536,
        "gases": {"ch4_pct": None, "co_ppm": None, "h2s_ppm": None, "o2_pct": None, "co2_pct": None},
        "ambiente": {"temp_c": 25.0, "humedad_pct": 72.0,
                     "presion_hpa": round(p, 3) if (p := _valor_o_none(fila["presion_hpa"])) is not None else None},
        "estado": {"bateria_pct": 100.0, "rssi_dbm": -55.0, "sensor_ok": True},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000/api")
    parser.add_argument("--intervalo", type=float, default=15.0,
                        help="Segundos reales entre paquetes (15 = cadencia real del contrato D6).")
    parser.add_argument("--precarga", type=int, default=5760,
                        help="Filas históricas a precargar a 15 s (5760 = 24 h).")
    parser.add_argument("--una-vez", action="store_true")
    args = parser.parse_args()

    # Dataset crudo real a cadencia 15 s (la misma del contrato D6 y del
    # entrenamiento) SIN downsamplear: el ciclo de inferencia en vivo
    # (src/servicio/ventanas.py) resamplea a 15 s y exige >=12 h de historia
    # densa para construir su ventana — publicar cada 5 min simulados (como
    # antes) deja casi todo el grid de 15 s en NaN y el motor nunca predice.
    datos = pd.read_csv(RAIZ / "data" / "sintetico_6m_15s" / "telemetria_2025-01.csv.gz")
    datos["timestamp"] = pd.to_datetime(datos["timestamp"], utc=True)
    posicion = max(12 * 240, min(len(datos) - 1, 23 * 24 * 240))
    inicio = max(0, posicion - args.precarga + 1)
    ahora = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    cliente = httpx.Client()

    _post(cliente, args.api, "/variables-operativas", {
        "schema_v": "1.0", "fecha": ahora.date().isoformat(), "turno": "manana", "frente": "A",
        "manto": "Manto 3", "produccion_ton": 42.5, "produccion_origen": "contada",
        "indice_gasificacion_m3_ton": 8.2, "ventilador_principal_on": True, "caudal_m3_s": 12.4,
        "voladuras": [], "observaciones": "Flujo sintético de demostración", "registrado_por": "simulador",
    })

    # Evita colisiones al reiniciar el simulador: el contrato deduplica por
    # (node_id, seq) y el contador real también continúa entre reconexiones.
    seq = int(time.time()) % 65536
    for indice in range(inicio, posicion + 1):
        momento = ahora - timedelta(seconds=15 * (posicion - indice))
        fila = datos.iloc[indice]
        _post(cliente, args.api, "/telemetria", _paquete_fijo("S1", "Retorno frente A", fila, momento, seq, 1.0))
        _post(cliente, args.api, "/telemetria", _paquete_fijo("S2", "Vía principal de entrada", fila, momento, seq, 0.62))
        _post(cliente, args.api, "/telemetria", _paquete_superficie(fila, momento, seq))
        seq += 1
    print(f"Precarga de telemetria completa: {args.precarga} filas a 15s, 3 nodos, el servicio calculara el riesgo conjunto cada 5 minutos.", flush=True)

    # Cierra el hueco entre el ultimo timestamp de precarga (anclado a un
    # "ahora" congelado ANTES del loop lento de ~17000 POSTs) y el reloj
    # real actual: sin esto, el pipeline en vivo (ventanas.py) arranca con
    # un hueco de NaN en la ventana rodante de 20 muestras y no predice
    # hasta que ese hueco sale de los ultimos 5 minutos.
    paso = timedelta(seconds=15)
    brecha = datetime.now(timezone.utc) - momento
    while brecha > paso:
        momento += paso
        _post(cliente, args.api, "/telemetria", _paquete_fijo("S1", "Retorno frente A", fila, momento, seq, 1.0))
        _post(cliente, args.api, "/telemetria", _paquete_fijo("S2", "Vía principal de entrada", fila, momento, seq, 0.62))
        _post(cliente, args.api, "/telemetria", _paquete_superficie(fila, momento, seq))
        seq += 1
        brecha = datetime.now(timezone.utc) - momento

    if args.una_vez:
        return
    while True:
        time.sleep(args.intervalo)
        posicion = (posicion + 1) % len(datos)
        ahora = datetime.now(timezone.utc)
        fila = datos.iloc[posicion]
        _post(cliente, args.api, "/telemetria", _paquete_fijo("S1", "Retorno frente A", fila, ahora, seq, 1.0))
        _post(cliente, args.api, "/telemetria", _paquete_fijo("S2", "Vía principal de entrada", fila, ahora, seq, 0.62))
        _post(cliente, args.api, "/telemetria", _paquete_superficie(fila, ahora, seq))
        seq += 1


if __name__ == "__main__":
    main()
