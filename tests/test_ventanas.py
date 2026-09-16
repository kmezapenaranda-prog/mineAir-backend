from src.servicio.ventanas import preparar_ventana


def _paquete(timestamp, ch4=0.4, co=5.0, sensor_ok=True):
    return {
        "timestamp": timestamp,
        "gases": {"ch4_pct": ch4, "co_ppm": co},
        "estado": {"sensor_ok": sensor_ok},
    }


def test_preparar_ventana_tolera_timestamps_con_y_sin_microsegundos():
    """Reproduce el fallo real observado en producción: la API almacena
    timestamps con precisión mixta porque datetime.isoformat() omite los
    microsegundos cuando son exactamente cero (paquetes de precarga
    redondeados al minuto) pero los incluye en paquetes en vivo. Un formato
    de timestamp inferido de un lote revienta al toparse con el otro."""
    paquetes = [
        _paquete("2026-09-12T23:00:00Z"),
        _paquete("2026-09-12T23:00:15.385174Z"),
        _paquete("2026-09-12T23:00:30Z"),
    ]
    ventana = preparar_ventana(paquetes, [], [])
    assert len(ventana) > 0
    assert "ch4_pct" in ventana.columns


def test_preparar_ventana_paquete_sin_sensor_ok_queda_nan():
    paquetes = [_paquete("2026-09-12T23:00:00Z", ch4=2.5, sensor_ok=False)]
    ventana = preparar_ventana(paquetes, [], [])
    assert ventana["ch4_pct"].isna().all()


def test_preparar_ventana_vacia_sin_paquetes():
    assert preparar_ventana([], [], []).empty
