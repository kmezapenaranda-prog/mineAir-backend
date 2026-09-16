MineAIr — Modelo ML (Track B)
⛔ LEE PRIMERO MINEAIR-SHARED.md

El contrato de datos, los umbrales del Decreto 1886, el inventario de nodos, las unidades, la regla de tiempo y el manejo de fallas viven exclusivamente en MINEAIR-SHARED.md. No los redefinas en este archivo. Si necesitas cambiar algo del contrato, se cambia allá, se sube la versión y se copia a los tres repos.

Contrato vigente: v1.7

⚠️ Cambio de arquitectura (D1, 2026-08-06; revisado 2026-09-10): el Jetson Nano fue eliminado, pero el cómputo en bocamina no — el gateway ahora es una **Raspberry Pi + módulo ESP32-LoRa**, y el modelo corre **localmente en esa Pi** (ARM64/aarch64), no en el computador de superficie. El computador del ingeniero en superficie ya no corre el servicio ML: solo corre el aplicativo web, que consulta la API de la Pi por red local (no `localhost`, salvo en desarrollo). Esto sí implica pensar en ARM: verificar que pandas/numpy/xgboost instalen limpio en ARM64 en la Pi, y que el modelo entrenado (artefacto .json de XGBoost, liviano) corra bien con los recursos limitados de una Pi frente a un portátil x86_64. Si encuentras referencias a "el modelo corre en el computador del ingeniero" en este repo, son residuo — corregir contra `MINEAIR-SHARED.md` D1.

🎯 ALCANCE FASE PITCH (v1 mínimo para Saskatoon) — LEER ANTES DE CONSTRUIR

Equipo real: 2 personas. Tiempo: ~5 semanas, compartidas con hardware, piloto, visa y pitch. El resto de este archivo describe la visión completa del producto. NO se construye todo de una. Se construye primero el NÚCLEO de abajo, y solo cuando ese núcleo corre y predice se pasa a lo DESEABLE. Todo lo marcado como POST-PILOTO no se toca hasta después de la mina.

NÚCLEO irrenunciable (construir primero, en este orden)
Estructura del repo + src/config/umbrales.py copiado del compartido (nunca hardcodear).
Generador sintético con la física real: CH₄ con rezago 4–8 h de la producción, correlación negativa con presión, pulso de CO tras voladura. 30–60 días a 15 s agregados a 5 min (NO 6 meses todavía). Seed fijo.
Generador de etiquetas desde umbrales del Decreto 1886, con su test (incluido O₂ invertido, aunque O₂ no sea objetivo predictivo v1).
Features + LASSO, y la prueba de fuego: LASSO debe encontrar el rezago producción→CH₄. Si no lo encuentra en sintético, hay bug — parar y revisar (ver regla de cuantización).
WOA + XGBoost para CH₄ únicamente en horizonte de 6 h primero. Validación temporal (nunca aleatoria). Métricas: AUC + tasa de falsas alarmas.
Una gráfica que muestre el rezago descubierto y la predicción vs realidad. Esta es la diapositiva más importante del pitch.

Con esos 6 puntos hay proyecto defendible en Saskatoon. Nada más es obligatorio.

DESEABLE (solo si el núcleo ya corre)
Predicción de CO como segundo gas.
Horizontes adicionales (1, 12, 24 h) además de 6 h.
Servicio FastAPI + simulador de flujo para la demo integrada con la web. Si no llega, se demuestra el modelo por separado y la web con su mock. El video demo cubre el respaldo.
POST-PILOTO / NO construir en fase pitch
Los 6 meses de sintético (con 30–60 días alcanza para demostrar el pipeline).
Calibración cruzada de los dos ZCE04B, produccion_origen, nodos contador, conteo.vagonetas. Si el piloto los aporta, bien; el modelo funciona sin ellos.
El diferencial S1 − S2 como feature solo si hay registro de calibración (ver regla). Sin calibración, se omite; no se inventa.
Regla de decisión ante dudas

Ante cualquier disyuntiva "¿lo hago simple o completo?", elegir simple que funcione seguro. Un pipeline sencillo, entendido a fondo y defendible vale más que uno sofisticado a medio terminar. El jurado premia demostrar que la idea funciona, no la cantidad de features.

Contexto

MineAIr es un sistema de monitoreo atmosférico con IA predictiva para minería subterránea de carbón. Ganó el Pitch Day Regional de AI4SafeMines 2026 y va a la final del 24 de septiembre de 2026 en Saskatoon, Canadá. Equipo: dos estudiantes de Ingeniería de Minas de la UFPS, Cúcuta.

Este repo contiene el modelo de predicción y el servicio que lo expone. Los otros dos tracks (mineair-firmware, mineair-web) viven en repos separados.

La tesis central: un detector comercial alerta cuando el límite ya se superó (reactivo). MineAIr correlaciona lecturas de sensores con variables operativas y predice la probabilidad de superar los límites normativos en horizontes de 1 a 24 h (preventivo).

El fenómeno físico que lo hace posible: el CH₄ responde a la producción con rezago de 4 a 8 horas y correlaciona negativamente con la presión barométrica — cuando la presión cae, el metano se desorbe de zonas selladas.

Corolario que no se puede olvidar: sin el nodo de superficie con sensor barométrico (SUP1, D7), la tesis no tiene sustento empírico. Si presion_hpa llega siempre null, el pipeline técnicamente corre pero el proyecto pierde su diferencial.

Pipeline

LASSO → WOA → XGBoost, validado en Song et al. (2023), mina Qianjiaying, MAE 0.17. Referencia complementaria: Diaz et al. (2021–2023, University of Kentucky), predicción de CH₄ con presión barométrica y producción en 3 minas activas de EE.UU., pasos de 12 h.

LASSO — selección de variables. Su salida (coeficientes no nulos, ordenados por magnitud) alimenta directamente el campo factores del JSON de predicción. La explicabilidad no es adorno: es lo que genera confianza en un ingeniero de minas.
WOA (Whale Optimization Algorithm) — optimización de hiperparámetros de XGBoost. Implementar desde cero o con mealpy; documentar el espacio de búsqueda.
XGBoost — clasificación binaria por (gas, horizonte): ¿probabilidad de superar el umbral normativo en las próximas H horas? Salida: predict_proba. XGBoost tolera faltantes de forma nativa — si un nodo cae, el modelo sigue prediciendo con confianza: "reducida", nunca lanza error.
Features (punto de partida; LASSO decide las finales)
Lecturas actuales y rezagadas (lags 1 h, 4 h, 8 h, 12 h) de cada gas por nodo
Diferencia de concentración entre retorno de aire y vía de entrada — esta diferencia es justamente lo que da poder predictivo, y por eso el modelo corre centralizado (D4)
Presión barométrica exterior (de SUP1): valor, tendencia (Δ 6 h, Δ 12 h)
Variables operativas: producción del turno, índice de gasificación (m³/ton), estado y caudal del ventilador, voladuras (hora y kg)
produccion_origen (contada vs estimada) como feature: un dato contado por los nodos contador y uno estimado a ojo no merecen el mismo peso
Temporales: hora del día, turno
Etiquetas

Se generan automáticamente desde los umbrales del §4 del compartido (Decreto 1886 de 2015): para cada (gas, nodo, timestamp), etiqueta = 1 si en la ventana [t, t+H] la lectura superó el umbral.

🔴 Cuantización del CH₄ — regla que no se puede saltar

El ZCE04B entrega CH₄ con resolución de 1 % LEL = 0.05 % vol por escalón. El firmware convierte y transmite la lectura cruda cada 15 s, sin suavizar.

El Track B debe promediar las 20 muestras de cada ventana de 5 min, nunca tomar la última lectura. Con la última lectura la señal se ve escalonada y el rezago producción→CH₄ se degrada o desaparece. Con el promedio, el ruido propio del sensor actúa como dither y se recupera resolución efectiva por debajo del escalón.

Si en Fase 2 LASSO no encuentra el rezago en datos reales pero sí en sintéticos, revisar esto antes que cualquier otra cosa.

🔴 El diferencial depende de la calibración cruzada

S1 − S2 (retorno − entrada) es la feature más importante del modelo y ahora es medible con hardware real (2 ZCE04B). Pero solo vale si los offsets entre los dos módulos fueron caracterizados (§3 del compartido).

Rechazar datos de campaña que no traigan registro de calibración previa. Sin eso, el diferencial es error de sensor con apariencia de física, y el modelo lo aprende con total confianza.

Etiquetas — alcance predictivo v1

El modelo v1 genera etiquetas y predicciones únicamente para CH₄ y CO, ambos con excedencia hacia arriba. El O₂ no es objetivo predictivo: permanece bajo control reactivo del firmware y monitoreo en la web. Aun así, su lógica invertida es obligatoria en tests de telemetría. Es el bug más probable de todo el repo.

Umbrales en src/config/umbrales.py, copiados de la tabla del compartido, nunca hardcodeados.

Datos de entrenamiento

El hardware ya está ensamblado y listo; solo falta conectarlo. Mientras eso pasa, fase inicial: generador de datos sintéticos que reproduzca la física real. La misma física que el mock del aplicativo web — cualquier cambio se coordina con Track C, o los dos tracks acaban demostrando fenómenos distintos en el mismo pitch.

CH₄ con ciclo diario y picos correlacionados con producción, desfasados 4–8 h
Correlación negativa presión barométrica ↔ CH₄
Pulso de CO y descenso de O₂ tras voladuras, recuperación gradual según ventilación
Retorno de aire con concentraciones mayores que la vía de entrada
Ruido de sensor ±3 % del rango
Nodos ocasionalmente caídos y paquetes con sensor_ok: false, para entrenar la tolerancia
CH₄ cuantizado en escalones de 0.05 % vol, para que el sintético tenga la misma textura que el dato real. Sin esto, el modelo entrena en una señal más limpia de la que va a encontrar.
Offset residual entre nodos tras calibración, para que el pipeline no asuma sensores perfectamente idénticos
Eventos de conteo.vagonetas desde nodos contador

Mínimo 6 meses simulados a resolución de 15 s (la cadencia real del contrato, D6), agregados a 5 min para entrenamiento. Generador parametrizable (características de la mina) y reproducible (seed fijo).

Cuando llegue el piloto en mina real, los datos reales reemplazan/complementan los sintéticos sin cambiar el pipeline.

Servicio

FastAPI + uvicorn corre en la Raspberry Pi del gateway, en bocamina — arrancado con `--host 0.0.0.0` para ser alcanzable desde la red local, no solo `localhost`. Es lo que consume el aplicativo web (src/data/edge/ del Track C apunta a la IP/hostname de la Pi; `localhost:8000` solo aplica en desarrollo local en la misma máquina).

Endpoints mínimos:

Método	Ruta	Qué hace
POST	/telemetria	Ingesta desde el gateway. Valida contra el schema.
POST	/variables-operativas	Formulario del contrato #2.
GET	/nodos	Inventario y último estado.
GET	/telemetria/{node_id}	Serie histórica (desde, hasta).
GET	/predicciones	Predicciones vigentes, filtrables.
GET	/estado	Salud del servicio y de la malla.

`/nodos` además expone `proximidad_normativa`: por cada gas monitoreado, la lectura actual, qué
fracción del límite del Decreto 1886 representa y un nivel (`normal`/`atencion`/`excedido`/
`riesgo_explosivo` para CH4 cerca del LEL completo). Es reactivo (lectura de ahora, ver
`src/config/umbrales.py::nivel_proximidad`), no una predicción — complementa, no reemplaza, el
campo `nivel` predictivo de `/predicciones` (contrato #3 v1.7). No forma parte de los schemas
compartidos: es información propia de este servicio, libre de evolucionar sin coordinar versión.
Mantiene ventana deslizante y emite predicciones cada 5 min en el JSON del contrato #3.
Valida todo input contra contrato/schemas/. Un paquete con schema_v desconocido se rechaza y se loguea; no se interpreta a medias.
Incluye un simulador de flujo en tiempo real para la demo — el pitch de Saskatoon no puede depender de que el hardware esté vivo ese día.
Estructura del repo
mineair-ml/
├── CLAUDE.md
├── MINEAIR-SHARED.md      # copia idéntica del contrato
├── contrato/
│   ├── schemas/           # copia idéntica
│   └── fixtures/          # copia idéntica
├── src/
│   ├── config/            # umbrales.py, mina.py (parámetros físicos de la mina)
│   ├── data/              # generador sintético, features, etiquetas (Decreto 1886)
│   ├── model/             # woa_xgboost.py, inferencia.py (motor de predicción)
│   ├── modelo/            # lasso.py (selección de variables, compartido)
│   └── servicio/          # API FastAPI
├── notebooks/
├── test/                  # test_umbrales.py (¡O₂ invertido!)
├── tests/                 # LASSO, features, generador, inferencia y API (v2)
├── models/                # artefactos entrenados (.json de XGBoost)
└── reports/               # métricas de validación (privadas hasta el piloto)
Stack
Python 3.10+, venv + requirements.txt
pandas, numpy, scikit-learn (LASSO), xgboost, mealpy (WOA), FastAPI + uvicorn, jsonschema
Target de despliegue: ARM64 (Raspberry Pi), 100 % offline. Ninguna dependencia que llame a internet en runtime. El entrenamiento sigue siendo x86_64 (ver abajo); es el despliegue el que cambió a ARM64 con D1 revisado (2026-09-10).
Dos máquinas, no una
	Máquina	Rol
Desarrollo / entrenamiento	PC de escritorio (gamer, multinúcleo, x86_64)	Correr WOA, entrenar, iterar experimentos
Demo / operación	Raspberry Pi (gateway)	Lo que viaja a Saskatoon y lo que corre en bocamina — ARM64, no x86_64

El modelo se entrena en el PC y se sirve desde un artefacto .json de XGBoost, que pesa kilobytes y corre en cualquier máquina, incluida una Raspberry Pi. Inferencia y entrenamiento tienen requisitos muy distintos; no confundirlos.

Regla dura: todo lo que se muestre en Saskatoon tiene que haber corrido al menos una vez en la Raspberry Pi que viaja, no solo en el PC de desarrollo. Un requirements.txt que solo instala limpio en x86_64 es un fallo de demo esperando su turno — confirmar wheels ARM64 para pandas/numpy/xgboost antes de dar esto por probado.

Sobre la GPU

El PC de escritorio tiene GPU. No cambia nada del pipeline y no debe cambiarlo.

XGBoost a esta escala corre en CPU sin problema. La GPU no aporta.
Tener GPU no es razón para volver a considerar LSTM u otra red profunda. La decisión de usar XGBoost se tomó por escasez de datos, no por falta de cómputo. Más GPU no crea datos. Cambiar de modelo por tener hardware disponible es exactamente el error que un jurado técnico detecta en preguntas.
Donde sí paga el PC multinúcleo: WOA. La optimización de hiperparámetros es vergonzosamente paralela — cada ballena evalúa su configuración de forma independiente. Configurar n_jobs al número de núcleos físicos convierte horas de búsqueda en minutos, y eso se traduce en más experimentos antes de septiembre.
Fases

Fase 1 — Fundación. Estructura, umbrales centralizados, generador sintético con la física descrita, tests del generador de etiquetas (incluido O₂ invertido).

Fase 2 — Features y LASSO. Lags y diferenciales entre nodos, selección con LASSO, y validación de que los factores seleccionados coinciden con la física esperada. Si LASSO no encuentra el rezago producción→CH₄ en datos sintéticos, el generador o las features tienen un bug — no se sigue adelante hasta resolverlo.

Fase 3 — WOA + XGBoost. Optimización y entrenamiento. Validación temporal (split por tiempo, nunca aleatorio — es serie temporal). Métricas: AUC, precision/recall por nivel, y sobre todo tasa de falsas alarmas: una falsa alarma por turno mata la confianza del ingeniero y con ella el producto.

Fase 4 — Servicio. API FastAPI completa + simulador de flujo para demo.

Fase 5 — Integración. Conexión con el gateway real y con el aplicativo web, pruebas extremo a extremo.

Restricciones
Validación temporal siempre. Split aleatorio en series temporales = fuga de datos = métricas infladas = quedar en ridículo en Saskatoon si alguien pregunta.
No inventar precisión. Las métricas con datos sintéticos demuestran que el pipeline funciona, no la exactitud en mina real. Comunicarlo así, siempre.
Datos faltantes son estado normal, no excepción. Todo el pipeline corre con nodos caídos.
null ≠ 0. Un null es "este nodo no mide eso"; un 0 es "medí y dio cero". Tratarlos igual envenena el entrenamiento en silencio.
sensor_ok: false → descartar los gases de ese paquete como faltantes, jamás como ceros.
Reproducibilidad: seeds fijos, artefactos versionados, un solo comando para reentrenar (make train).
