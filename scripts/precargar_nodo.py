"""Precarga historial sintetico para UN nodo especifico, terminando en el
instante actual — para que el pipeline en vivo (que exige >=12h de historia
densa) ya tenga ventana completa apenas empiece a llegar telemetria real del
mismo node_id.

Uso (correr justo antes de conectar el sensor real, no con horas de
anticipacion: la ventana solo mira las ultimas 13h, un precarga viejo
"envejece" fuera de rango):
    .venv/Scripts/python.exe -m scripts.precargar_nodo --node-id H1 --node-type casco --horas 13
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np


def _post(cliente: httpx.Client, base_url: str, ruta: str, payload: dict) -> dict:
    try:
        respuesta = cliente.post(f"{base_url}{ruta}", json=payload, timeout=10)
        respuesta.raise_for_status()
        return respuesta.json()
    except httpx.HTTPError as error:
        raise RuntimeError(f"No se pudo conectar con {base_url}: {error}") from error


def _paquete(node_id: str, node_type: str, momento: datetime, seq: int, rng: np.random.Generator) -> dict:
    ch4 = round(float(np.clip(0.35 + rng.normal(0, 0.03), 0.05, 1.0)), 4)
    co = round(float(np.clip(8 + rng.normal(0, 1.5), 0, 25)), 3)
    return {
        "schema_v": "1.0",
        "node_id": node_id,
        "node_type": node_type,
        "ubicacion": None,
        "frente": None,
        "timestamp": momento.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "seq": seq % 65536,
        "gases": {
            "ch4_pct": ch4,
            "co_ppm": co,
            "h2s_ppm": 0.0,
            "o2_pct": round(float(np.clip(20.9 - max(ch4 - 0.7, 0) * 1.5, 19.5, 20.9)), 3),
            "co2_pct": round(float(np.clip(0.08 + rng.normal(0, 0.005), 0, 1)), 4),
        },
        "ambiente": {"temp_c": 27.0, "humedad_pct": 65.0, "presion_hpa": None},
        "alarma_local": {"activa": False, "gases": []},
        "estado": {"bateria_pct": 90.0, "rssi_dbm": -70.0, "sensor_ok": True},
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api", default="http://127.0.0.1:8000/api")
    p.add_argument("--node-id", default="H1")
    p.add_argument("--node-type", default="casco", choices=["fijo", "casco"])
    p.add_argument("--horas", type=float, default=13.0,
                    help="Horas de historia a precargar. >=13 para cubrir toda la ventana del pipeline.")
    p.add_argument("--seed", type=int, default=1886)
    args = p.parse_args()

    rng = np.random.default_rng(args.seed)
    ahora = datetime.now(timezone.utc)
    pasos = int(args.horas * 3600 / 15)

    cliente = httpx.Client()
    seq = int(ahora.timestamp()) % 65536
    for i in range(pasos, -1, -1):
        momento = ahora - timedelta(seconds=15 * i)
        _post(cliente, args.api, "/telemetria", _paquete(args.node_id, args.node_type, momento, seq, rng))
        seq += 1
        if i % 500 == 0:
            print(f"faltan {i} paquetes...", flush=True)

    print(f"Precarga sintetica completa para {args.node_id}: {pasos + 1} paquetes, "
          f"terminando en {ahora.isoformat()}. Conecta el sensor real ahora.", flush=True)


if __name__ == "__main__":
    main()
