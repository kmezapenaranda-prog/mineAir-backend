import pandas as pd

from src.data.generador import ConfiguracionGenerador, generar_datos_sinteticos


def test_generador_v2_es_reproducible_y_cada_15_segundos():
    cfg = ConfiguracionGenerador(dias=2, seed=1886)
    a, b = generar_datos_sinteticos(cfg), generar_datos_sinteticos(cfg)
    pd.testing.assert_frame_equal(a, b)
    assert a.timestamp.diff().dropna().eq(pd.Timedelta(seconds=15)).all()
    assert len(a) == 2 * 24 * 60 * 4


def test_operacion_y_fisica_configuradas():
    d = generar_datos_sinteticos(ConfiguracionGenerador(dias=30))
    assert d.voladura.sum() == 30 * 4
    assert set(d.turno.unique()) == {0, 1, 2}
    assert 3.7 <= d.caudal_m3_s.median() <= 4.0
    assert 972 <= d.presion_hpa.median() <= 979
    assert .30 <= d.ch4_pct.quantile(.25) <= .40
    assert .35 <= d.ch4_pct.median() <= .42
    assert d.ch4_pct.isna().any() and (~d.sensor_ok).any()


def test_hay_eventos_raros_para_ambos_objetivos():
    d = generar_datos_sinteticos(ConfiguracionGenerador(dias=30))
    tasa_ch4 = d.ch4_pct.gt(1).mean()
    tasa_co = d.co_ppm.gt(25).mean()
    assert 0 < tasa_ch4 < .01
    assert 0 < tasa_co < .02
