"""Umbrales del Decreto 1886 de 2015.

Esta es la única fuente de valores normativos dentro del track ML. Los
módulos de etiquetas, alarmas y modelos deben importar ``UMBRALES``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Sentido(str, Enum):
    MAXIMO = "maximo"
    MINIMO = "minimo"
    RANGO = "rango"


@dataclass(frozen=True)
class Umbral:
    unidad: str
    sentido: Sentido
    valor: float | None = None
    minimo: float | None = None
    maximo: float | None = None
    tipo_exposicion: str | None = None
    monitoreado: bool = True
    stel: float | None = None  # None si el Decreto 1886 no define un límite de corta duración
    lel: float | None = None  # límite físico de explosividad, no un valor de exposición ACGIH


UMBRALES: dict[str, Umbral] = {
    "o2": Umbral("pct", Sentido.RANGO, minimo=19.5, maximo=23.5),
    "ch4": Umbral("pct", Sentido.MAXIMO, valor=1.0, tipo_exposicion="20% LEL (limite unico art. 39)", lel=5.0),
    "co2": Umbral("pct", Sentido.MAXIMO, valor=0.5, tipo_exposicion="TLV-TWA", stel=3.0),
    "co": Umbral("ppm", Sentido.MAXIMO, valor=25.0, tipo_exposicion="TLV-TWA"),
    "h2s": Umbral("ppm", Sentido.MAXIMO, valor=1.0, tipo_exposicion="TLV-TWA", stel=5.0),
    "h2s_stel": Umbral("ppm", Sentido.MAXIMO, valor=5.0, tipo_exposicion="STEL"),
    "so2": Umbral("ppm", Sentido.MAXIMO, valor=0.25, tipo_exposicion="STEL", monitoreado=False),
    "no2": Umbral("ppm", Sentido.MAXIMO, valor=0.2, tipo_exposicion="TLV-TWA", monitoreado=False),
}


def columnas_referencia(gas: str) -> dict[str, float]:
    """Columnas estáticas de referencia normativa para anexar junto a la
    lectura de ``gas`` en un dataset (TLV-TWA, STEL, límite del Decreto 1886
    y LEL). Para gases de rango (p. ej. O2) se devuelven mínimo/máximo
    permisibles en vez de TWA/STEL, que no aplican a ese tipo de límite.
    Los límites que el decreto no define quedan en ``float('nan')``, nunca en
    0 (0 significaría "el decreto exige cero", que es falso).
    """
    nan = float("nan")
    umbral = UMBRALES[gas]
    if umbral.sentido is Sentido.RANGO:
        return {f"{gas}_min_permisible": umbral.minimo, f"{gas}_max_permisible": umbral.maximo}
    return {
        f"{gas}_tlv_twa": umbral.valor if umbral.tipo_exposicion != "STEL" else nan,
        f"{gas}_stel": umbral.stel if umbral.stel is not None else nan,
        f"{gas}_limite_dec1886": umbral.valor,
        f"{gas}_lel": umbral.lel if umbral.lel is not None else nan,
    }


def excede(gas: str, valor: float) -> bool:
    """Indica si una lectura está fuera del límite normativo."""
    umbral = UMBRALES[gas]
    if umbral.sentido is Sentido.RANGO:
        assert umbral.minimo is not None and umbral.maximo is not None
        return valor < umbral.minimo or valor > umbral.maximo
    assert umbral.valor is not None
    if umbral.sentido is Sentido.MINIMO:
        return valor < umbral.valor
    return valor > umbral.valor


FRACCION_ATENCION = 0.80
FRACCION_ATENCION_LEL = 0.80  # sobre el 100% LEL, no sobre el límite del Decreto 1886


def porcentaje_limite(gas: str, valor: float) -> float:
    """Fracción de `valor` sobre el límite normativo del Decreto 1886
    (>1.0 = excedido). Solo aplica a gases con excedencia hacia arriba
    (Sentido.MAXIMO); O2 usa un rango permisible, no un único límite al que
    "acercarse", así que no lo soporta esta función."""
    umbral = UMBRALES[gas]
    if umbral.sentido is not Sentido.MAXIMO:
        raise ValueError(f"porcentaje_limite no aplica a {gas!r} (sentido {umbral.sentido}).")
    assert umbral.valor is not None
    return valor / umbral.valor


def nivel_proximidad(gas: str, valor: float, *, fraccion_atencion: float = FRACCION_ATENCION) -> str:
    """'normal' | 'atencion' | 'excedido' | 'riesgo_explosivo' según qué tan
    cerca está `valor` del límite normativo. 'atencion' es una alerta
    temprana (por defecto 80% del límite) para que el ingeniero verifique
    antes de que se excede — no es una etiqueta regulatoria del Decreto 1886.

    Para CH4, que además tiene un LEL (límite de explosividad) muy por
    encima del límite regulatorio (5.0 %vol vs. 1.0 %vol = 20% LEL),
    acercarse al LEL completo es un riesgo cualitativamente distinto
    (explosivo, no solo de exposición) y se reporta aparte como
    'riesgo_explosivo', incluso si ya está 'excedido' respecto al límite
    normativo.
    """
    umbral = UMBRALES[gas]
    if umbral.lel is not None and valor >= umbral.lel * FRACCION_ATENCION_LEL:
        return "riesgo_explosivo"
    fraccion = porcentaje_limite(gas, valor)
    if fraccion > 1.0:
        return "excedido"
    if fraccion >= fraccion_atencion:
        return "atencion"
    return "normal"
