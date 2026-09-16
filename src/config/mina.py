"""Parámetros físicos y operacionales del piloto MineAIr en Sardinata."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ConfiguracionMina:
    metodo_explotacion: str = "camara_y_pilares"
    frentes_activos: int = 1
    frentes_sellados: int = 1
    ancho_seccion_m: float = 1.80
    alto_seccion_m: float = 2.00
    espesor_manto_medio_m: float = 1.10
    espesor_manto_desv_m: float = 0.12
    trabajadores_por_turno: int = 20
    turnos_por_dia: int = 2
    horas_por_turno: int = 8
    produccion_nominal_persona_ton_h: float = 1.0
    voladuras_por_turno: int = 2
    gasificacion_media_m3_ton: float = 4.0
    gasificacion_desv_m3_ton: float = 0.45
    caudal_diseno_m3_s: float = 4.0
    caudal_min_operacional_m3_s: float = 3.7
    longitud_equivalente_ducto_m: float = 420.0
    curvas_equivalentes: int = 6
    altitud_referencia_m: float = 320.0
    nombre_referencia_barometrica: str = "Estacion Sardinata IDEAM (320 m s. n. m.)"

    @property
    def area_seccion_m2(self) -> float:
        return self.ancho_seccion_m * self.alto_seccion_m

    @property
    def diametro_hidraulico_m(self) -> float:
        return 4 * self.area_seccion_m2 / (2 * (self.ancho_seccion_m + self.alto_seccion_m))

    @property
    def produccion_nominal_turno_ton_h(self) -> float:
        return self.trabajadores_por_turno * self.produccion_nominal_persona_ton_h

    @property
    def presion_isa_hpa(self) -> float:
        return 1013.25 * (1 - 2.25577e-5 * self.altitud_referencia_m) ** 5.25588

    def como_dict(self) -> dict[str, object]:
        return {**asdict(self), "area_seccion_m2": self.area_seccion_m2,
                "diametro_hidraulico_m": self.diametro_hidraulico_m,
                "presion_isa_hpa": self.presion_isa_hpa}
