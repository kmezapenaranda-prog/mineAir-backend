import pytest

from src.config.umbrales import UMBRALES, Sentido, excede, nivel_proximidad, porcentaje_limite


def test_ch4_excede_hacia_arriba():
    assert excede("ch4", 1.01) is True
    assert excede("ch4", 1.0) is False


def test_o2_excede_fuera_del_rango():
    assert excede("o2", 19.49) is True
    assert excede("o2", 19.5) is False
    assert excede("o2", 20.9) is False
    assert excede("o2", 23.51) is True


def test_valores_decreto_1886_vigente():
    assert (UMBRALES["o2"].minimo, UMBRALES["o2"].maximo) == (19.5, 23.5)
    esperados = {"ch4": 1.0, "co2": 0.5, "co": 25.0, "h2s": 1.0,
                 "h2s_stel": 5.0, "so2": 0.25, "no2": 0.2}
    assert {gas: UMBRALES[gas].valor for gas in esperados} == esperados


@pytest.mark.parametrize("gas", ["co", "co2", "h2s"])
def test_gases_maximo_no_exceden_por_debajo(gas):
    assert UMBRALES[gas].sentido is Sentido.MAXIMO
    assert excede(gas, 0.0) is False


def test_no_monitoreados():
    assert UMBRALES["so2"].monitoreado is False
    assert UMBRALES["no2"].monitoreado is False


def test_porcentaje_limite_no_aplica_a_gases_de_rango():
    with pytest.raises(ValueError):
        porcentaje_limite("o2", 20.0)


def test_porcentaje_limite_es_lectura_sobre_limite():
    assert porcentaje_limite("co", 12.5) == pytest.approx(0.5)
    assert porcentaje_limite("co", 25.0) == pytest.approx(1.0)


def test_nivel_proximidad_co_tres_bandas():
    assert nivel_proximidad("co", 10.0) == "normal"
    assert nivel_proximidad("co", 21.0) == "atencion"
    assert nivel_proximidad("co", 26.0) == "excedido"


def test_nivel_proximidad_ch4_riesgo_explosivo_por_encima_del_excedido():
    assert nivel_proximidad("ch4", 0.5) == "normal"
    assert nivel_proximidad("ch4", 0.9) == "atencion"
    assert nivel_proximidad("ch4", 1.5) == "excedido"
    assert nivel_proximidad("ch4", 4.2) == "riesgo_explosivo"
