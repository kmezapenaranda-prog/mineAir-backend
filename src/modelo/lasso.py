"""Selección de variables con LASSO, por (gas, horizonte).

El target es binario (excedencia sí/no dentro de la ventana [t, t+H], ver
`src.etiquetas.etiquetas`), así que "LASSO" aquí es regresión logística con
penalización L1 (`sklearn.linear_model.LogisticRegression(penalty="l1")`) —
el equivalente de LASSO para clasificación, y consistente con que XGBoost
(Fase 3) también hace clasificación binaria vía `predict_proba`.

La salida (coeficientes no nulos, ordenados por magnitud) es exactamente lo
que alimenta el campo `factores` del contrato de predicción — ver
`factores_contrato()` más abajo.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class Factor:
    nombre: str
    peso: float  # coeficiente sobre features estandarizadas; puede ser negativo


class SinExcedenciasError(ValueError):
    """El target no tiene ambas clases (0 y 1) tras eliminar filas con NaN.

    No es un bug: algunos gases pueden no cruzar su umbral en absoluto en un
    dataset sintético dado (p. ej. si su física de excedencia no está
    modelada en el generador todavía). Repórtalo, no lo escondas — es una
    señal de que el generador necesita más trabajo para ese gas, o de que la
    ventana de datos es demasiado corta.
    """


def seleccionar_features(
    X: pd.DataFrame, y: pd.Series, C: float = 0.5, seed: int = 42
) -> list[Factor]:
    """Ajusta LASSO (regresión logística L1) sobre `X` estandarizada para
    predecir `y` (binaria), y devuelve los coeficientes no nulos ordenados
    por magnitud descendente.

    Elimina filas con NaN en `X` o `y` antes de ajustar — a diferencia de
    XGBoost (Fase 3), sklearn no tolera datos faltantes nativamente. Es
    aceptable en esta etapa exploratoria de selección de variables.
    """
    datos = X.copy()
    datos["_y"] = y.to_numpy()
    datos = datos.dropna()

    if datos.empty:
        raise SinExcedenciasError("No quedan filas tras eliminar NaN; revisa el rango de fechas o las features.")
    if datos["_y"].nunique() < 2:
        raise SinExcedenciasError(
            "La etiqueta solo tiene una clase tras eliminar NaN (sin excedencias en los datos): "
            "no se puede ajustar LASSO para este (gas, horizonte)."
        )

    X_limpio = datos.drop(columns="_y")
    y_limpio = datos["_y"]

    escalador = StandardScaler()
    X_escalado = escalador.fit_transform(X_limpio)

    # l1_ratio=1.0 == penalty="l1" (la forma "penalty" quedó deprecada en
    # sklearn 1.8 y se elimina en 1.10; liblinear sigue soportando L1 puro).
    modelo = LogisticRegression(
        l1_ratio=1.0, solver="liblinear", C=C, class_weight="balanced", random_state=seed, max_iter=2000
    )
    modelo.fit(X_escalado, y_limpio)

    coeficientes = pd.Series(modelo.coef_[0], index=X_limpio.columns)
    no_nulos = coeficientes[coeficientes != 0]
    orden = no_nulos.reindex(no_nulos.abs().sort_values(ascending=False).index)

    return [Factor(nombre=nombre, peso=float(peso)) for nombre, peso in orden.items()]


def _valor_legible(nombre: str, valor: float) -> str:
    """Formatea el valor actual de una feature en texto legible, para el
    campo `valor` del contrato. Cobertura por categoría de feature; el resto
    cae a un formato numérico genérico — Fase 4 (servicio) puede ampliarla."""
    if pd.isna(valor):
        return "sin dato"
    if nombre.startswith("presion_delta_"):
        horas = nombre.rsplit("_", 1)[-1]
        signo = "+" if valor >= 0 else ""
        return f"{signo}{valor:.1f} hPa / {horas}"
    if nombre == "presion_hpa":
        return f"{valor:.1f} hPa"
    if nombre.endswith("_diff_retorno_entrada"):
        return f"{valor:+.2f} (retorno - entrada)"
    if nombre.startswith(("ch4_pct", "co2_pct", "o2_pct")) and "lag" in nombre:
        return f"{valor:.2f} % vol"
    if nombre.startswith(("co_ppm", "h2s_ppm")) and "lag" in nombre:
        return f"{valor:.1f} ppm"
    if nombre == "produccion_turno_actual_ton":
        return f"{valor:.1f} ton acumuladas en el turno"
    if nombre == "indice_gasificacion_m3_ton":
        return f"{valor:.1f} m³/ton"
    if nombre == "caudal_m3_s":
        return f"{valor:.1f} m³/s"
    if nombre == "ventilador_principal_on":
        return "encendido" if valor >= 0.5 else "apagado"
    if nombre == "horas_desde_ultima_voladura":
        return f"{valor:.1f} h desde la última voladura"
    if nombre == "kg_ultima_voladura":
        return f"{valor:.0f} kg"
    if nombre.startswith("turno_"):
        return "sí" if valor >= 0.5 else "no"
    return f"{valor:.3g}"


def factores_contrato(factores: list[Factor], fila_valores: pd.Series) -> list[dict]:
    """Convierte la salida de `seleccionar_features` al formato del campo
    `factores` del contrato de predicción: `[{nombre, peso, valor}, ...]`,
    ordenado por peso descendente, con el peso normalizado a fracción del
    total (así el ejemplo del contrato, donde los 3 factores mostrados no
    suman 1, es consistente: hay más factores no mostrados).
    """
    if not factores:
        return []
    total_abs = sum(abs(f.peso) for f in factores)
    return [
        {
            "nombre": f.nombre,
            "peso": round(abs(f.peso) / total_abs, 3) if total_abs else 0.0,
            "valor": _valor_legible(f.nombre, fila_valores.get(f.nombre, np.nan)),
        }
        for f in factores
    ]
