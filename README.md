# MineAIr ML v2

Pipeline predictivo local para **CH4** y **CO**: **LASSO → WOA → XGBoost**.

## Alcance honesto

El dataset generado por este repositorio es sintético. Sirve para verificar que
la adquisición, las relaciones físicas, las etiquetas y el pipeline funcionan;
no demuestra precisión en una mina real.

## Supuestos del piloto

- Muestreo crudo cada 15 segundos.
- Predicción calculada cada 5 minutos.
- Cámara y pilares; un frente activo y uno sellado.
- Sección 1.80 × 2.00 m; manto medio 1.10 m variable.
- Dos turnos de 20 trabajadores; nominal 1 ton/h por persona.
- Arranque mecánico y por voladura; dos voladuras por turno.
- Caudal habitual 3.7–4.0 m³/s con resistencia equivalente de paredes y curvas.
- Gasificación media 4 m³/ton.
- Presión base estimada con atmósfera estándar a 320 m s. n. m. (estación
  Sardinata IDEAM). Debe sustituirse por la altitud y las lecturas de SUP1.

No existe un lag medido de 5 s cuando la adquisición ocurre cada 15 s. Las
features crudas empiezan en 15, 30, 45 y 60 s; después incluyen 5, 15 y 30 min
y rezagos de 1, 4, 6, 8 y 12 h.

## Comandos

```powershell
.\.venv\Scripts\python.exe -m scripts.generar_dataset
.\.venv\Scripts\python.exe -m scripts.entrenar_modelos
.\.venv\Scripts\python.exe -m scripts.entrenar_evacuar
.\.venv\Scripts\python.exe -m pytest -q
```

El primer comando crea 6 CSV gzip y `manifest.json` bajo
`data/sintetico_6m_15s` (6 meses, cadencia cruda de 15 s agregada a 5 min para
etiquetas y features). El manifiesto registra parámetros, seed, cadencia y
número de filas. `entrenar_modelos` entrena CH4/CO por separado;
`entrenar_evacuar` entrena el objetivo unificado "evacuar" (optimizado para
F2, prevalencia objetivo ~2 %) y genera las gráficas en `reports/evacuar/`.
La evaluación reproducida más reciente está en
`reports/evacuar/EVALUACION-2026-09-12.md`; sigue siendo evidencia sintética.

## Gateway USB y servicio ML

Desde la raíz del proyecto, instalar dependencias y arrancar una sola instancia:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:DATABASE_URL = "mysql+pymysql://mineair:clave@127.0.0.1:3306/mineair?charset=utf8mb4"
.\.venv\Scripts\python.exe -m uvicorn src.servicio.api:app --host 127.0.0.1 --port 8000
```

La base `mineair` y sus credenciales deben existir antes de arrancar. La API crea
automáticamente sus tablas vacías al conectarse. No se migran los datos históricos
de SQLite.

En otra terminal, cerrar el Monitor Serie de Arduino y abrir el puente:

```powershell
.\.venv\Scripts\python.exe -m scripts.recibir_gateway --puerto COM8
```

El puente asigna UTC con el reloj del computador y guarda una cola persistente
en `gateway_pendientes.db`. Los reintentos HTTP conservan timestamp; los errores
422 se conservan con su motivo en la columna `error`. Para usarlo en Linux/Pi,
cambiar COM8 por el dispositivo correspondiente, por ejemplo `/dev/ttyUSB0`.
La hora del host debe estar sincronizada. No arrancar dos lectores del mismo USB.

La persistencia de la API usa MySQL mediante `DATABASE_URL`. La cola de reintentos
del puente USB (`gateway_pendientes.db`) sigue siendo SQLite local porque es un
componente independiente de la base de datos del backend. Los tests usan una base
efímera aislada.

`GET /api/nodos` muestra recepción. `GET /api/estado` informa carga real del modelo
y último ciclo. `GET /api/riesgo-conjunto` entrega un único resultado experimental
por nodo: probabilidad de CH4 en 6 h **o** CO en 1 h. No son dos probabilidades por
gas; `/api/predicciones` queda para resultados independientes publicados por otro
motor. El simulador ahora solo inyecta telemetría; el servicio hace los cálculos.

El ciclo corre cada 5 min y espera 12 h de historial. Se abstiene con sensor en
falla, gases actuales ausentes o nodo sin recepción reciente. Presión de superficie
se combina con tolerancia de 60 s; caudal/gasificación del formulario, desde su
registro y durante 8 h, solo para el mismo frente. Toneladas del turno no se
convierten artificialmente en producción instantánea. Las variables ausentes
quedan NaN y se declara confianza reducida. Se mantiene la representación de
features del artefacto existente; cambiar a promedios como señal principal
requiere versionar features y reentrenar, aún pendiente.

Los artefactos locales no se distribuyen por Git: copiar modelo y metadata juntos
o entrenarlos antes. Los cambios de etiquetas y continuidad mensual requieren
regenerar datos y reentrenar para obtener métricas nuevas. Las métricas anteriores
siguen siendo históricas; no se afirma mejora de precisión. La generación continua
se ejecuta en el PC y requiere memoria para todo el periodo.

En Raspberry Pi, usar un entorno Python ARM64 y verificar dependencias allí.
Para acceso por red, usar `--host 0.0.0.0` y definir `MINEAIR_CORS_ORIGINS` con
los orígenes exactos de la web separados por comas. Ejecutar un solo worker.
La validación con casco y Pi sigue pendiente. Los POST internos no tienen
autenticación: limitar acceso de red antes de exponer el servicio fuera del piloto.

## Umbrales

Los valores regulatorios se leen de `src/config/umbrales.py`. CH4 conserva el
límite de 1.0 %vol. Para CO, 25 ppm corresponde a TLV-TWA; debe evitarse
presentarlo como un techo instantáneo certificado sin revisión del responsable
de higiene y seguridad.
