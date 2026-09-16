"""CLI: genera telemetría sintética particionada por mes."""

import argparse
from pathlib import Path

from src.data.generador import ConfiguracionGenerador, generar_dataset


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--destino", default="data/sintetico_6m_15s")
    p.add_argument("--dias", type=int, default=183)
    p.add_argument("--seed", type=int, default=1886)
    args = p.parse_args()
    ruta = generar_dataset(Path(args.destino), ConfiguracionGenerador(dias=args.dias, seed=args.seed))
    print(ruta / "manifest.json")


if __name__ == "__main__":
    main()
