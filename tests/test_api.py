from fastapi.testclient import TestClient
from datetime import datetime, timezone

from src.servicio.api import almacen, app


client = TestClient(app)


def setup_function():
    almacen.limpiar()


def _telemetria(seq=1, timestamp="2026-08-24T12:00:00Z"):
    return {
        "schema_v": "1.0",
        "node_id": "S1",
        "node_type": "fijo",
        "ubicacion": "Retorno frente A",
        "frente": "A",
        "timestamp": timestamp,
        "seq": seq,
        "gases": {"ch4_pct": 0.42, "co_ppm": 12, "h2s_ppm": 0, "o2_pct": 20.6, "co2_pct": 0.08},
        "ambiente": {"temp_c": 28.4, "humedad_pct": 82, "presion_hpa": None},
        "estado": {"bateria_pct": 78, "sensor_ok": True},
    }


def _variables():
    return {
        "schema_v": "1.0",
        "fecha": "2026-08-24",
        "turno": "manana",
        "frente": "A",
        "produccion_ton": 42.5,
        "produccion_origen": "contada",
        "ventilador_principal_on": True,
        "registrado_por": "usuario_id",
    }


def test_ingesta_consulta_y_deduplicacion_de_telemetria():
    assert client.post("/api/telemetria", json=_telemetria()).json()["duplicado"] is False
    assert client.post("/api/telemetria", json=_telemetria()).json()["duplicado"] is True

    nodos = client.get("/api/nodos").json()
    assert nodos[0]["node_id"] == "S1"
    assert nodos[0]["ultima_lectura"]["gases"]["ch4_pct"] == 0.42
    historial = client.get(
        "/api/telemetria/S1",
        params={"desde": "2026-08-24T11:00:00Z", "hasta": "2026-08-24T13:00:00Z"},
    ).json()
    assert len(historial) == 1


def test_nodos_expone_proximidad_normativa_reactiva():
    paquete = _telemetria()
    paquete["gases"]["co_ppm"] = 21.0  # 84% del TLV-TWA (25 ppm) -> "atencion"
    client.post("/api/telemetria", json=paquete)

    nodos = client.get("/api/nodos").json()
    proximidad = nodos[0]["proximidad_normativa"]
    assert proximidad["co"] == {"valor": 21.0, "porcentaje_limite": 0.84, "nivel": "atencion"}
    assert proximidad["ch4"]["nivel"] == "normal"
    assert "o2" not in proximidad  # O2 es un rango, no tiene "límite" único


def test_alias_sin_api_tambien_funciona():
    assert client.post("/telemetria", json=_telemetria()).status_code == 200
    assert client.get("/nodos").status_code == 200


def test_rechaza_schema_desconocido_y_payload_operativo_incompleto():
    paquete = _telemetria()
    paquete["schema_v"] = "9.9"
    assert client.post("/api/telemetria", json=paquete).status_code == 422

    variables = _variables()
    variables.pop("produccion_origen")
    assert client.post("/api/variables-operativas", json=variables).status_code == 422


def test_variables_operativas_validas_se_registran():
    respuesta = client.post("/api/variables-operativas", json=_variables())
    assert respuesta.status_code == 200
    assert respuesta.json()["registro"]["registrado_en"].endswith("Z")


def test_predicciones_se_filtran_con_nombres_que_usa_web():
    almacen.publicar_prediccion(
        {
            "schema_v": "1.0",
            "node_id": "S1",
            "ubicacion": "Retorno frente A",
            "gas": "ch4",
            "horizonte_h": 6,
            "probabilidad": 0.82,
            "umbral_normativo": 1.0,
            "unidad": "pct",
            "sentido": "max",
            "nivel": "evacuar",
            "recomienda_evacuar": True,
            "confianza": "normal",
            "generada_en": datetime.now(timezone.utc).isoformat(),
            "factores": [{"nombre": "produccion", "peso": 1.0, "valor": "42.5 ton"}],
        }
    )
    respuesta = client.get(
        "/api/predicciones", params={"nodeId": "S1", "gas": "ch4", "recomienda_evacuar": True}
    )
    assert respuesta.status_code == 200
    assert len(respuesta.json()) == 1


def test_publicacion_interna_rechaza_prediccion_fuera_de_contrato():
    respuesta = client.post("/api/predicciones", json={"schema_v": "1.0", "gas": "o2"})
    assert respuesta.status_code == 422


def test_publicacion_interna_exige_token_si_esta_configurado(monkeypatch):
    monkeypatch.setenv("MINEAIR_INTERNAL_TOKEN", "secreto-demo")
    payload = {"schema_v": "1.0", "gas": "o2"}
    sin_token = client.post("/api/predicciones", json=payload)
    assert sin_token.status_code == 401
    con_token_malo = client.post("/api/predicciones", json=payload, headers={"x-internal-token": "otro"})
    assert con_token_malo.status_code == 401
    # Con el token correcto, pasa la barrera y llega a la validación normal (422, no 401).
    con_token_bueno = client.post("/api/predicciones", json=payload, headers={"x-internal-token": "secreto-demo"})
    assert con_token_bueno.status_code == 422


def test_estado_expone_version_y_modelo():
    estado = client.get("/api/estado").json()
    assert estado["servicio"] == "ok"
    assert estado["contrato_v"] == "1.8"
    assert isinstance(estado["modelo_evacuar_cargado"], bool)
