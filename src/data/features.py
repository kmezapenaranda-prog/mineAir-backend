"""Features causales a partir de telemetría cruda de 15 segundos."""

from __future__ import annotations

import numpy as np
import pandas as pd

CADENCIA_S = 15
FRECUENCIA_PREDICCION_S = 300
# Un lag observado debe ser múltiplo de 15 s. No se interpola un lag de 5 s.
LAGS_SEGUNDOS = (15, 30, 45, 60, 300, 900, 1800, 3600, 4 * 3600, 6 * 3600, 8 * 3600, 12 * 3600)
GASES = ("ch4_pct", "co_ppm")
# Rezago 4-8h producción->CH4: la variable operativa que sostiene la tesis
# central del proyecto (ver CLAUDE-ml.md, "prueba de fuego" de Fase 2). Sin
# estos lags, LASSO solo puede predecir CH4 futuro desde su propio pasado
# (autocorrelación), no desde la causa física real.
LAGS_PRODUCCION_SEGUNDOS = (3600, 4 * 3600, 8 * 3600, 12 * 3600)


def _nombre_lag(segundos: int) -> str:
    if segundos % 3600 == 0:
        return f"{segundos // 3600}h"
    if segundos % 60 == 0:
        return f"{segundos // 60}min"
    return f"{segundos}s"


def construir_features(datos: pd.DataFrame, *, columna_tiempo: str = "timestamp",
                       frecuencia_prediccion_s: int = FRECUENCIA_PREDICCION_S,
                       version: str = "crudo-v2") -> pd.DataFrame:
    if version not in ("crudo-v2", "media5m-v1"):
        raise ValueError(f"Versión de features desconocida: {version}")
    if columna_tiempo not in datos:
        raise ValueError(f"Falta la columna {columna_tiempo!r}.")
    datos = datos.copy()
    for bandera in ("sensor_ok", "paquete_recibido"):
        if bandera in datos:
            datos.loc[~datos[bandera].eq(True), [g for g in GASES if g in datos]] = np.nan
    for columna in ("presion_hpa", "caudal_m3_s", "produccion_ton_h"):
        if columna not in datos:
            datos[columna] = np.nan
    indice = pd.DatetimeIndex(pd.to_datetime(datos[columna_tiempo], utc=True))
    if not indice.is_monotonic_increasing or indice.has_duplicates:
        raise ValueError("Los timestamps deben estar ordenados y no repetidos.")
    if len(indice) < 2 or not indice.to_series().diff().dropna().eq(pd.Timedelta(seconds=CADENCIA_S)).all():
        raise ValueError("Las features canónicas requieren telemetría regular cada 15 segundos.")
    if frecuencia_prediccion_s <= 0 or frecuencia_prediccion_s % CADENCIA_S:
        raise ValueError("La frecuencia de predicción debe ser múltiplo de 15 segundos.")

    posiciones = np.arange(0, len(datos), frecuencia_prediccion_s // CADENCIA_S)
    salida = pd.DataFrame(index=indice[posiciones])
    for gas in GASES:
        if gas not in datos:
            raise ValueError(f"Falta el gas objetivo {gas!r}.")
        valores = pd.to_numeric(datos[gas], errors="coerce").to_numpy()
        # La lectura actual es válida: se predice una excedencia estrictamente futura.
        medias = pd.Series(valores).rolling(20, min_periods=16).mean().to_numpy()
        salida[gas] = (medias if version == "media5m-v1" else valores)[posiciones]
        for segundos in LAGS_SEGUNDOS:
            offset = segundos // CADENCIA_S
            lag = np.full(len(posiciones), np.nan)
            validas = posiciones >= offset
            fuente = medias if version == "media5m-v1" and segundos >= 300 else valores
            lag[validas] = fuente[posiciones[validas] - offset]
            salida[f"{gas}_lag_{_nombre_lag(segundos)}"] = lag
        serie = pd.Series(valores).ffill(limit=4)
        for minutos in (5, 15, 30):
            salida[f"{gas}_media_{minutos}min"] = serie.rolling(minutos * 60 // CADENCIA_S,
                                                                  min_periods=1).mean().iloc[posiciones].to_numpy()
            salida[f"{gas}_std_{minutos}min"] = serie.rolling(minutos * 60 // CADENCIA_S,
                                                                min_periods=2).std().iloc[posiciones].to_numpy()

    numericas = ("produccion_ton_h", "espesor_manto_m", "gasificacion_m3_ton",
                 "caudal_m3_s", "resistencia_relativa", "presion_hpa",
                 "trabajadores", "arranque_mecanico", "turno", "voladura", "kg_voladura")
    for columna in numericas:
        if columna in datos:
            salida[columna] = pd.to_numeric(datos[columna], errors="coerce").to_numpy()[posiciones]

    if "produccion_ton_h" in datos:
        produccion = pd.to_numeric(datos["produccion_ton_h"], errors="coerce").to_numpy()
        for segundos in LAGS_PRODUCCION_SEGUNDOS:
            offset = segundos // CADENCIA_S
            lag = np.full(len(posiciones), np.nan)
            validas = posiciones >= offset
            lag[validas] = produccion[posiciones[validas] - offset]
            salida[f"produccion_ton_h_lag_{_nombre_lag(segundos)}"] = lag

    presion = pd.to_numeric(datos["presion_hpa"], errors="coerce").to_numpy()
    for horas in (1, 3, 6, 12):
        offset = horas * 3600 // CADENCIA_S
        delta = np.full(len(posiciones), np.nan)
        validas = posiciones >= offset
        delta[validas] = presion[posiciones[validas]] - presion[posiciones[validas] - offset]
        salida[f"presion_delta_{horas}h"] = delta
    caudal = pd.Series(pd.to_numeric(datos["caudal_m3_s"], errors="coerce"))
    salida["caudal_media_15min"] = caudal.rolling(15 * 60 // CADENCIA_S, min_periods=1).mean().iloc[posiciones].to_numpy()
    salida["caudal_delta_15min"] = caudal.diff(15 * 60 // CADENCIA_S).iloc[posiciones].to_numpy()
    h = salida.index.hour + salida.index.minute / 60
    salida["hora_sin"] = np.sin(2 * np.pi * h / 24)
    salida["hora_cos"] = np.cos(2 * np.pi * h / 24)
    salida["dia_ano_sin"] = np.sin(2 * np.pi * salida.index.dayofyear / 365.25)
    salida["dia_ano_cos"] = np.cos(2 * np.pi * salida.index.dayofyear / 365.25)
    return salida.replace([np.inf, -np.inf], np.nan)
