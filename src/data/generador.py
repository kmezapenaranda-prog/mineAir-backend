"""Generador físico reproducible de operación minera a 15 segundos.

Los datos son sintéticos y sirven para verificar el pipeline; no representan
validación en una mina real.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

from src.config.mina import ConfiguracionMina

SEED_FIJO = 1886
CADENCIA_SENSOR = "15s"
PASO_CH4_PCT = 0.05


@dataclass(frozen=True)
class ConfiguracionGenerador:
    inicio: str = "2025-01-01T00:00:00Z"
    dias: int = 183
    seed: int = SEED_FIJO
    cadencia_s: int = 15
    mina: ConfiguracionMina = ConfiguracionMina()
    # Severidad de fallas de ventilación (rango del multiplicador de caudal
    # durante la falla) y del pulso de CO por voladura. Calibrados para que
    # la etiqueta "evacuar" (unión CH4/CO, ventana de predicción) ronde el
    # 2 % de prevalencia en el dataset anual — ver scripts/entrenar_evacuar.py.
    factor_caudal_falla: tuple[float, float] = (.65, .88)
    coeficiente_pulso_co: float = 81.0


def _ewm(a: np.ndarray, semivida_pasos: float) -> np.ndarray:
    return pd.Series(a, copy=False).ewm(halflife=semivida_pasos, adjust=False).mean().to_numpy()


def _voladuras(indice: pd.DatetimeIndex, cantidad_turno: int,
               rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    impulso = np.zeros(len(indice), dtype=np.float32)
    kg = np.zeros(len(indice), dtype=np.float32)
    horas = (9.0, 12.0)[:cantidad_turno] + (17.0, 20.0)[:cantidad_turno]
    for dia in pd.date_range(indice[0].normalize(), indice[-1].normalize(), freq="1D", tz="UTC"):
        for hora in horas:
            instante = dia + pd.Timedelta(hours=hora, minutes=int(rng.integers(-12, 13)))
            pos = int((instante - indice[0]).total_seconds() // 15)
            if 0 <= pos < len(indice):
                impulso[pos] = 1
                kg[pos] = rng.uniform(18, 32)
    return impulso, kg


def generar_datos_sinteticos(config: ConfiguracionGenerador | None = None) -> pd.DataFrame:
    config = config or ConfiguracionGenerador()
    if config.cadencia_s != 15:
        raise ValueError("El contrato del piloto exige cadencia de 15 segundos.")
    if config.dias < 1:
        raise ValueError("`dias` debe ser positivo.")
    mina, rng = config.mina, np.random.default_rng(config.seed)
    n = config.dias * 24 * 60 * 4
    inicio = pd.Timestamp(config.inicio)
    inicio = inicio.tz_localize("UTC") if inicio.tzinfo is None else inicio.tz_convert("UTC")
    indice = pd.date_range(inicio, periods=n, freq=CADENCIA_SENSOR)
    hora = (indice.hour * 3600 + indice.minute * 60 + indice.second) / 3600
    dia_n = np.arange(n) / (24 * 240)

    turno = np.where((hora >= 6) & (hora < 14), 1, np.where((hora >= 14) & (hora < 22), 2, 0))
    activo = turno > 0
    mecanico = activo & (((indice.dayofyear.to_numpy() + turno) % 3) != 0)
    disponibilidad = np.clip(.74 + .08 * np.sin(2 * np.pi * dia_n / 9) + rng.normal(0, .035, n), .5, .92)
    produccion = mina.produccion_nominal_turno_ton_h * disponibilidad * np.where(mecanico, 1, .78) * activo
    impulso, kg = _voladuras(indice, mina.voladuras_por_turno, rng)
    # 20 min antes y 35 min después sin producción por evacuación/ventilación.
    antes, despues = 80, 140
    pausa = np.convolve(impulso, np.ones(antes + despues + 1), mode="full")[antes:antes + n] > 0
    produccion[pausa] = 0
    produccion = np.clip(produccion + rng.normal(0, .35, n) * activo, 0, None).astype(np.float32)

    semanas = max(2, config.dias // 7 + 2)
    espesor_sem = np.clip(mina.espesor_manto_medio_m + rng.normal(0, mina.espesor_manto_desv_m, semanas), .75, 1.45)
    espesor = np.interp(dia_n / 7, np.arange(semanas), espesor_sem).astype(np.float32)

    paseo = np.cumsum(rng.normal(0, .0022, n)); paseo -= _ewm(paseo, 14 * 24 * 240)
    presion = (mina.presion_isa_hpa + 2.7 * np.sin(2 * np.pi * dia_n / 5.7)
               + .65 * np.sin(2 * np.pi * hora / 24) + paseo).astype(np.float32)

    pared = 1 + .055 * np.sin(2 * np.pi * dia_n / 31) + _ewm(rng.normal(0, .002, n), 24 * 240)
    resistencia = np.clip(pared * (1 + mina.curvas_equivalentes * .006), .98, 1.17)
    caudal = mina.caudal_diseno_m3_s / np.sqrt(resistencia) + rng.normal(0, .018, n)
    # Aproximadamente cuatro incidentes al mes, distribuidos a lo largo del
    # tiempo para que train/validación/prueba contengan eventos sin inflarlos.
    cantidad_fallas = max(3, round(48 * config.dias / 365))
    centros = np.linspace(24 * 240, n - 24 * 240, cantidad_fallas, dtype=int)
    jitter = rng.integers(-12 * 240, 12 * 240 + 1, cantidad_fallas)
    falla_lo, falla_hi = config.factor_caudal_falla
    for pos in np.clip(centros + jitter, 0, n - 1):
        dur = int(rng.integers(10 * 60 // 15, 40 * 60 // 15))
        caudal[pos:pos + dur] *= rng.uniform(falla_lo, falla_hi)
    caudal = np.clip(caudal, 1.8, 4.05).astype(np.float32)

    gasificacion = rng.normal(mina.gasificacion_media_m3_ton, mina.gasificacion_desv_m3_ton, n)
    gasificacion *= np.clip(espesor / mina.espesor_manto_medio_m, .75, 1.3)
    gasificacion = _ewm(np.clip(gasificacion, 2.5, 6), 2 * 240).astype(np.float32)

    fuente = produccion * gasificacion
    retrasada = np.zeros(n, dtype=np.float32); retrasada[4 * 240:] = fuente[:-4 * 240]
    emision = _ewm(retrasada, 2 * 240)
    delta_p_6h = np.zeros(n, dtype=np.float32); delta_p_6h[6 * 240:] = presion[6 * 240:] - presion[:-6 * 240]
    # Frente sellado: liberación aumenta cuando cae la presión barométrica.
    sellado = .025 + .045 * np.maximum(-delta_p_6h, 0) + .012 * np.sin(2 * np.pi * dia_n / 11)
    ch4_real = .30 + .0065 * emision / np.maximum(caudal, .5) + sellado
    # La pérdida fuerte de caudal tiene efecto no lineal; en régimen 3.7–4.0
    # no se fabrica una alarma, pero una falla sí puede acumular metano.
    ch4_real += .80 * np.maximum(3.5 - caudal, 0) + rng.normal(0, .012, n)
    ch4 = (np.round(np.clip(ch4_real, .05, 2.5) / PASO_CH4_PCT) * PASO_CH4_PCT).astype(np.float32)

    pulso_co = _ewm(impulso * np.maximum(kg, 1), 22 * 60 / 15)
    co = (4.5 + config.coeficiente_pulso_co * pulso_co + .08 * produccion
          + 8 * np.maximum(3.7 - caudal, 0) + rng.normal(0, .8, n)).clip(0, 500).astype(np.float32)

    # O2 no es objetivo predictivo (control reactivo, ver CLAUDE-ml.md); se
    # simula por completitud del monitoreo: se consume durante la voladura y
    # se desplaza cuando sube el CH4, y se recupera con la ventilación.
    o2 = (20.9 - .06 * pulso_co - 1.0 * np.maximum(ch4_real - .3, 0)
          - .5 * np.maximum(3.7 - caudal, 0) + rng.normal(0, .08, n)).clip(17.0, 20.9).astype(np.float32)

    sensor_ok = rng.random(n) > .0008
    recibido = rng.random(n) > .0015
    invalidos = ~sensor_ok | ~recibido
    ch4_referencia = ch4.copy()
    co_referencia = co.copy()
    ch4[invalidos] = np.nan; co[invalidos] = np.nan; o2[invalidos] = np.nan
    return pd.DataFrame({
        "timestamp": indice, "node_id": "S1", "frente": "activo",
        "metodo_explotacion": mina.metodo_explotacion, "turno": turno.astype(np.int8),
        "trabajadores": (activo * mina.trabajadores_por_turno).astype(np.int8),
        "arranque_mecanico": mecanico.astype(np.int8), "voladura": impulso.astype(np.int8),
        "kg_voladura": kg, "produccion_ton_h": produccion, "espesor_manto_m": espesor,
        "gasificacion_m3_ton": gasificacion, "caudal_m3_s": caudal,
        "resistencia_relativa": resistencia.astype(np.float32), "presion_hpa": presion,
        "ch4_pct": ch4, "co_ppm": co, "o2_pct": o2,
        "sensor_ok": sensor_ok, "paquete_recibido": recibido,
        "ch4_referencia_pct": ch4_referencia, "co_referencia_ppm": co_referencia,
    })


def generar_por_meses(config: ConfiguracionGenerador | None = None) -> Iterator[tuple[str, pd.DataFrame]]:
    config = config or ConfiguracionGenerador()
    # Generar una trayectoria continua antes de particionarla conserva la
    # memoria física de 4–12h y todos los parámetros. Se ejecuta en el PC
    # de entrenamiento; requiere memoria para el periodo completo.
    datos = generar_datos_sinteticos(config)
    for nombre, parte in datos.groupby(datos.timestamp.dt.strftime('%Y-%m'), sort=True):
        yield nombre, parte.reset_index(drop=True)


def generar_dataset(destino: str | Path, config: ConfiguracionGenerador | None = None) -> Path:
    config = config or ConfiguracionGenerador()
    if config.dias < 28:
        raise ValueError("El dataset debe contener por lo menos 28 días.")
    raiz = Path(destino); raiz.mkdir(parents=True, exist_ok=True)
    partes, filas = [], 0
    for nombre, datos in generar_por_meses(config):
        ruta = raiz / f"telemetria_{nombre}.csv.gz"
        datos.to_csv(ruta, index=False, compression="gzip"); partes.append(ruta.name); filas += len(datos)
    manifiesto = {"schema_v": "2.0", "sintetico": True, "cadencia_s": 15,
                  "dias": config.dias, "filas": filas, "seed": config.seed,
                  "particiones": partes, "mina": config.mina.como_dict(),
                  "configuracion_generador": asdict(config), "trayectoria_continua": True}
    (raiz / "manifest.json").write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")
    return raiz


def generar_30_dias(destino: str | Path | None = None) -> pd.DataFrame:
    datos = generar_datos_sinteticos(ConfiguracionGenerador(dias=30))
    if destino is not None:
        ruta = Path(destino); ruta.parent.mkdir(parents=True, exist_ok=True); datos.to_csv(ruta, index=False)
    return datos
