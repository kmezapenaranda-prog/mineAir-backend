import numpy as np
import pandas as pd

from src.model.woa_xgboost import _elegir_umbral_fbeta, dividir_temporalmente


def test_division_temporal_esta_ordenada_y_purgada():
    i = pd.date_range("2026-01-01", periods=30 * 24 * 12, freq="5min", tz="UTC")
    h = pd.Timedelta(hours=6)
    b = dividir_temporalmente(i, purga=h)
    assert b.entrenamiento.max() < b.validacion.min() < b.prueba.min()
    assert i[b.validacion.min()] - i[b.entrenamiento.max()] > h
    assert i[b.prueba.min()] - i[b.validacion.max()] > h


def test_elegir_umbral_fbeta_respeta_techo_de_falsas_alarmas():
    rng = np.random.default_rng(0)
    n = 2000
    y = pd.Series((rng.random(n) < 0.02).astype(int))
    # Probabilidad informativa pero ruidosa: separa positivos de negativos
    # sin ser perfecta, para que exista una zona intermedia de umbrales.
    p = np.clip(y.to_numpy() * 0.6 + rng.normal(0, 0.25, n), 0, 1)

    sin_techo = _elegir_umbral_fbeta(y, p, beta=2.0)
    con_techo = _elegir_umbral_fbeta(y, p, beta=2.0, fpr_maxima=0.05)

    def fpr_de(umbral):
        pred = p >= umbral
        negativos = (y == 0).to_numpy()
        fp = (pred & negativos).sum()
        return fp / negativos.sum()

    assert fpr_de(con_techo) <= 0.05 + 1e-9
    assert con_techo >= sin_techo  # el techo solo puede exigir un umbral igual o mas alto
