"""Genera las gráficas de predicción e importancia para el pitch."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from src.data.etiquetas import etiquetar_excedencia
from src.data.features import construir_features
from src.model.woa_xgboost import dividir_temporalmente


RAIZ = Path(__file__).resolve().parents[1]
COLOR_PREDICCION = "#126782"
COLOR_REALIDAD = "#D1495B"
COLOR_UMBRAL = "#6C757D"
COLOR_ANTICIPACION = "#F4B942"


def _cargar_predicciones() -> tuple[pd.DataFrame, XGBClassifier]:
    datos = pd.read_csv(RAIZ / "data" / "sinteticos_30d_5min.csv")
    modelo = XGBClassifier()
    modelo.load_model(RAIZ / "models" / "ch4_6h_woa_xgboost.json")
    features = construir_features(datos)
    bloques = dividir_temporalmente(features.index)
    posiciones = bloques.prueba
    columnas = modelo.get_booster().feature_names
    valores = pd.Series(datos["ch4_pct"].to_numpy(), index=features.index)
    etiqueta = etiquetar_excedencia(valores, "ch4")
    prueba = pd.DataFrame(
        {
            "ch4_pct": datos["ch4_pct"].iloc[posiciones].to_numpy(),
            "probabilidad": modelo.predict_proba(features.iloc[posiciones][columnas])[:, 1],
            "excedencia_en_6h": etiqueta.iloc[posiciones].to_numpy(),
        },
        index=features.index[posiciones],
    )
    return prueba, modelo


def _evento_representativo(prueba: pd.DataFrame) -> tuple[int, int]:
    excedida = prueba["ch4_pct"].gt(1.0)
    inicios = np.flatnonzero((excedida & ~excedida.shift(fill_value=False)).to_numpy())
    alarma = prueba["probabilidad"].ge(0.5)
    cruces = np.flatnonzero((alarma & ~alarma.shift(fill_value=False)).to_numpy())
    candidatos: list[tuple[float, int, int]] = []
    for evento in inicios:
        previas = cruces[
            (prueba.index[cruces] >= prueba.index[evento] - pd.Timedelta(hours=6))
            & (prueba.index[cruces] < prueba.index[evento])
        ]
        if len(previas):
            alarma_idx = int(previas[-1])
            pico = prueba["ch4_pct"].iloc[evento : evento + 24].max()
            candidatos.append((float(pico), alarma_idx, int(evento)))
    if not candidatos:
        raise RuntimeError("No se encontró una alarma correcta previa a una excedencia.")
    _, alarma_idx, evento_idx = max(candidatos)
    return alarma_idx, evento_idx


def grafica_prediccion(prueba: pd.DataFrame, destino: Path) -> None:
    alarma_idx, evento_idx = _evento_representativo(prueba)
    momento_alarma = prueba.index[alarma_idx]
    momento_evento = prueba.index[evento_idx]
    inicio = momento_alarma - pd.Timedelta(hours=2)
    fin = momento_evento + pd.Timedelta(hours=5)
    muestra = prueba.loc[inicio:fin]
    anticipacion = (momento_evento - momento_alarma) / pd.Timedelta(hours=1)

    fig, (ax_prob, ax_ch4) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, constrained_layout=True)
    fig.patch.set_facecolor("white")
    fig.suptitle("El modelo anticipa una excedencia de CH₄", fontsize=19, fontweight="bold")

    ax_prob.plot(muestra.index, muestra["probabilidad"], color=COLOR_PREDICCION, linewidth=2.5,
                 label="Probabilidad predicha (próximas 6 h)")
    ax_prob.step(muestra.index, muestra["excedencia_en_6h"], where="post", color=COLOR_REALIDAD,
                 linewidth=1.8, alpha=0.8, label="Realidad: habrá excedencia en ≤6 h")
    ax_prob.axhline(0.5, color=COLOR_UMBRAL, linestyle="--", linewidth=1.2, label="Umbral de alarma (50%)")
    ax_prob.set_ylim(-0.04, 1.05)
    ax_prob.set_ylabel("Probabilidad")
    ax_prob.grid(axis="y", alpha=0.2)
    ax_prob.legend(loc="upper left", ncol=3, frameon=False)

    ax_ch4.plot(muestra.index, muestra["ch4_pct"], color=COLOR_REALIDAD, linewidth=2.5, label="CH₄ medido")
    ax_ch4.axhline(1.0, color=COLOR_UMBRAL, linestyle="--", linewidth=1.4, label="Umbral Decreto 1886 (1.0%)")
    ax_ch4.axvspan(momento_alarma, momento_evento, color=COLOR_ANTICIPACION, alpha=0.22)
    for eje in (ax_prob, ax_ch4):
        eje.axvline(momento_alarma, color=COLOR_PREDICCION, linestyle=":", linewidth=2)
        eje.axvline(momento_evento, color=COLOR_REALIDAD, linestyle=":", linewidth=2)
    ax_ch4.scatter([momento_alarma], [muestra.loc[momento_alarma, "ch4_pct"]], color=COLOR_PREDICCION,
                   s=65, zorder=5)
    ax_ch4.scatter([momento_evento], [muestra.loc[momento_evento, "ch4_pct"]], color=COLOR_REALIDAD,
                   s=65, zorder=5)
    ax_ch4.annotate(f"ALARMA\n{anticipacion:.1f} h antes", xy=(momento_alarma, muestra.loc[momento_alarma, "ch4_pct"]),
                    xytext=(-5, 45), textcoords="offset points", ha="right", color=COLOR_PREDICCION,
                    fontweight="bold", arrowprops={"arrowstyle": "->", "color": COLOR_PREDICCION})
    ax_ch4.annotate("EXCEDENCIA REAL", xy=(momento_evento, muestra.loc[momento_evento, "ch4_pct"]),
                    xytext=(12, 42), textcoords="offset points", color=COLOR_REALIDAD, fontweight="bold",
                    arrowprops={"arrowstyle": "->", "color": COLOR_REALIDAD})
    ax_ch4.set_ylabel("CH₄ (% vol)")
    ax_ch4.set_xlabel("Fecha y hora (UTC)")
    ax_ch4.grid(axis="y", alpha=0.2)
    ax_ch4.legend(loc="upper left", frameon=False)
    ax_ch4.xaxis.set_major_formatter(mdates.DateFormatter("%d ago\n%H:%M", tz=prueba.index.tz))
    fig.savefig(destino, dpi=320, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def grafica_importancia(modelo: XGBClassifier, destino: Path) -> None:
    puntajes = pd.Series(modelo.get_booster().get_score(importance_type="total_gain"), dtype=float)
    nombres = {
        "hora_cos": "Hora del día",
        "produccion_ton_h": "Producción actual",
        "produccion_ton_h_lag_1h": "Producción · lag 1 h",
        "produccion_ton_h_lag_4h": "Producción · lag 4 h",
        "produccion_ton_h_lag_6h": "Producción · lag 6 h",
        "produccion_ton_h_lag_8h": "Producción · lag 8 h",
        "produccion_ton_h_lag_12h": "Producción · lag 12 h",
        "produccion_media_6h": "Producción · media 6 h",
        "presion_hpa": "Presión barométrica",
        "presion_delta_12h": "Presión · Δ12 h",
        "presion_delta_6h": "Presión · Δ6 h",
        "ventilacion_pct": "Ventilación",
        "ch4_pct_lag_1h": "CH₄ · lag 1 h",
        "ch4_pct_lag_4h": "CH₄ · lag 4 h",
        "ch4_pct_lag_8h": "CH₄ · lag 8 h",
        "ch4_pct_lag_12h": "CH₄ · lag 12 h",
    }
    top = puntajes.sort_values(ascending=False).head(12).sort_values()
    porcentaje = 100 * top / puntajes.sum()
    etiquetas = [nombres.get(nombre, nombre) for nombre in top.index]
    colores = [
        "#D99000" if nombre.startswith("produccion") else
        "#44546A" if nombre.startswith("presion") else
        "#087E8B"
        for nombre in top.index
    ]

    fig, ax = plt.subplots(figsize=(12, 8), constrained_layout=True)
    fig.patch.set_facecolor("white")
    barras = ax.barh(etiquetas, porcentaje, color=colores)
    ax.bar_label(barras, labels=[f"{valor:.1f}%" for valor in porcentaje], padding=5, fontsize=10)
    ax.set_title("Qué impulsa la predicción de excedencia de CH₄", fontsize=18, fontweight="bold", pad=18)
    ax.set_xlabel("Importancia XGBoost (ganancia total, %)")
    ax.set_xlim(0, max(porcentaje) * 1.18)
    ax.grid(axis="x", alpha=0.2)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.text(0.99, 0.02, "Producción y rezagos     Presión     Otras variables",
            transform=ax.transAxes, ha="right", color="#555555", fontsize=10)
    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destino, dpi=320, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    destino = RAIZ / "reports"
    destino.mkdir(exist_ok=True)
    prueba, modelo = _cargar_predicciones()
    ruta_prediccion = destino / "pitch_prediccion_vs_realidad.png"
    ruta_importancia = destino / "pitch_importancia_variables.png"
    grafica_prediccion(prueba, ruta_prediccion)
    grafica_importancia(modelo, ruta_importancia)
    print(ruta_prediccion)
    print(ruta_importancia)


if __name__ == "__main__":
    main()
