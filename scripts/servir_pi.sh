#!/usr/bin/env bash
# Arranca el servicio ML en la Raspberry Pi del gateway (ARM64).
#
# Requisitos previos (una sola vez, en la propia Pi):
#   python3 -m venv .venv
#   .venv/bin/pip install -r requirements.txt
#   Copiar a mano models/evacuar/ desde el PC de entrenamiento (esta
#   ignorado por git a proposito, no llega con git pull).
#
# Variables de entorno relevantes (ver src/servicio/api.py):
#   DATABASE_URL           URL obligatoria de MySQL (mysql+pymysql://usuario:clave@host:3306/base)
#   MINEAIR_CORS_ORIGINS   Origenes permitidos, separados por coma (ej. http://192.168.1.50:5173)
#   MINEAIR_INTERNAL_TOKEN Token para POST /predicciones (dejar vacio en demo local)
#
# --host 0.0.0.0 es obligatorio: sin esto la Pi solo se responde a si
# misma y el aplicativo web (en otra maquina) no puede llegar (ver
# CLAUDE-ml.md, cambio de arquitectura D1). --workers 1 es intencional:
# el estado en memoria del predictor no esta pensado para multiproceso.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -z "${MINEAIR_CORS_ORIGINS:-}" ]; then
  echo "AVISO: MINEAIR_CORS_ORIGINS no está definido — CORS solo permitirá" \
       "localhost/127.0.0.1. Si la web corre en otra máquina de la red de" \
       "la mina, exportar MINEAIR_CORS_ORIGINS=http://<ip-o-host-de-la-web>:<puerto> antes de esto." >&2
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL es obligatorio y debe apuntar a MySQL." >&2
  exit 1
fi

exec .venv/bin/python -m uvicorn src.servicio.api:app \
  --host 0.0.0.0 --port 8000 --workers 1
