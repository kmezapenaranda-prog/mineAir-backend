"""Ciclo de inferencia independiente de las solicitudes HTTP."""
import logging
from datetime import datetime, timedelta, timezone

from src.servicio.ventanas import preparar_ventana

LOG = logging.getLogger(__name__)


class Predictor:
    def __init__(self, almacen, motor):
        self.almacen, self.motor = almacen, motor
        self.resultados = {}
        self.ultimo_ciclo = None
        self.error = None

    def ejecutar(self):
        ahora = datetime.now(timezone.utc)
        nuevos = {}
        if not self.motor.listo:
            self.resultados = {}
            return
        try:
            ultimas = self.almacen.ultima_lectura_por_nodo()
            superficie = []
            for identidad, p in ultimas.items():
                if p['node_type'] == 'superficie':
                    superficie.extend(self.almacen.historial_nodo(identidad) or [])
            with self.almacen.lock:
                import json
                operaciones = [json.loads(f['payload']) for f in self.almacen.conexion.execute(
                    'SELECT payload FROM variables_operativas ORDER BY registrado_en').fetchall()]
            for identidad, p in ultimas.items():
                ts = datetime.fromisoformat(p['timestamp'].replace('Z', '+00:00'))
                if p['node_type'] not in ('fijo', 'casco') or not p['estado']['sensor_ok']:
                    continue
                if not timedelta(0) <= ahora - ts <= timedelta(seconds=60):
                    continue
                historial = self.almacen.historial_nodo(identidad) or []
                ventana = preparar_ventana(historial, superficie,
                    [o for o in operaciones if p.get('frente') is not None and o.get('frente') == p.get('frente')])
                for resultado in self.motor.predecir(ventana):
                    nuevos[identidad] = {**resultado, 'node_id': identidad,
                                          'ubicacion': p.get('ubicacion'),
                                          'datos_hasta': p['timestamp'],
                                          'generada_en': ahora.isoformat().replace('+00:00', 'Z')}
            self.resultados = nuevos
            self.ultimo_ciclo = ahora.isoformat().replace('+00:00', 'Z')
            self.error = None
        except Exception:
            LOG.exception('No se pudo ejecutar el ciclo de inferencia')
            self.resultados = {}
            self.error = 'Error de inferencia; consultar el registro del servicio'

    def vigentes(self):
        ahora = datetime.now(timezone.utc)
        ultimas = self.almacen.ultima_lectura_por_nodo()
        return [p for identidad, p in self.resultados.items()
                if timedelta(0) <= ahora - datetime.fromisoformat(p['generada_en'].replace('Z', '+00:00')) <= timedelta(minutes=10)
                and identidad in ultimas and ultimas[identidad]['estado']['sensor_ok']
                and timedelta(0) <= ahora - datetime.fromisoformat(ultimas[identidad]['timestamp'].replace('Z', '+00:00')) <= timedelta(seconds=60)]
