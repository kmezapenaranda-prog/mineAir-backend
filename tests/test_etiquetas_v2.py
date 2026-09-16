import pandas as pd
import pytest

from src.data.etiquetas import etiquetar_excedencia, generar_dataset_evacuar_referencia


def test_etiqueta_es_futura_y_censura_la_cola():
    i = pd.date_range("2026-01-01", periods=8, freq="15s", tz="UTC")
    y = etiquetar_excedencia(pd.Series([.3, .3, 1.1, .3, .3, .3, .3, .3], index=i),
                            "ch4", pd.Timedelta(seconds=30))
    assert y.iloc[0] == 1 and y.iloc[1] == 1
    assert y.iloc[2] == 0  # el presente no cuenta como predicción futura
    assert y.tail(2).isna().all()


def test_solo_ch4_y_co_son_objetivos():
    i = pd.date_range("2026-01-01", periods=4, freq="15s", tz="UTC")
    with pytest.raises(ValueError):
        etiquetar_excedencia(pd.Series([20.9] * 4, index=i), "o2")


def _datos_minimos(n_5min: int) -> pd.DataFrame:
    i = pd.date_range("2026-01-01", periods=n_5min * 20, freq="15s", tz="UTC")
    return pd.DataFrame({"timestamp": i, "ch4_pct": 0.3, "co_ppm": 5.0, "o2_pct": 20.9})


def test_dataset_referencia_pone_limites_al_lado_de_cada_gas():
    df = generar_dataset_evacuar_referencia(_datos_minimos(20))
    assert list(df.columns) == [
        "ch4_pct", "ch4_tlv_twa", "ch4_stel", "ch4_limite_dec1886", "ch4_lel",
        "co_ppm", "co_tlv_twa", "co_stel", "co_limite_dec1886", "co_lel",
        "o2_pct", "o2_min_permisible", "o2_max_permisible",
        "excede_ch4_6h", "excede_co_1h", "evacuar",
    ]
    assert (df["ch4_tlv_twa"] == 1.0).all() and (df["ch4_lel"] == 5.0).all()
    assert df["ch4_stel"].isna().all()  # el decreto no define STEL para CH4
    assert (df["co_tlv_twa"] == 25.0).all() and df["co_stel"].isna().all()
    assert (df["o2_min_permisible"] == 19.5).all() and (df["o2_max_permisible"] == 23.5).all()


def test_dataset_referencia_sin_o2_omite_sus_columnas():
    datos = _datos_minimos(20).drop(columns="o2_pct")
    df = generar_dataset_evacuar_referencia(datos)
    assert not any(c.startswith("o2_") for c in df.columns)
