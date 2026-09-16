"""Pipeline canónico LASSO -> WOA -> XGBoost para CH4 y CO."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from mealpy import FloatVar
from mealpy.swarm_based import WOA
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    fbeta_score,
    roc_auc_score,
)
from xgboost import XGBClassifier

from src.config.mina import ConfiguracionMina
from src.data.etiquetas import HORIZONTES, OBJETIVOS, generar_etiqueta_evacuar, generar_etiquetas
from src.data.features import construir_features
from src.modelo.lasso import Factor, seleccionar_features


@dataclass(frozen=True)
class BloquesTemporales:
    entrenamiento: np.ndarray
    validacion: np.ndarray
    prueba: np.ndarray


@dataclass(frozen=True)
class ResultadoPipeline:
    gas: str
    horizonte_h: float
    auc: float
    average_precision: float
    brier: float
    tasa_falsas_alarmas: float
    precision: float
    sensibilidad: float
    umbral_clasificacion: float
    prevalencia_prueba: float
    hiperparametros: dict[str, float | int]
    factores_lasso: list[Factor]
    n_entrenamiento: int
    n_validacion: int
    n_prueba: int
    falsos_positivos: int
    falsos_negativos: int
    ruta_modelo: str | None


def dividir_temporalmente(indice: pd.DatetimeIndex, *, purga: pd.Timedelta,
                          proporcion_entrenamiento: float = .60,
                          proporcion_validacion: float = .20) -> BloquesTemporales:
    if len(indice) < 10 or proporcion_entrenamiento + proporcion_validacion >= 1:
        raise ValueError("División temporal inválida o insuficiente.")
    c1, c2 = indice[int(len(indice) * proporcion_entrenamiento)], indice[int(len(indice) * (proporcion_entrenamiento + proporcion_validacion))]
    p = np.arange(len(indice))
    bloques = BloquesTemporales(p[indice < c1 - purga], p[(indice >= c1) & (indice < c2 - purga)], p[indice >= c2])
    if not all(len(x) for x in asdict(bloques).values()):
        raise ValueError("No hay datos suficientes después de aplicar la purga.")
    return bloques


def _params(solucion: np.ndarray) -> dict[str, float | int]:
    return {"max_depth": int(round(solucion[0])), "learning_rate": float(solucion[1]),
            "n_estimators": int(round(solucion[2])), "subsample": float(solucion[3]),
            "colsample_bytree": float(solucion[4]), "min_child_weight": float(solucion[5]),
            "gamma": float(solucion[6])}


def _modelo(params: dict[str, float | int], y: pd.Series, seed: int) -> XGBClassifier:
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if not pos or not neg:
        raise ValueError("El bloque necesita ejemplos positivos y negativos.")
    return XGBClassifier(**params, objective="binary:logistic", eval_metric="aucpr",
                         scale_pos_weight=neg / pos, random_state=seed, n_jobs=1,
                         tree_method="hist", missing=np.nan)


def _elegir_umbral(y: pd.Series, p: np.ndarray, fpr_maxima: float = .02) -> float:
    """Prioriza confianza operacional: máximo recall con <=2% FPR en validación."""
    candidatos = []
    respaldo = []
    for umbral in np.linspace(.05, .99, 95):
        z = p >= umbral
        tn, fp, fn, tp = confusion_matrix(y, z, labels=[0, 1]).ravel()
        sensibilidad = tp / (tp + fn) if tp + fn else 0
        fpr = fp / (fp + tn) if fp + tn else 1
        precision = tp / (tp + fp) if tp + fp else 0
        respaldo.append((sensibilidad - fpr, precision, float(umbral)))
        if fpr <= fpr_maxima:
            candidatos.append((sensibilidad, precision, float(umbral)))
    return max(candidatos or respaldo)[2]


def entrenar_woa_xgboost(datos: pd.DataFrame, *, gas: str = "ch4",
                          epochs_woa: int = 10, poblacion_woa: int = 10,
                          C_lasso: float = .005, seed: int = 1886,
                          ruta_modelo: str | Path | None = None) -> ResultadoPipeline:
    if gas not in OBJETIVOS:
        raise ValueError("Solo se entrenan modelos para CH4 y CO.")
    X, etiquetas = construir_features(datos), generar_etiquetas(datos)
    y = etiquetas[f"excede_{gas}_{int(HORIZONTES[gas] / pd.Timedelta(hours=1))}h"]
    conjunto = X.join(y).dropna(subset=[y.name])
    X, y = conjunto.drop(columns=y.name), conjunto[y.name].astype("int8")
    bloques = dividir_temporalmente(X.index, purga=HORIZONTES[gas])

    factores = seleccionar_features(X.iloc[bloques.entrenamiento], y.iloc[bloques.entrenamiento], C=C_lasso, seed=seed)
    columnas = [f.nombre for f in factores]
    if not columnas:
        raise ValueError("LASSO no seleccionó variables.")
    xe, ye = X.iloc[bloques.entrenamiento][columnas], y.iloc[bloques.entrenamiento]
    xv, yv = X.iloc[bloques.validacion][columnas], y.iloc[bloques.validacion]

    def objetivo(solucion: np.ndarray) -> float:
        m = _modelo(_params(solucion), ye, seed); m.fit(xe, ye)
        return float(average_precision_score(yv, m.predict_proba(xv)[:, 1]))

    problema = {"bounds": FloatVar(lb=(2, .02, 80, .60, .60, 1, 0),
                                    ub=(8, .30, 400, 1, 1, 10, 2), name="xgb"),
                "minmax": "max", "obj_func": objetivo, "log_to": None}
    mejor = WOA.OriginalWOA(epoch=epochs_woa, pop_size=poblacion_woa).solve(problema, seed=seed)
    hiper = _params(mejor.solution)
    provisional = _modelo(hiper, ye, seed); provisional.fit(xe, ye)
    umbral = _elegir_umbral(yv, provisional.predict_proba(xv)[:, 1])

    ajuste = np.concatenate([bloques.entrenamiento, bloques.validacion])
    modelo = _modelo(hiper, y.iloc[ajuste], seed); modelo.fit(X.iloc[ajuste][columnas], y.iloc[ajuste])
    ruta_guardada = None
    if ruta_modelo:
        ruta = Path(ruta_modelo); ruta.parent.mkdir(parents=True, exist_ok=True); modelo.save_model(ruta)
        meta = {"schema_v": "2.0", "gas": gas, "horizonte_h": HORIZONTES[gas] / pd.Timedelta(hours=1),
                "umbral_clasificacion": umbral, "features": columnas,
                "factores_lasso": [asdict(f) for f in factores], "hiperparametros": hiper,
                "seed": seed, "mina": ConfiguracionMina().como_dict()}
        ruta.with_suffix(".metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        ruta_guardada = str(ruta)

    yp = y.iloc[bloques.prueba]; prob = modelo.predict_proba(X.iloc[bloques.prueba][columnas])[:, 1]; pred = prob >= umbral
    tn, fp, fn, tp = confusion_matrix(yp, pred, labels=[0, 1]).ravel()
    return ResultadoPipeline(gas, float(HORIZONTES[gas] / pd.Timedelta(hours=1)),
        float(roc_auc_score(yp, prob)), float(average_precision_score(yp, prob)),
        float(brier_score_loss(yp, prob)), float(fp / (fp + tn)), float(tp / (tp + fp)),
        float(tp / (tp + fn)), umbral, float(yp.mean()), hiper, factores,
        len(bloques.entrenamiento), len(bloques.validacion), len(bloques.prueba),
        int(fp), int(fn), ruta_guardada)


# =====================================================================
# Objetivo unificado "evacuar" (unión CH4/CO) — reemplaza como target de
# producción a las predicciones separadas por gas, a pedido del equipo.
# Métrica de selección: F2 (recall pesa 4x más que precisión), coherente
# con un evento raro (~2 % de prevalencia) donde un falso negativo es
# mucho más caro que una falsa alarma.
# =====================================================================

@dataclass(frozen=True)
class ResultadoEvacuar:
    horizonte_ch4_h: float
    horizonte_co_h: float
    auc: float
    average_precision: float
    f2: float
    f3: float
    precision: float
    sensibilidad: float
    tasa_falsas_alarmas: float
    umbral_clasificacion: float
    prevalencia_prueba: float
    hiperparametros: dict[str, float | int]
    factores_lasso: list[Factor]
    n_entrenamiento: int
    n_validacion: int
    n_prueba: int
    falsos_positivos: int
    falsos_negativos: int
    ruta_modelo: str | None


def _elegir_umbral_fbeta(y: pd.Series, p: np.ndarray, *, beta: float = 2.0,
                          fpr_maxima: float | None = None) -> float:
    """Umbral que maximiza F-beta en validación (prioriza recall si beta>1).

    ``fpr_maxima`` acota la tasa de falsas alarmas antes de maximizar F-beta
    entre los umbrales elegibles — sin esto, F-beta puro puede elegir un
    umbral con una tasa de falsas alarmas inaceptable en operación (una
    etiqueta más escasa/estricta empuja el óptimo de F-beta hacia umbrales
    muy bajos). Si ningún umbral cumple la cota, se cae al mejor F-beta
    global (mismo criterio de respaldo que ``_elegir_umbral``)."""
    umbrales = np.linspace(.01, .99, 197)
    positivos = np.asarray(y) == 1
    pred = np.asarray(p)[:, None] >= umbrales
    tp = (pred & positivos[:, None]).sum(axis=0)
    fp = (pred & ~positivos[:, None]).sum(axis=0)
    tn = (~pred & ~positivos[:, None]).sum(axis=0)
    fn = positivos.sum() - tp
    divisor = (1 + beta**2) * tp + beta**2 * fn + fp
    scores = np.divide((1 + beta**2) * tp, divisor, out=np.zeros_like(divisor, dtype=float), where=divisor > 0)
    if fpr_maxima is not None:
        fpr = np.divide(fp, fp + tn, out=np.ones_like(fp, dtype=float), where=(fp + tn) > 0)
        elegibles = fpr <= fpr_maxima
        if elegibles.any():
            scores = np.where(elegibles, scores, -1.0)
    return float(umbrales[np.argmax(scores)])


def entrenar_woa_xgboost_evacuar(datos: pd.DataFrame, *, epochs_woa: int = 10, poblacion_woa: int = 10,
                                  C_lasso: float = .005, seed: int = 1886, beta: float = 2.0,
                                  fpr_maxima: float = .05,
                                  ruta_modelo: str | Path | None = None) -> ResultadoEvacuar:
    X, etiquetas = construir_features(datos, version="media5m-v1"), generar_etiqueta_evacuar(datos)
    y_col = etiquetas["evacuar"]
    conjunto = X.join(y_col).dropna(subset=["evacuar"])
    X, y = conjunto.drop(columns="evacuar"), conjunto["evacuar"].astype("int8")
    purga = max(HORIZONTES.values())
    bloques = dividir_temporalmente(X.index, purga=purga)

    factores = seleccionar_features(X.iloc[bloques.entrenamiento], y.iloc[bloques.entrenamiento], C=C_lasso, seed=seed)
    columnas = [f.nombre for f in factores]
    if not columnas:
        raise ValueError("LASSO no seleccionó variables.")
    xe, ye = X.iloc[bloques.entrenamiento][columnas], y.iloc[bloques.entrenamiento]
    xv, yv = X.iloc[bloques.validacion][columnas], y.iloc[bloques.validacion]

    def objetivo(solucion: np.ndarray) -> float:
        m = _modelo(_params(solucion), ye, seed); m.fit(xe, ye)
        p = m.predict_proba(xv)[:, 1]
        umbral = _elegir_umbral_fbeta(yv, p, beta=beta, fpr_maxima=fpr_maxima)
        return float(fbeta_score(yv, p >= umbral, beta=beta, zero_division=0))

    problema = {"bounds": FloatVar(lb=(2, .02, 80, .60, .60, 1, 0),
                                    ub=(8, .30, 400, 1, 1, 10, 2), name="xgb"),
                "minmax": "max", "obj_func": objetivo, "log_to": None}
    mejor = WOA.OriginalWOA(epoch=epochs_woa, pop_size=poblacion_woa).solve(problema, seed=seed)
    hiper = _params(mejor.solution)
    provisional = _modelo(hiper, ye, seed); provisional.fit(xe, ye)
    umbral = _elegir_umbral_fbeta(yv, provisional.predict_proba(xv)[:, 1], beta=beta, fpr_maxima=fpr_maxima)

    # Mantener el mismo modelo cuyo umbral se eligió en validación.
    # Reajustar incluyendo validación invalidaría ese umbral.
    modelo = provisional
    ruta_guardada = None
    if ruta_modelo:
        ruta = Path(ruta_modelo); ruta.parent.mkdir(parents=True, exist_ok=True); modelo.save_model(ruta)
        meta = {"schema_v": "evacuar-1.0", "objetivo": "evacuar", "features_version": "media5m-v1",
                "horizonte_ch4_h": HORIZONTES["ch4"] / pd.Timedelta(hours=1),
                "horizonte_co_h": HORIZONTES["co"] / pd.Timedelta(hours=1),
                "umbral_clasificacion": umbral, "metrica_objetivo": f"f{beta:g}",
                "features": columnas, "factores_lasso": [asdict(f) for f in factores],
                "hiperparametros": hiper, "seed": seed, "mina": ConfiguracionMina().como_dict()}
        ruta.with_suffix(".metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        ruta_guardada = str(ruta)

    yp = y.iloc[bloques.prueba]; prob = modelo.predict_proba(X.iloc[bloques.prueba][columnas])[:, 1]; pred = prob >= umbral
    tn, fp, fn, tp = confusion_matrix(yp, pred, labels=[0, 1]).ravel()
    return ResultadoEvacuar(
        float(HORIZONTES["ch4"] / pd.Timedelta(hours=1)), float(HORIZONTES["co"] / pd.Timedelta(hours=1)),
        float(roc_auc_score(yp, prob)), float(average_precision_score(yp, prob)),
        float(fbeta_score(yp, pred, beta=2.0, zero_division=0)), float(fbeta_score(yp, pred, beta=3.0, zero_division=0)),
        float(tp / (tp + fp)) if (tp + fp) else 0.0, float(tp / (tp + fn)) if (tp + fn) else 0.0,
        float(fp / (fp + tn)) if (fp + tn) else 0.0, umbral, float(yp.mean()), hiper, factores,
        len(bloques.entrenamiento), len(bloques.validacion), len(bloques.prueba),
        int(fp), int(fn), ruta_guardada)
