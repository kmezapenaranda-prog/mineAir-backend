"""CLI reproducible para entrenar CH4 y CO con LASSO -> WOA -> XGBoost."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from src.model.woa_xgboost import entrenar_woa_xgboost


def cargar_dataset(raiz: Path) -> pd.DataFrame:
    partes = sorted(raiz.glob("telemetria_*.csv.gz"))
    if not partes:
        raise FileNotFoundError(f"No hay particiones en {raiz}")
    return pd.concat((pd.read_csv(p) for p in partes), ignore_index=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data/sintetico_6m_15s")
    p.add_argument("--models", default="models/v2")
    p.add_argument("--epochs-woa", type=int, default=10)
    p.add_argument("--poblacion-woa", type=int, default=10)
    p.add_argument("--gas", choices=("ch4", "co", "ambos"), default="ambos")
    args = p.parse_args()
    datos, destino = cargar_dataset(Path(args.data)), Path(args.models)
    destino.mkdir(parents=True, exist_ok=True)
    gases = ("ch4", "co") if args.gas == "ambos" else (args.gas,)
    resultados = {}
    for gas in gases:
        resultado = entrenar_woa_xgboost(datos, gas=gas, epochs_woa=args.epochs_woa,
            poblacion_woa=args.poblacion_woa, ruta_modelo=destino / f"{gas}_woa_xgboost.json")
        resultados[gas] = asdict(resultado)
        print(gas, resultado.auc, resultado.average_precision, resultado.tasa_falsas_alarmas)
    (destino / "metricas.json").write_text(json.dumps(resultados, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
