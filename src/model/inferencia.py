"""Carga artefactos versionados y ejecuta inferencia sobre telemetría canónica."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from xgboost import XGBClassifier

from src.data.features import construir_features
from src.modelo.lasso import Factor, factores_contrato

# La probabilidad conjunta tiene endpoint propio: no es una probabilidad
# marginal de CH4 ni de CO.

# Fracción del umbral de evacuar a partir de la cual se marca "atencion":
# una alerta temprana para que el ingeniero verifique antes de que el
# modelo recomiende evacuar (contrato #3 v1.7, campo `nivel`).
FACTOR_ATENCION = 0.70


def nivel_desde_probabilidad(probabilidad: float, umbral_evacuar: float,
                              *, factor_atencion: float = FACTOR_ATENCION) -> str:
    """'normal' | 'atencion' | 'evacuar' según la probabilidad predicha vs.
    el umbral de decisión del modelo (no es un umbral normativo del Decreto
    1886 — para eso ver `src.config.umbrales.nivel_proximidad`, que mira la
    lectura actual, no la predicción a futuro)."""
    if probabilidad >= umbral_evacuar:
        return "evacuar"
    if probabilidad >= umbral_evacuar * factor_atencion:
        return "atencion"
    return "normal"


class MotorInferencia:
    def __init__(self, directorio: str | Path = "models/evacuar") -> None:
        self.directorio = Path(directorio)
        self.modelo: XGBClassifier | None = None
        self.meta: dict | None = None
        ruta = self.directorio / "evacuar_woa_xgboost.json"
        meta_ruta = ruta.with_suffix(".metadata.json")
        if ruta.exists() and meta_ruta.exists():
            modelo = XGBClassifier(); modelo.load_model(ruta)
            self.modelo = modelo
            self.meta = json.loads(meta_ruta.read_text(encoding="utf-8"))

    @property
    def listo(self) -> bool:
        return self.modelo is not None

    def predecir(self, telemetria: pd.DataFrame) -> list[dict]:
        """Predice el último instante; requiere al menos 12 h regulares a 15 s."""
        if not self.listo or len(telemetria) < 2:
            return []
        telemetria = telemetria.copy()
        telemetria['timestamp'] = pd.to_datetime(telemetria.timestamp, utc=True)
        if telemetria.timestamp.max() - telemetria.timestamp.min() < pd.Timedelta(hours=12):
            return []
        # La API entrega datos regulares. Para uso directo, insertar huecos
        # a 15s conservando las columnas físicas ya presentes.
        telemetria = (telemetria.set_index('timestamp').sort_index()
                      .resample('15s', closed='right', label='right').last().reset_index())
        if telemetria[['ch4_pct', 'co_ppm']].iloc[-1].isna().any():
            return []
        features = construir_features(telemetria, frecuencia_prediccion_s=15,
                                       version=self.meta.get('features_version', 'crudo-v2'))
        fila = features.iloc[[-1]]
        columnas = self.meta["features"]
        faltantes = [c for c in columnas if c not in fila]
        for columna in faltantes:
            fila[columna] = float('nan')
        if fila[['ch4_pct', 'co_ppm']].isna().any(axis=None):
            return []
        prob = float(self.modelo.predict_proba(fila[columnas])[:, 1][0])
        corte = float(self.meta["umbral_clasificacion"])
        nivel = nivel_desde_probabilidad(prob, corte)
        generada_en = fila.index[-1].isoformat()
        return [{"schema_v": "riesgo-conjunto-1.0", "objetivo": "ch4_6h_o_co_1h",
                 "probabilidad": prob, "nivel": nivel,
                 "recomienda_evacuar": prob >= corte, "umbral_clasificacion": corte,
                 "generada_en": generada_en, "experimental": True,
                 "confianza": "reducida" if fila[columnas].isna().any(axis=None) else "normal",
                 "features_faltantes": [c for c in columnas if pd.isna(fila.iloc[0][c])],
                 "factores": factores_contrato([Factor(**f) for f in self.meta['factores_lasso']], fila.iloc[0])}]
