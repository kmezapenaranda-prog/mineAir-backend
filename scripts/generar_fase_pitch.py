"""Genera el dataset y la figura de validación física de la fase pitch."""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.data.generador import ConfiguracionGenerador, generar_datos_sinteticos


RAIZ = Path(__file__).resolve().parents[1]


def main() -> None:
    datos, _, voladuras = generar_datos_sinteticos(ConfiguracionGenerador(), devolver_crudos=True)
    ruta_csv = RAIZ / "data" / "sinteticos_30d_5min.csv"
    ruta_png = RAIZ / "reports" / "fisica_sintetica_30d.png"
    ruta_csv.parent.mkdir(exist_ok=True)
    ruta_png.parent.mkdir(exist_ok=True)
    datos.to_csv(ruta_csv, index=False)

    lags = np.arange(0, 12.25, 0.25)
    correlaciones = np.array([
        datos["produccion_ton_h"].corr(datos["ch4_pct"].shift(-round(lag * 12)))
        for lag in lags
    ])
    mejor_lag = float(lags[np.nanargmax(correlaciones)])
    corr_presion = float(datos["ch4_pct"].corr(datos["presion_hpa"]))

    fig, ejes = plt.subplots(2, 2, figsize=(14, 8), constrained_layout=True)
    muestra = datos.iloc[: 3 * 24 * 12]
    ax = ejes[0, 0]
    ax.plot(muestra["timestamp"], muestra["produccion_ton_h"], color="#d99000", label="Producción (t/h)")
    ax2 = ax.twinx()
    ax2.plot(muestra["timestamp"], muestra["ch4_pct"], color="#087e8b", label="CH₄ (% vol)")
    ax.set_title("CH₄ responde después de la producción")
    ax.set_ylabel("Producción (t/h)")
    ax2.set_ylabel("CH₄ (% vol)")

    ejes[0, 1].plot(lags, correlaciones, color="#087e8b")
    ejes[0, 1].axvspan(4, 8, color="#d99000", alpha=0.18, label="ventana física 4–8 h")
    ejes[0, 1].axvline(mejor_lag, color="#c23b22", linestyle="--", label=f"máximo: {mejor_lag:.2f} h")
    ejes[0, 1].set(title="Correlación cruzada producción → CH₄", xlabel="Rezago (h)", ylabel="Correlación")
    ejes[0, 1].legend()

    ejes[1, 0].scatter(datos["presion_hpa"], datos["ch4_pct"], s=3, alpha=0.18, color="#44546a")
    ajuste = np.polyfit(datos["presion_hpa"], datos["ch4_pct"], 1)
    x = np.linspace(datos["presion_hpa"].min(), datos["presion_hpa"].max(), 100)
    ejes[1, 0].plot(x, np.polyval(ajuste, x), color="#c23b22", linewidth=2)
    ejes[1, 0].set(title=f"CH₄ vs presión: r = {corr_presion:.3f}", xlabel="Presión (hPa)", ylabel="CH₄ (% vol)")

    evento = voladuras.iloc[len(voladuras) // 2]["timestamp"]
    ventana = datos.set_index("timestamp").loc[evento - pd.Timedelta(hours=1):evento + pd.Timedelta(hours=5)]
    ejes[1, 1].plot(ventana.index, ventana["co_ppm"], color="#7b2cbf")
    ejes[1, 1].axvline(evento, color="#c23b22", linestyle="--", label="voladura")
    ejes[1, 1].set(title="Pulso de CO después de una voladura", ylabel="CO (ppm)")
    ejes[1, 1].legend()

    fig.suptitle("MineAIr — validación de la física sintética (30 días, datos a 5 min)", fontsize=15)
    fig.savefig(ruta_png, dpi=160)
    plt.close(fig)
    print(f"filas={len(datos)}")
    print(f"mejor_rezago_h={mejor_lag:.2f}")
    print(f"corr_ch4_presion={corr_presion:.6f}")
    print(f"csv={ruta_csv}")
    print(f"grafica={ruta_png}")


if __name__ == "__main__":
    main()
