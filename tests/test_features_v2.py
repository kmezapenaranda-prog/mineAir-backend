import pandas as pd

from src.data.features import construir_features
from src.data.generador import ConfiguracionGenerador, generar_datos_sinteticos


def test_features_respetan_cadencia_y_no_inventan_lag_5s():
    d = generar_datos_sinteticos(ConfiguracionGenerador(dias=1))
    x = construir_features(d)
    assert x.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=5)).all()
    assert "ch4_pct_lag_15s" in x and "ch4_pct_lag_30s" in x
    assert "ch4_pct_lag_5s" not in x
    assert x.iloc[1].ch4_pct_lag_15s == d.iloc[19].ch4_pct
    assert {"presion_delta_6h", "caudal_media_15min", "resistencia_relativa"} <= set(x)
