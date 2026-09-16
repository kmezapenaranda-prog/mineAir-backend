"""Etiquetas estrictamente futuras para los únicos objetivos v1: CH4 y CO."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.config.umbrales import UMBRALES, columnas_referencia, excede

OBJETIVOS = {"ch4": "ch4_pct", "co": "co_ppm"}
REFERENCIAS_SINTETICAS = {"ch4": "ch4_referencia_pct", "co": "co_referencia_ppm"}
HORIZONTES = {"ch4": pd.Timedelta(hours=6), "co": pd.Timedelta(hours=1)}


def etiquetar_excedencia(valores: pd.Series, gas: str,
                         horizonte: pd.Timedelta | None = None) -> pd.Series:
    """1 si habrá una excedencia en ``(t, t+horizonte]``; cola incompleta=NaN."""
    if gas not in OBJETIVOS:
        raise ValueError("El modelo v1 solo genera etiquetas para 'ch4' y 'co'.")
    if not isinstance(valores.index, pd.DatetimeIndex) or not valores.index.is_monotonic_increasing:
        raise ValueError("Se requiere un DatetimeIndex ordenado.")
    horizonte = horizonte or HORIZONTES[gas]
    if horizonte <= pd.Timedelta(0) or len(valores) < 2:
        raise ValueError("El horizonte y la serie deben ser válidos.")
    diferencias = valores.index.to_series().diff().dropna()
    resolucion = diferencias.iloc[0]
    if resolucion <= pd.Timedelta(0) or not diferencias.eq(resolucion).all():
        raise ValueError("La serie debe tener cadencia regular.")
    pasos_f = horizonte / resolucion
    if not float(pasos_f).is_integer():
        raise ValueError("El horizonte debe ser múltiplo de la cadencia.")
    pasos = int(pasos_f)
    fuera = pd.Series([bool(excede(gas, float(v))) if pd.notna(v) else np.nan for v in valores],
                      index=valores.index, dtype="float64")
    # shift(-1) excluye el presente; invertir permite una ventana hacia adelante.
    ventana = fuera.shift(-1).iloc[::-1].rolling(pasos, min_periods=1)
    maxima = ventana.max().iloc[::-1]
    cobertura = ventana.count().iloc[::-1]
    futura = maxima.where((maxima == 1) | (cobertura == pasos))
    # Mantener la censura temporal al final del dataset.
    futura.iloc[-pasos:] = np.nan
    horas = horizonte / pd.Timedelta(hours=1)
    sufijo = str(int(horas)) if float(horas).is_integer() else f"{horas:g}"
    futura.name = f"excede_{gas}_{sufijo}h"
    return futura


def generar_etiquetas(datos: pd.DataFrame, *, frecuencia_prediccion_s: int = 300) -> pd.DataFrame:
    indice = pd.DatetimeIndex(pd.to_datetime(datos["timestamp"], utc=True))
    paso = frecuencia_prediccion_s // 15
    posiciones = np.arange(0, len(datos), paso)
    salida = pd.DataFrame(index=indice[posiciones])
    for gas, columna in OBJETIVOS.items():
        columna = REFERENCIAS_SINTETICAS[gas] if REFERENCIAS_SINTETICAS[gas] in datos else columna
        completa = etiquetar_excedencia(pd.Series(datos[columna].to_numpy(), index=indice), gas)
        salida[completa.name] = completa.iloc[posiciones].to_numpy()
    return salida


def generar_etiqueta_evacuar(datos: pd.DataFrame, *, frecuencia_prediccion_s: int = 300) -> pd.DataFrame:
    """Etiqueta unificada ``evacuar``: 1 si CH4 o CO excederán su umbral, cada
    uno en su propio horizonte físico (CH4 6 h por el rezago de desorción,
    CO 1 h por el pulso de voladura). La unión es la decisión operativa; los
    umbrales del Decreto 1886 no cambian por unificar la salida.

    Se incluyen las mediciones actuales y las etiquetas por gas junto a
    ``evacuar`` para que la decisión sea auditable (factores) y no una caja
    negra: nunca se presenta un "evacuar" sin poder explicar cuál gas y
    cuándo lo disparó.
    """
    indice = pd.DatetimeIndex(pd.to_datetime(datos["timestamp"], utc=True))
    paso = frecuencia_prediccion_s // 15
    posiciones = np.arange(0, len(datos), paso)
    salida = pd.DataFrame(index=indice[posiciones])
    salida["ch4_pct"] = datos["ch4_pct"].to_numpy()[posiciones]
    salida["co_ppm"] = datos["co_ppm"].to_numpy()[posiciones]

    etiquetas_gas: dict[str, np.ndarray] = {}
    for gas, columna in OBJETIVOS.items():
        columna = REFERENCIAS_SINTETICAS[gas] if REFERENCIAS_SINTETICAS[gas] in datos else columna
        completa = etiquetar_excedencia(pd.Series(datos[columna].to_numpy(), index=indice), gas)
        etiquetas_gas[gas] = completa.iloc[posiciones].to_numpy()
        salida[completa.name] = etiquetas_gas[gas]

    ch4_l, co_l = etiquetas_gas["ch4"], etiquetas_gas["co"]
    alguno_positivo = (np.nan_to_num(ch4_l) > 0) | (np.nan_to_num(co_l) > 0)
    alguno_desconocido = np.isnan(ch4_l) | np.isnan(co_l)
    salida["evacuar"] = np.where(alguno_positivo, 1.0, np.where(alguno_desconocido, np.nan, 0.0))
    return salida


def generar_dataset_evacuar_referencia(datos: pd.DataFrame, *,
                                       frecuencia_prediccion_s: int = 300) -> pd.DataFrame:
    """Dataset "evacuar" con las columnas de referencia normativa (TLV-TWA,
    STEL, límite del Decreto 1886, LEL) al lado de cada gas. Son contexto
    legal para lectura humana, no features del modelo: por eso van en un
    dataset aparte y no en ``construir_features``.
    """
    etiquetas = generar_etiqueta_evacuar(datos, frecuencia_prediccion_s=frecuencia_prediccion_s)
    columnas: dict[str, object] = {"ch4_pct": etiquetas["ch4_pct"], **columnas_referencia("ch4"),
                                   "co_ppm": etiquetas["co_ppm"], **columnas_referencia("co")}
    if "o2_pct" in datos.columns:
        paso = frecuencia_prediccion_s // 15
        posiciones = np.arange(0, len(datos), paso)
        columnas["o2_pct"] = datos["o2_pct"].to_numpy()[posiciones]
        columnas.update(columnas_referencia("o2"))
    columnas["excede_ch4_6h"] = etiquetas["excede_ch4_6h"]
    columnas["excede_co_1h"] = etiquetas["excede_co_1h"]
    columnas["evacuar"] = etiquetas["evacuar"]
    return pd.DataFrame(columnas, index=etiquetas.index)
