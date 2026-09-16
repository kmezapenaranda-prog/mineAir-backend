import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
from fastapi.testclient import TestClient

from scripts.recibir_gateway import ColaGateway, decodificar_linea
from src.data.etiquetas import etiquetar_excedencia
from src.servicio import api
from tests.test_api import _telemetria


def test_reinicios_retransmisiones_wrap_y_retencion(tmp_path):
    ruta = tmp_path / 'estado.db'
    almacen = api.AlmacenSQLite(ruta, max_paquetes_por_nodo=3)
    p = _telemetria(seq=65535); p['t_ms'] = 100000
    assert almacen.guardar_telemetria(p)
    assert not almacen.guardar_telemetria({**p, 'timestamp': '2026-08-24T12:00:02Z'})
    # seq vuelve a cero mientras millis sigue avanzando.
    assert almacen.guardar_telemetria({**p, 'seq': 0, 't_ms': 115000, 'timestamp': '2026-08-24T12:00:15Z'})
    # Reinicio: misma secuencia pero uptime distinto.
    reinicio = {**p, 'seq': 0, 't_ms': 1000, 'timestamp': '2026-08-24T12:00:30Z'}
    assert almacen.guardar_telemetria(reinicio)
    almacen.conexion.close()
    almacen = api.AlmacenSQLite(ruta, max_paquetes_por_nodo=3)
    assert not almacen.guardar_telemetria(reinicio)
    assert almacen.guardar_telemetria({**reinicio, 'timestamp': '2026-08-24T13:00:30Z'})
    assert len(almacen.historial_nodo('S1')) == 3
    almacen.conexion.close()


def test_migracion_conserva_datos_y_respaldo(tmp_path):
    ruta = tmp_path / 'vieja.db'
    con = sqlite3.connect(ruta)
    con.execute('CREATE TABLE telemetria (node_id TEXT, seq INTEGER, timestamp TEXT, recibido_en TEXT, payload TEXT, PRIMARY KEY(node_id, seq))')
    p = _telemetria()
    con.execute('INSERT INTO telemetria VALUES (?,?,?,?,?)', ('S1', 1, p['timestamp'], p['timestamp'], json.dumps(p)))
    con.commit(); con.close()
    almacen = api.AlmacenSQLite(ruta)
    assert almacen.historial_nodo('S1') == [p]
    assert almacen.conexion.execute('SELECT COUNT(*) FROM telemetria_legacy').fetchone()[0] == 1
    almacen.conexion.close()
    almacen = api.AlmacenSQLite(ruta)
    assert len(almacen.historial_nodo('S1')) == 1
    almacen.conexion.close()


def test_casco_falla_y_fechas(tmp_path, monkeypatch):
    almacen = api.AlmacenSQLite(tmp_path / 'api.db')
    monkeypatch.setattr(api, 'almacen', almacen)
    cliente = TestClient(api.app)
    p = _telemetria(); p.update(node_id='H1', node_type='casco')
    p['estado']['sensor_ok'] = False
    assert cliente.post('/api/telemetria', json=p).status_code == 200
    nodo = cliente.get('/api/nodos').json()[0]
    assert nodo['ultima_lectura']['gases']['co2_pct'] == .08
    assert nodo['proximidad_normativa'] == {}
    assert cliente.get('/api/telemetria/H1', params={'desde':'2026-01-01T00:00:00', 'hasta':'2026-12-01T00:00:00Z'}).status_code == 422
    almacen.conexion.close()


def test_predicciones_caducan_y_no_se_cuentan(tmp_path):
    almacen = api.AlmacenSQLite(tmp_path / 'p.db')
    p = dict(schema_v='1.0', node_id='S1', gas='ch4', horizonte_h=6, probabilidad=.1,
             umbral_normativo=1, unidad='pct', sentido='max', nivel='normal', recomienda_evacuar=False,
             confianza='normal', generada_en=datetime.now(timezone.utc).isoformat(),
             factores=[dict(nombre='presion', peso=1, valor='980 hPa')])
    almacen.publicar_prediccion(p)
    assert almacen.total_predicciones() == 1
    for fecha in [datetime.now(timezone.utc) - timedelta(minutes=11), datetime.now(timezone.utc) + timedelta(hours=1)]:
        almacen.publicar_prediccion({**p, 'generada_en':fecha.isoformat()})
        assert almacen.predicciones_vigentes() == []
        assert almacen.total_predicciones() == 0
    almacen.conexion.close()


def test_etiquetas_no_inventan_negativos():
    indice = pd.date_range('2026-01-01', periods=8, freq='15s', tz='UTC')
    valores = pd.Series([0, float('nan'), float('nan'), 1.2, 0, 0, 0, 0], index=indice)
    etiquetas = etiquetar_excedencia(valores, 'ch4', pd.Timedelta(seconds=30))
    assert pd.isna(etiquetas.iloc[0])
    assert etiquetas.iloc[1] == 1  # una excedencia observada sí prueba el positivo
    assert etiquetas.iloc[3] == 0
    assert etiquetas.tail(2).isna().all()


def test_cola_reintenta_con_mismo_timestamp_y_cuarentena(tmp_path):
    assert decodificar_linea('LoRa: escuchando...') is None
    ahora = datetime(2026, 9, 12, tzinfo=timezone.utc)
    p = decodificar_linea(json.dumps(_telemetria()), ahora)
    assert p['timestamp'] == '2026-09-12T00:00:00Z'
    ruta = tmp_path / 'cola.db'
    cola = ColaGateway(ruta); cola.agregar(p)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as c:
        cola.enviar(c, 'http://test/api/telemetria')
    cola.db.close()
    _comprobar_reenvio(ruta, p)


def test_ventana_causal_con_huecos_y_sensor_fallado():
    from src.servicio.ventanas import preparar_ventana
    p = _telemetria(timestamp='2026-09-12T12:00:01Z')
    q = _telemetria(seq=2, timestamp='2026-09-12T12:00:46Z')
    q['estado']['sensor_ok'] = False
    ventana = preparar_ventana([p, q], [], [])
    assert ventana.timestamp.iloc[0] == pd.Timestamp('2026-09-12T12:00:15Z')
    assert ventana.ch4_pct.iloc[0] == .42
    assert ventana.ch4_pct.iloc[1:].isna().all()
    assert ventana.presion_hpa.isna().all()


def test_api_a_motor_conjunto_sin_probabilidades_duplicadas(tmp_path, monkeypatch):
    from src.servicio.predictor import Predictor
    from src.model.inferencia import MotorInferencia
    from src.data.generador import ConfiguracionGenerador, generar_datos_sinteticos
    almacen = api.AlmacenSQLite(tmp_path / 'flujo.db')
    monkeypatch.setattr(api, 'almacen', almacen)
    cliente = TestClient(api.app)
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    class MotorPrueba:
        listo = True
        def predecir(self, datos):
            assert datos.ch4_pct.iloc[-1] == .42
            return [dict(probabilidad=.25, objetivo='ch4_6h_o_co_1h', experimental=True)]
    predictor = Predictor(almacen, MotorPrueba())
    monkeypatch.setattr(api, 'predictor', predictor)
    for j in range(2):
        p = _telemetria(seq=j, timestamp=(ahora - timedelta(seconds=15*(1-j))).isoformat())
        assert cliente.post('/api/telemetria', json=p).status_code == 200
    predictor.ejecutar()
    resultado = cliente.get('/api/riesgo-conjunto').json()
    assert len(resultado) == 1 and resultado[0]['node_id'] == 'S1'
    assert cliente.get('/api/predicciones').json() == []
    p['estado']['sensor_ok'] = False; p['seq'] = 3
    p['timestamp'] = (ahora + timedelta(seconds=1)).isoformat()
    cliente.post('/api/telemetria', json=p)
    assert cliente.get('/api/riesgo-conjunto').json() == []
    almacen.conexion.close()


def test_motor_con_huecos_presion_ausente_y_calentamiento():
    from src.model.inferencia import MotorInferencia
    from src.data.generador import ConfiguracionGenerador, generar_datos_sinteticos
    motor = MotorInferencia()
    if not motor.listo:
        import pytest
        pytest.skip('Artefacto local no distribuido con Git')
    datos = generar_datos_sinteticos(ConfiguracionGenerador(dias=1))
    assert motor.predecir(datos.iloc[:20]) == []
    resultado = motor.predecir(datos.drop(index=5).drop(columns='presion_hpa'))
    assert len(resultado) == 1
    assert resultado[0]['objetivo'] == 'ch4_6h_o_co_1h'
    assert resultado[0]['confianza'] == 'reducida'
    assert 'presion_hpa' in resultado[0]['features_faltantes']
    from pathlib import Path
    from jsonschema import Draft202012Validator, FormatChecker
    schema = json.loads(Path('riesgo_conjunto.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate({
        **resultado[0], 'node_id':'S1', 'ubicacion':None, 'datos_hasta':resultado[0]['generada_en']})


def test_arranque_api_carga_motor_y_cierra_ciclo(tmp_path, monkeypatch):
    almacen = api.AlmacenSQLite(tmp_path / 'inicio.db')
    monkeypatch.setattr(api, 'almacen', almacen)
    with TestClient(api.app) as cliente:
        estado = cliente.get('/api/estado').json()
        assert estado['modelo_evacuar_cargado'] == api.predictor.motor.listo
        assert cliente.get('/api/riesgo-conjunto').json() == []
    assert api.predictor is None
    almacen.conexion.close()


def test_particiones_preservan_trayectoria_y_parametros():
    from src.data.generador import ConfiguracionGenerador, generar_datos_sinteticos, generar_por_meses
    config = ConfiguracionGenerador(inicio='2025-01-31T00:00:00Z', dias=2, coeficiente_pulso_co=12)
    esperado = generar_datos_sinteticos(config)
    partes = list(generar_por_meses(config))
    assert [nombre for nombre, _ in partes] == ['2025-01', '2025-02']
    pd.testing.assert_frame_equal(pd.concat([d for _, d in partes], ignore_index=True), esperado)


def _comprobar_reenvio(ruta, p):
    cola = ColaGateway(ruta)
    enviados = []
    def aceptar(r):
        enviados.append(json.loads(r.content))
        return httpx.Response(200, json={'ok': True})
    with httpx.Client(transport=httpx.MockTransport(aceptar)) as c:
        cola.enviar(c, 'http://test/api/telemetria')
    assert enviados == [p]
    assert cola.db.execute('SELECT COUNT(*) FROM pendientes').fetchone()[0] == 0
    cola.agregar(p)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(422, text='incompatible'))) as c:
        cola.enviar(c, 'http://test/api/telemetria')
    assert cola.db.execute('SELECT error FROM pendientes').fetchone()[0] == 'incompatible'
    cola.db.close()
