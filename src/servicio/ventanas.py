"""Adaptación causal de paquetes JSON a la cadencia del modelo existente."""
import numpy as np
import pandas as pd


def preparar_ventana(paquetes: list[dict], superficie: list[dict], operaciones: list[dict]) -> pd.DataFrame:
    if not paquetes:
        return pd.DataFrame()
    filas = []
    for p in paquetes:
        filas.append({'timestamp':p['timestamp'], **{
            gas: p['gases'].get(gas) if p['estado']['sensor_ok'] else np.nan
            for gas in ('ch4_pct', 'co_ppm')}})
    datos = pd.DataFrame(filas)
    # format='ISO8601': los timestamps almacenados mezclan precisión (con y
    # sin microsegundos, según datetime.isoformat() los omita o no) — un
    # formato inferido de un lote falla al toparse con el otro.
    datos['timestamp'] = pd.to_datetime(datos['timestamp'], utc=True, format='ISO8601')
    # Etiquetado a la derecha: ninguna muestra posterior al instante de
    # inferencia entra en esa ventana. Huecos quedan NaN, nunca cero.
    datos = datos.set_index('timestamp').sort_index().resample('15s', closed='right', label='right').mean()
    datos = datos.loc[datos.index >= datos.index[-1] - pd.Timedelta(hours=13)]
    datos['presion_hpa'] = np.nan
    fuentes_presion = [*paquetes, *superficie]
    if fuentes_presion:
        presion = pd.DataFrame([{'timestamp':p['timestamp'], 'presion':p.get('ambiente', {}).get('presion_hpa')
                                 if p.get('estado', {}).get('sensor_ok') else np.nan} for p in fuentes_presion])
        presion = presion.dropna(subset=['presion'])
    if fuentes_presion and not presion.empty:
        presion['timestamp'] = pd.to_datetime(presion.timestamp, utc=True, format='ISO8601')
        fusion = pd.merge_asof(datos.reset_index(), presion.sort_values('timestamp'), on='timestamp',
                              direction='backward', tolerance=pd.Timedelta(seconds=60))
        datos['presion_hpa'] = fusion.presion.to_numpy()
    # El formulario registra toneladas del turno, NO una tasa instantánea.
    # No inventar producción_ton_h ni retroproyectar datos ingresados después.
    datos['caudal_m3_s'] = np.nan
    datos['gasificacion_m3_ton'] = np.nan
    if operaciones:
        ops = pd.DataFrame([{'timestamp':o['registrado_en'], 'caudal_op':o.get('caudal_m3_s'),
                             'gasificacion_op':o.get('indice_gasificacion_m3_ton')} for o in operaciones])
        ops['timestamp'] = pd.to_datetime(ops.timestamp, utc=True, format='ISO8601')
        fusion = pd.merge_asof(datos.reset_index(), ops.sort_values('timestamp'), on='timestamp',
                              direction='backward', tolerance=pd.Timedelta(hours=8))
        datos['caudal_m3_s'] = fusion.caudal_op.to_numpy()
        datos['gasificacion_m3_ton'] = fusion.gasificacion_op.to_numpy()
    return datos.reset_index()
