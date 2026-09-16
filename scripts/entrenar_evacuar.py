"""Entrena el modelo unificado "evacuar" (unión CH4/CO) y genera resultados
visuales. Reemplaza como target de producción a los modelos separados por
gas: ver `src.model.woa_xgboost.entrenar_woa_xgboost_evacuar`.

Uso:
    .venv/Scripts/python.exe -m scripts.entrenar_evacuar
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    confusion_matrix,
    fbeta_score,
    precision_recall_curve,
)
from xgboost import XGBClassifier

from src.data.etiquetas import generar_dataset_evacuar_referencia, generar_etiqueta_evacuar
from src.data.features import construir_features
from src.model.woa_xgboost import dividir_temporalmente, entrenar_woa_xgboost_evacuar

RAIZ = Path(__file__).resolve().parents[1]
COLOR_POSITIVO = "#D1495B"
COLOR_NEGATIVO = "#087E8B"
COLOR_PREDICCION = "#126782"
COLOR_UMBRAL = "#6C757D"


def _cargar_dataset(raiz_datos: Path) -> pd.DataFrame:
    partes = sorted(raiz_datos.glob("telemetria_*.csv.gz"))
    if not partes:
        raise FileNotFoundError(f"No hay particiones en {raiz_datos}")
    return pd.concat((pd.read_csv(p) for p in partes), ignore_index=True)


def _grafica_balance_clases(prevalencia: float, destino: Path) -> None:
    fig, ax = plt.subplots(figsize=(6, 5), constrained_layout=True)
    fig.patch.set_facecolor("white")
    valores = [1 - prevalencia, prevalencia]
    barras = ax.bar(["No evacuar", "Evacuar"], valores, color=[COLOR_NEGATIVO, COLOR_POSITIVO])
    ax.bar_label(barras, labels=[f"{v * 100:.2f}%" for v in valores], padding=6, fontsize=12, fontweight="bold")
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("Proporción del dataset")
    ax.set_title("Balance de clases — etiqueta \"evacuar\"", fontsize=15, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(destino, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _grafica_umbral(y: pd.Series, prob: np.ndarray, umbral_elegido: float, destino: Path) -> None:
    umbrales = np.linspace(.01, .99, 197)
    precision, recall, f2, f3 = [], [], [], []
    for u in umbrales:
        pred = prob >= u
        tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
        precision.append(tp / (tp + fp) if (tp + fp) else 0.0)
        recall.append(tp / (tp + fn) if (tp + fn) else 0.0)
        f2.append(fbeta_score(y, pred, beta=2.0, zero_division=0))
        f3.append(fbeta_score(y, pred, beta=3.0, zero_division=0))

    fig, ax = plt.subplots(figsize=(10, 6), constrained_layout=True)
    fig.patch.set_facecolor("white")
    ax.plot(umbrales, recall, label="Recall (sensibilidad)", color=COLOR_POSITIVO, linewidth=2.2)
    ax.plot(umbrales, precision, label="Precisión", color=COLOR_NEGATIVO, linewidth=2.2)
    ax.plot(umbrales, f2, label="F2 (objetivo)", color=COLOR_PREDICCION, linewidth=2.6, linestyle="--")
    ax.plot(umbrales, f3, label="F3 (referencia)", color="#8E7CC3", linewidth=1.6, linestyle=":")
    ax.axvline(umbral_elegido, color=COLOR_UMBRAL, linestyle="-", linewidth=1.4,
               label=f"Umbral elegido ({umbral_elegido:.2f})")
    ax.set_xlabel("Umbral de decisión")
    ax.set_ylabel("Puntaje")
    ax.set_ylim(-0.02, 1.02)
    ax.set_title("Recall / Precisión / F-beta vs. umbral (conjunto de prueba)", fontsize=15, fontweight="bold")
    ax.grid(alpha=0.2)
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False)
    fig.savefig(destino, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _grafica_matriz_confusion(y: pd.Series, pred: np.ndarray, destino: Path) -> None:
    fig, ax = plt.subplots(figsize=(6, 6), constrained_layout=True)
    fig.patch.set_facecolor("white")
    ConfusionMatrixDisplay(confusion_matrix(y, pred, labels=[0, 1]),
                           display_labels=["No evacuar", "Evacuar"]).plot(
        ax=ax, cmap="Blues", colorbar=False, values_format="d")
    ax.set_title("Matriz de confusión — conjunto de prueba", fontsize=14, fontweight="bold")
    fig.savefig(destino, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _grafica_importancia(modelo: XGBClassifier, destino: Path) -> None:
    puntajes = pd.Series(modelo.get_booster().get_score(importance_type="total_gain"), dtype=float)
    top = puntajes.sort_values(ascending=False).head(12).sort_values()
    porcentaje = 100 * top / puntajes.sum()
    colores = [
        "#D99000" if n.startswith("produccion") else
        "#44546A" if n.startswith("presion") else
        "#D1495B" if n.startswith("ch4") else
        "#087E8B" if n.startswith("co_ppm") else
        "#6C757D"
        for n in top.index
    ]
    fig, ax = plt.subplots(figsize=(11, 7), constrained_layout=True)
    fig.patch.set_facecolor("white")
    barras = ax.barh(top.index, porcentaje, color=colores)
    ax.bar_label(barras, labels=[f"{v:.1f}%" for v in porcentaje], padding=5, fontsize=9)
    ax.set_title("Qué impulsa la predicción de \"evacuar\"", fontsize=16, fontweight="bold")
    ax.set_xlabel("Importancia XGBoost (ganancia total, %)")
    ax.spines[["top", "right", "left"]].set_visible(False)
    fig.savefig(destino, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _grafica_serie_temporal(muestra: pd.DataFrame, destino: Path) -> None:
    fig, (ax_prob, ax_gas) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, constrained_layout=True)
    fig.patch.set_facecolor("white")
    fig.suptitle("Predicción de \"evacuar\" vs. realidad", fontsize=18, fontweight="bold")

    ax_prob.plot(muestra.index, muestra["probabilidad"], color=COLOR_PREDICCION, linewidth=2.2,
                 label="Probabilidad predicha de evacuar")
    ax_prob.step(muestra.index, muestra["evacuar"], where="post", color=COLOR_POSITIVO,
                 linewidth=1.6, alpha=0.75, label="Realidad: evacuar")
    ax_prob.set_ylim(-0.04, 1.05)
    ax_prob.set_ylabel("Probabilidad")
    ax_prob.grid(axis="y", alpha=0.2)
    ax_prob.legend(loc="upper left", frameon=False)

    ax_gas.plot(muestra.index, muestra["ch4_pct"], color=COLOR_POSITIVO, linewidth=2.0, label="CH₄ (% vol)")
    ax_gas.axhline(1.0, color=COLOR_POSITIVO, linestyle="--", linewidth=1.1, alpha=0.6)
    ax_co = ax_gas.twinx()
    ax_co.plot(muestra.index, muestra["co_ppm"], color=COLOR_NEGATIVO, linewidth=1.6, label="CO (ppm)", alpha=0.85)
    ax_co.axhline(25.0, color=COLOR_NEGATIVO, linestyle="--", linewidth=1.1, alpha=0.6)
    ax_gas.set_ylabel("CH₄ (% vol)")
    ax_co.set_ylabel("CO (ppm)")
    ax_gas.set_xlabel("Fecha y hora (UTC)")
    lineas1, etiquetas1 = ax_gas.get_legend_handles_labels()
    lineas2, etiquetas2 = ax_co.get_legend_handles_labels()
    ax_gas.legend(lineas1 + lineas2, etiquetas1 + etiquetas2, loc="upper left", frameon=False)
    fig.savefig(destino, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _grafica_limites_normativos(muestra: pd.DataFrame, destino: Path) -> None:
    fig, (ax_ch4, ax_co, ax_o2) = plt.subplots(3, 1, figsize=(13, 9), sharex=True, constrained_layout=True)
    fig.patch.set_facecolor("white")
    fig.suptitle("Lecturas vs. límites normativos (Decreto 1886)", fontsize=17, fontweight="bold")

    ax_ch4.plot(muestra.index, muestra["ch4_pct"], color=COLOR_POSITIVO, linewidth=1.8, label="CH₄ (% vol)")
    ax_ch4.axhline(muestra["ch4_limite_dec1886"].iloc[0], color=COLOR_UMBRAL, linestyle="--",
                   linewidth=1.2, label="Límite Decreto 1886 (1.0 %vol = 20 % LEL)")
    ax_ch4.axhline(muestra["ch4_lel"].iloc[0], color="#8E7CC3", linestyle=":", linewidth=1.4,
                   label="LEL — 100 % (5.0 %vol)")
    ax_ch4.set_ylabel("CH₄ (% vol)")
    ax_ch4.legend(loc="upper left", frameon=False, fontsize=8)

    ax_co.plot(muestra.index, muestra["co_ppm"], color=COLOR_NEGATIVO, linewidth=1.8, label="CO (ppm)")
    ax_co.axhline(muestra["co_limite_dec1886"].iloc[0], color=COLOR_UMBRAL, linestyle="--",
                  linewidth=1.2, label="TLV-TWA 25 ppm (Decreto 1886)")
    ax_co.set_ylabel("CO (ppm)")
    ax_co.legend(loc="upper left", frameon=False, fontsize=8)

    ax_o2.plot(muestra.index, muestra["o2_pct"], color=COLOR_PREDICCION, linewidth=1.8, label="O₂ (% vol)")
    ax_o2.axhline(muestra["o2_min_permisible"].iloc[0], color=COLOR_UMBRAL, linestyle="--",
                  linewidth=1.2, label="Mínimo permisible 19.5 %")
    ax_o2.axhline(muestra["o2_max_permisible"].iloc[0], color=COLOR_UMBRAL, linestyle=":",
                  linewidth=1.2, label="Máximo permisible 23.5 %")
    ax_o2.set_ylabel("O₂ (% vol)")
    ax_o2.set_xlabel("Fecha y hora (UTC)")
    ax_o2.legend(loc="lower left", frameon=False, fontsize=8)
    fig.savefig(destino, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=RAIZ, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _escribir_manifiesto(destino_modelo: Path, raiz_datos: Path) -> None:
    """Manifiesto de procedencia del artefacto (A14 auditoría): de qué
    dataset y commit sale este modelo, para que "cuál .json es este" nunca
    sea una pregunta sin respuesta al llegar a la Pi."""
    dataset_manifest = json.loads((raiz_datos / "manifest.json").read_text(encoding="utf-8"))
    manifiesto = {
        "generado_en": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_commit": _git_commit(),
        "python_version": sys.version,
        "plataforma_entrenamiento": platform.platform(),
        "dataset": {
            "ruta": str(raiz_datos), "dias": dataset_manifest.get("dias"),
            "seed": dataset_manifest.get("seed"), "filas": dataset_manifest.get("filas"),
            "trayectoria_continua": dataset_manifest.get("trayectoria_continua", False),
        },
    }
    (destino_modelo / "manifiesto.json").write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    raiz_datos = RAIZ / "data" / "sintetico_6m_15s"
    datos = _cargar_dataset(raiz_datos)
    destino_modelo = RAIZ / "models" / "evacuar"
    destino_reportes = RAIZ / "reports" / "evacuar"
    destino_modelo.mkdir(parents=True, exist_ok=True)
    destino_reportes.mkdir(parents=True, exist_ok=True)

    resultado = entrenar_woa_xgboost_evacuar(
        datos, ruta_modelo=destino_modelo / "evacuar_woa_xgboost.json")
    (destino_modelo / "metricas.json").write_text(
        json.dumps(asdict(resultado), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    _escribir_manifiesto(destino_modelo, raiz_datos)
    print("AUC:", resultado.auc, "AP:", resultado.average_precision)
    print("F2:", resultado.f2, "F3:", resultado.f3)
    print("Precisión:", resultado.precision, "Recall:", resultado.sensibilidad)
    print("Tasa falsas alarmas:", resultado.tasa_falsas_alarmas, "Umbral:", resultado.umbral_clasificacion)
    print("Prevalencia prueba:", resultado.prevalencia_prueba)

    # --- Reconstruir X/y/prob de prueba para las gráficas (mismos bloques
    # temporales y misma versión de features que usó el entrenamiento) ---
    X = construir_features(datos, version="media5m-v1")
    etiquetas = generar_etiqueta_evacuar(datos)
    conjunto = X.join(etiquetas["evacuar"]).dropna(subset=["evacuar"])
    Xc, yc = conjunto.drop(columns="evacuar"), conjunto["evacuar"].astype("int8")
    bloques = dividir_temporalmente(Xc.index, purga=pd.Timedelta(hours=6))

    modelo = XGBClassifier()
    modelo.load_model(destino_modelo / "evacuar_woa_xgboost.json")
    columnas = modelo.get_booster().feature_names
    yp = yc.iloc[bloques.prueba]
    prob = modelo.predict_proba(Xc.iloc[bloques.prueba][columnas])[:, 1]
    pred = prob >= resultado.umbral_clasificacion

    _grafica_balance_clases(float(etiquetas["evacuar"].mean()), destino_reportes / "balance_clases.png")
    _grafica_umbral(yp, prob, resultado.umbral_clasificacion, destino_reportes / "umbral_recall_precision_f2.png")
    _grafica_matriz_confusion(yp, pred, destino_reportes / "matriz_confusion.png")
    _grafica_importancia(modelo, destino_reportes / "importancia_variables.png")

    # Ventana de ejemplo para la serie temporal: un tramo del conjunto de
    # prueba centrado en el primer evento positivo real.
    prueba_idx = conjunto.index[bloques.prueba]
    serie = pd.DataFrame({
        "ch4_pct": conjunto["ch4_pct"].iloc[bloques.prueba].to_numpy(),
        "co_ppm": conjunto["co_ppm"].iloc[bloques.prueba].to_numpy(),
        "evacuar": yp.to_numpy(),
        "probabilidad": prob,
    }, index=prueba_idx)
    positivos = serie.index[serie["evacuar"] == 1]
    # Dataset "evacuar" con TLV-TWA/STEL/límite Decreto 1886/LEL al lado de
    # cada gas (CH4, CO, O2) — contexto normativo para lectura humana, no
    # features del modelo.
    referencia = generar_dataset_evacuar_referencia(datos)
    referencia.to_csv(destino_reportes / "dataset_evacuar_referencia.csv.gz", compression="gzip")
    referencia.head(2000).to_csv(destino_reportes / "muestra_evacuar.csv")
    if len(positivos):
        centro = positivos[0]
        ventana = serie.loc[centro - pd.Timedelta(hours=8): centro + pd.Timedelta(hours=8)]
        _grafica_serie_temporal(ventana, destino_reportes / "prediccion_vs_realidad.png")
        ventana_ref = referencia.loc[centro - pd.Timedelta(hours=8): centro + pd.Timedelta(hours=8)]
        _grafica_limites_normativos(ventana_ref, destino_reportes / "limites_normativos.png")
    print("Gráficas y dataset en:", destino_reportes)


if __name__ == "__main__":
    main()
