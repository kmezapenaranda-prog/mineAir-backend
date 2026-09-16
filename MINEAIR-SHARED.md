# MineAIr — Contrato Compartido (fuente única de verdad)

> **Contrato v1.8 — 2026-09-12**
>
> Este archivo se copia **idéntico** en los tres repos (`mineair-firmware`, `mineair-ml`, `mineair-web`).
> Ningún `CLAUDE.md` de track puede redefinir nada de lo que está aquí. Si un track necesita
> un cambio, se cambia **aquí**, se sube la versión, se copia a los otros dos repos y se anota
> en el Changelog del final. Nunca "lo arreglo en mi repo y ya".
>
> Todo paquete de telemetría lleva `schema_v`. Si un track recibe un `schema_v` que no conoce,
> **falla ruidosamente** — no adivina.

---

## 0. Cómo se usa este archivo con agentes

Cada repo tiene:

```
<repo>/
├── CLAUDE.md              # solo lo específico del track
├── MINEAIR-SHARED.md      # ESTE archivo, copia idéntica
└── contrato/
    ├── schemas/           # JSON Schema: telemetria, prediccion, variables_operativas
    └── fixtures/          # JSON de ejemplo, idénticos en los 3 repos
```

Regla para el agente de cada track: **antes de tocar cualquier estructura de datos, leer
`MINEAIR-SHARED.md`. Los tests del repo deben validar contra `contrato/schemas/`.**
Si los tres repos validan contra el mismo schema, la integración de septiembre es enchufar,
no depurar.

---

## 1. Decisiones de arquitectura ya tomadas (no reabrir sin avisar a los tres tracks)

| # | Decisión | Fecha | Estado |
|---|---|---|---|
| D1 | **Jetson Nano eliminado; reemplazado por Raspberry Pi.** El gateway de bocamina es una Raspberry Pi + módulo ESP32-LoRa, y el modelo ML corre **localmente en esa Pi** (ARM64), no en el computador de superficie. | 2026-08-06, revisado 2026-09-10 | Firme (revisada) |
| D2 | ~~El gateway de bocamina no calcula nada~~ — **superada por D1 revisado**: el gateway (la Pi) sí calcula, corre el pipeline ML completo y sirve la API. | 2026-08-06, superada 2026-09-10 | Reemplazada |
| D3 | Malla LoRa: **todos** los nodos retransmiten (fijos, cascos y repetidores dedicados), no solo los repetidores. | 2026-08-06 | Firme |
| D4 | El modelo corre **centralizado**, no por nodo — necesita el diferencial retorno vs. entrada. | — | Firme |
| D5 | Nodo `contador` de vagonetas para medir `produccion_ton` en vez de estimarla. | 2026-08-07 | Aceptado en contrato v1.0 |
| D6 | Cadencia única de telemetría: **1 paquete por nodo cada 15 s**. | 2026-08-10 | Firme |
| D7 | El **nodo de superficie es obligatorio**, no opcional. Sin presión barométrica exterior no hay tesis predictiva. | 2026-08-10 | Firme |

### Diagrama canónico

```
Puntos fijos (3)      Cascos (2)      Repetidores (4)     Contador (1..n)   Superficie (1)
gases + ambiente      gases +         solo retransmiten   solo conteo       solo presión
+ alarma local        ambiente                                              barométrica
      │                   │                  │                  │                 │
      └───── malla LoRa multi-salto: TODOS retransmiten (flooding con TTL) ────────┘
                                        │
                    Gateway = Raspberry Pi + ESP32-LoRa (bocamina)
                    - deduplica, pone timestamp UTC
                    - CORRE el servicio ML completo (FastAPI, red local)
                    - guarda el estado en SQLite localmente
                                        │
                    enlace de red bocamina → superficie  ⚠️ ver §7
                                        │
                    Computador del ingeniero (superficie)
                    └── aplicativo web (PWA, consume la API de la Pi por
                        red local — no localhost, salvo en desarrollo)
```

---

## 2. Inventario de nodos (idéntico en los 3 repos)

| `node_type` | Cant. | Reporta gases | Reporta `presion_hpa` | Reporta `conteo` | Retransmite |
|---|---|---|---|---|---|
| `fijo` | 3 | sí (5 gases) | no | no | sí |
| `casco` | 2 | sí, `co2_pct: null` | no | no | sí |
| `repetidor` | 4 | no (todo `null`) | no | no | sí |
| `contador` | 1+ | no (todo `null`) | no | sí | sí |
| `superficie` | 1 | no (todo `null`) | **sí** | no | sí |
| `gateway` | 1 | — | — | — | recibe |

`node_id`: `S1..S3` (fijos), `H1..H2` (cascos), `R1..R4` (repetidores), `C1..` (contadores),
`SUP1` (superficie), `GW1` (gateway).

---

## 3. BOM — hardware real ✅ CERRADO (compras del 2026-08-10/11)

| Componente | Cant. | Función | Interfaz |
|---|---|---|---|
| **ESP32 LoRa V3** (MakerFocus, ESP32-S3 8MB flash / 512KB SRAM + SX1262 915 MHz, OLED integrado) | **2** | Nodo y gateway | — |
| **Winsen ZCE04B** 4-en-1 (CO / H₂S / O₂ / CH₄) | **2** | Los 4 gases normativos, en dos puntos → **habilita el diferencial (D4)** | UART **2.8 V** |
| **Sensirion SCD41** | **1** | CO₂ + temp + humedad | I²C |
| **BME280** | **1** | Presión barométrica + temp + humedad | I²C (o SPI) |

### Specs del ZCE04B que afectan al contrato

| Gas | Rango del módulo | Resolución | Umbral Decreto 1886 | Margen |
|---|---|---|---|---|
| CO | 0–1000 ppm | 1 ppm | 25 ppm TLV-TWA | ✅ holgado |
| H₂S | 0–100 ppm | 1 ppm | 1 ppm TLV-TWA / 5 ppm STEL | ✅ cubre, resolución limitada en el TWA |
| O₂ | 0–30 % vol | 0.1 % vol | 19.5 % vol (mín.) | ✅ holgado |
| CH₄ | **0–100 % LEL** | **1 % LEL** | 1.0 % vol = **20 % LEL** | ⚠️ ver abajo |

Otros parámetros: alimentación 3.5–5 V, consumo < 100 mA, respuesta < 30 s, condiciones de
operación −20 a 50 °C y 15–90 % RH, vida útil ~2 años en aire limpio.

### ⚠️ Tres consecuencias técnicas que los tres tracks deben conocer

**1. El CH₄ sale en % LEL, no en % vol.** El LEL del metano es 5 % vol, así que:

```
ch4_pct (%vol) = lectura_LEL × 0.05
```

La conversión la hace **el firmware**, antes de transmitir (§5). La resolución de 1 % LEL
equivale a **0.05 % vol por escalón**. El umbral normativo (1.0 % vol) cae en el escalón 20 —
suficiente para la alarma reactiva, pero **grueso para el modelo predictivo**: las variaciones
sutiles de CH₄ que correlacionan con la presión barométrica pueden quedar por debajo del paso
de cuantización.

*Mitigación obligatoria:* el nodo muestrea cada 15 s y el pipeline agrega a 5 min (20 muestras).
Con el ruido propio del sensor actuando como dither, el promedio recupera resolución efectiva
por debajo del escalón. **El Track B debe promediar, nunca tomar la última lectura.** Si el ML
ve una señal escalonada en vez de continua, la detección del rezago producción→CH₄ se degrada.

**2. UART a 2.8 V contra un ESP32-S3 de 3.3 V.** La línea **ESP32 TX → ZCE04B RX** necesita
un divisor resistivo o level shifter; 3.3 V puede exceder el Vih del módulo. La dirección
contraria (2.8 V → ESP32 RX) se lee bien: el Vih del ESP32-S3 ronda 2.48 V.

```
ESP32 TX ──[ 1 kΩ ]──┬── ZCE04B RX     Vout = 3.3 × 5.6/6.6 = 2.8 V ✅
                     │
                  [ 5.6 kΩ ]
                     │
                    GND
```

Conectar directo **no destruye el módulo de inmediato**, pero degrada la entrada y produce
comunicación intermitente difícil de diagnosticar. Poner el divisor desde el primer prototipo.

**3. Consumo.** ZCE04B < 100 mA sostenidos + ESP32-S3 con LoRa (~80–120 mA en TX). Con una
celda 18650 de 3500 mAh la autonomía realista ronda **15–20 h**. Cubre un turno de 8 h con
margen, no cubre despliegue permanente sin alimentación externa.

### Limitaciones documentadas (decirlas en el pitch, no esconderlas)

| Limitación | Impacto | Cómo se comunica |
|---|---|---|
| **SCD41: rango especificado 400–5000 ppm** = 0.04–0.5 % vol, y el umbral de CO₂ del Decreto 1886 es exactamente 0.5 %. | Sirve para **recolectar datos y predecir** en el rango normal de mina (0.03–0.3 % vol). **No sirve como alarma certificada de CO₂.** | "El CO₂ se instrumenta para el modelo predictivo; la alarma certificada de CO₂ requiere un NDIR de rango minero en la versión de producción." |
| **ZCE04B: 15–90 % RH.** Las minas de carbón colombianas superan 90 % RH con frecuencia. | Operación fuera de especificación → deriva. | Registrar `humedad_pct` en toda campaña y reportar cuándo se salió de rango. |
| **Vida útil ~2 años** (electroquímicos se consumen). | Recalibración periódica obligatoria. | Es normal en la industria; incluirlo en el costeo de operación. |
| **Sin SO₂ ni NO₂.** | Dos gases del Decreto 1886 sin cubrir. | La UI los muestra como `"no monitoreado"`, nunca como `"normal"`. |

### 🔴 Calibración cruzada obligatoria antes de separar los dos ZCE04B

El diferencial retorno − entrada solo tiene sentido si **los dos sensores coinciden en aire
idéntico**. Si en el mismo aire limpio uno lee 0.10 % vol de CH₄ y el otro 0.25 %, el
diferencial que ve el modelo es error de sensor, no física de la mina — y el modelo aprende
ruido con toda confianza.

Procedimiento, antes de instalar nada:

1. Ambos módulos encendidos **más de 30 min** en el mismo aire limpio (respuesta < 30 s, pero
   los electroquímicos derivan al arrancar).
2. Registrar 30 min de lecturas simultáneas de los dos, a 15 s.
3. Calcular el **offset por gas** entre ambos y guardarlo en `config/calibracion.h`, indexado
   por `node_id`.
4. Aceptar el diferencial solo si la desviación residual queda por debajo de la resolución del
   sensor (1 % LEL para CH₄, 1 ppm para CO/H₂S, 0.1 % vol para O₂).
5. **Repetir la comparación después de la campaña**, para cuantificar deriva.

Esto además es material de pitch: "validamos consistencia entre sensores antes de desplegarlos"
es exactamente el tipo de rigor que un jurado de seguridad minera reconoce.

### Brecha de cantidades restante

El §2 define 11 nodos. Hoy hay **2 placas**. El diferencial de gases ya está cubierto (2 ZCE04B);
lo que sigue faltando es la **malla multi-salto**, que necesita mínimo 3–4 placas. Ver
`PLAN-HARDWARE.md`.

---

## 4. Umbrales normativos — Decreto 1886 de 2015 (Colombia)

Fuente única. En firmware van en `include/umbrales.h`; en ML en `src/config/umbrales.py`;
en web en `src/config/umbrales.js`. **Los tres archivos deben tener exactamente estos valores.**

| Gas | Clave contrato | Límite | Sentido | Nota |
|---|---|---|---|---|
| O₂ | `o2_pct` | 19.5–23.5 % vol | **RANGO** | ⚠️ no apto por debajo o por encima (art. 38) |
| CH₄ | `ch4_pct` | 1.0 % vol | máximo | en frentes de trabajo |
| CO₂ | `co2_pct` | 0.5 % vol | máximo | |
| CO | `co_ppm` | 25 ppm TLV-TWA | máximo | art. 39 |
| H₂S | `h2s_ppm` | 1 ppm TLV-TWA / 5 ppm STEL | máximo | art. 39 |
| SO₂ | — | 0.25 ppm STEL | máximo | **no monitoreado** por el hardware |
| NO₂ | — | 0.2 ppm TLV-TWA | máximo | **no monitoreado** por el hardware |

- El O₂ se controla como rango: menos de 19.5 % o más de 23.5 % no es atmósfera apta para
  trabajar o transitar. **Test unitario obligatorio en los tres tracks.**
- SO₂ y NO₂ se muestran como `"no monitoreado"`, nunca como `"normal"`.

---

## 5. Unidades — regla dura

| Magnitud | Unidad del contrato | Conversión desde sensor |
|---|---|---|
| CH₄ | `%` vol (`ch4_pct`) | LEL% → vol%: dividir por 20 (5 % vol = 100 % LEL) |
| O₂ | `%` vol (`o2_pct`) | directo |
| CO₂ | `%` vol (`co2_pct`) | **ppm → pct: dividir entre 10 000** |
| CO | `ppm` (`co_ppm`) | directo |
| H₂S | `ppm` (`h2s_ppm`) | directo |
| Presión | `hPa` (`presion_hpa`) | Pa → hPa: dividir entre 100 |
| Temperatura | `°C` | directo |

**La conversión ocurre en el firmware, antes de transmitir.** El ML y la web nunca convierten
unidades. Si un track está haciendo aritmética de unidades, hay un bug de contrato.

---

## 6. Tiempo — regla dura

El ESP32 **no tiene RTC ni internet bajo tierra**. No puede generar un ISO-8601 UTC confiable.

- **El nodo envía:** `t_ms` (milisegundos desde el boot, `uint32`) y `seq` (contador de
  paquete, `uint16`, con wraparound).
- **El gateway asigna:** `timestamp` en ISO-8601 UTC con `Z`, en el momento de la recepción.
- El gateway sí necesita hora fiable: la toma del computador de superficie al arrancar, o de
  un DS3231 si se decide independizarlo.
- La latencia de la malla (segundos) es despreciable frente a horizontes de 1–24 h.

`seq` no es decorativo: es lo que permite deduplicar en una malla por flooding y detectar
paquetes perdidos.

---

## 7. Enlace bocamina → superficie ⚠️ DECISIÓN ABIERTA (naturaleza del enlace cambió con D1 revisado)

Con D1 revisado (2026-09-10) el modelo ML corre **en la Raspberry Pi del gateway**, en
bocamina, no en el computador de superficie. Esto cambia qué tiene que viajar: ya no es
telemetría cruda cada 15 s (el JSON completo del contrato #1), sino solo lo que el aplicativo
web necesita consultar — peticiones HTTP a la API de la Pi (`/nodos`, `/predicciones`,
`/estado`). Es tráfico mucho más liviano y tolerante a intermitencia que streaming continuo
de sensores.

Opciones a evaluar con Track A (mismo abanico de antes, pero ahora para llevar tráfico de red
en vez de una trama serial):

| Opción | Alcance | Complejidad | Nota |
|---|---|---|---|
| WiFi local (Pi unida a la red de superficie, o como AP) | según terreno/antena | mínima | más simple si hay señal; varios modelos de Pi traen WiFi integrado |
| Ethernet (cable directo o PoE) | ~100 m por tramo | media | requiere infraestructura |
| RS-485 (MAX485) + puente serial↔IP | hasta ~1 200 m | media-alta | robusta a ruido; solo si WiFi/Ethernet no llegan |
| Segundo enlace LoRa | según terreno | baja | ancho de banda muy limitado para HTTP; último recurso |

**Mientras no haya enlace, el aplicativo web no puede consultar la Pi.** El pipeline sigue
operando y prediciendo localmente en la Pi (todo corre offline, §14) — solo se pierde la
visualización remota hasta que el enlace se restablece. El medio físico exacto sigue sin
decidirse; no asumir ninguno sin confirmar con Track A.

---

## 7.5 El JSON del contrato NO viaja por LoRa 🔴

El SX1262 admite un payload máximo de **255 bytes**. El JSON del contrato #1, en su forma más
compacta, ronda **350–420 bytes**. **No cabe.** Y aunque cupiera, el airtime a SF10 sería de
más de un segundo por paquete, con 11 nodos retransmitiendo cada 15 s.

Por lo tanto:

| Capa | Formato | Quién lo produce |
|---|---|---|
| **Aire (LoRa)** | Trama binaria compacta, ~30–40 bytes | El nodo |
| **Salida del gateway** | JSON del contrato #1 | El gateway |

**El contrato JSON es la salida del gateway, no la trama de radio.** Esto no cambia nada para
los Tracks B y C: ellos siguen viendo exactamente el JSON de §8. Es responsabilidad exclusiva
del Track A.

### Trama de radio sugerida (little-endian, ~34 bytes)

| Offset | Campo | Tipo | Nota |
|---|---|---|---|
| 0 | `ver` | `uint8` | versión de trama |
| 1 | `node_id` | `uint8` | índice, no string |
| 2 | `node_type` | `uint8` | enum |
| 3 | `ttl` | `uint8` | decrementa por salto |
| 4 | `seq` | `uint16` | dedup |
| 6 | `t_ms` | `uint32` | millis desde boot |
| 10 | `ch4` | `uint16` | ×0.01 % vol |
| 12 | `co` | `uint16` | ppm |
| 14 | `h2s` | `uint16` | ppm |
| 16 | `o2` | `uint16` | ×0.01 % vol |
| 18 | `co2` | `uint16` | ppm (el gateway convierte a %) |
| 20 | `temp` | `int16` | ×0.1 °C |
| 22 | `hum` | `uint8` | % |
| 23 | `presion` | `uint16` | ×0.1 hPa, offset 800 |
| 25 | `bateria` | `uint8` | % |
| 26 | `flags` | `uint8` | bit0 `sensor_ok`, bit1 `alarma_activa` |
| 27 | `gases_alarma` | `uint8` | bitmask de los 5 gases |
| 28 | `vagonetas` | `uint8` | incremental |
| 29 | `crc16` | `uint16` | integridad |

`ubicacion`, `frente` y `fw_version` **no viajan por radio**: son metadatos estáticos que el
gateway resuelve desde una tabla local indexada por `node_id`. Retransmitirlos cada 15 s es
desperdicio puro de airtime.

El gateway expande esta trama al JSON completo, le pone el `timestamp` ISO-8601 (§6) y lo
entrega al servicio ML.

---

## 8. Contrato #1 — Telemetría (nodo → gateway → servicio ML → web)

Un paquete por nodo cada **15 s**. Schema: `contrato/schemas/telemetria.schema.json`.

```json
{
  "schema_v": "1.0",
  "node_id": "S1",
  "node_type": "fijo",
  "ubicacion": "Retorno frente A",
  "frente": "A",
  "timestamp": "2026-09-03T14:30:15Z",
  "seq": 4821,
  "t_ms": 72315000,
  "gases": {
    "ch4_pct": 0.42,
    "co_ppm": 12,
    "h2s_ppm": 0,
    "o2_pct": 20.6,
    "co2_pct": 0.08
  },
  "ambiente": {
    "temp_c": 28.4,
    "humedad_pct": 82,
    "presion_hpa": null
  },
  "conteo": null,
  "alarma_local": {
    "activa": false,
    "gases": []
  },
  "estado": {
    "bateria_pct": 78,
    "rssi_dbm": -87,
    "snr_db": 8.5,
    "saltos": 2,
    "sensor_ok": true,
    "fw_version": "0.3.1"
  }
}
```

### Reglas de campos

- `schema_v` — obligatorio en todo paquete. Un receptor que no reconozca la versión **rechaza
  el paquete y lo registra**, no lo interpreta a medias.
- `node_type` ∈ `fijo` | `casco` | `repetidor` | `contador` | `superficie`.
- **Campo no aplicable = `null`, nunca `0`.** Un `0` en `ch4_pct` significa "medí y dio cero";
  un `null` significa "este nodo no mide eso". Confundirlos envenena el entrenamiento del modelo.
- `co2_pct` puede ser numérico en el casco del piloto, según el firmware acordado con el equipo. Es `null` cuando ese casco no mide CO₂; el receptor conserva las mediciones recibidas.
- `presion_hpa` solo lo llena `SUP1` (y opcionalmente los fijos si llevan BME280).
- `conteo` es `null` salvo en `node_type: "contador"`, donde vale `{ "vagonetas": 0|1|... }`
  — **incremental desde el paquete anterior de ese nodo**, no acumulado. La suma sobre
  cualquier rango es exacta.
- `alarma_local.activa` — la alarma **reactiva** que dispara el ESP32 por umbral, independiente
  de todo lo demás. `alarma_local.gases` lista las claves excedidas, ej. `["ch4_pct","o2_pct"]`.
  La web **muestra** este estado; nunca lo controla ni lo recalcula.
- `sensor_ok: false` → las lecturas de gases de ese paquete se tratan como **faltantes**, no
  como ceros. El ML las ignora; la web muestra "sensor en falla".
- `saltos` — número de retransmisiones que llevó el paquete. Sirve para diagnosticar la malla.
- La equivalencia vagonetas → toneladas (`capacidad_ton_vagoneta`) vive en la **Configuración
  del aplicativo web**, no en el firmware. El tamaño de carro cambia sin reflashear nada.

---

## 9. Contrato #2 — Variables operativas (web → servicio ML)

Las captura el aplicativo web por formulario al cierre de turno. Schema:
`contrato/schemas/variables_operativas.schema.json`.

```json
{
  "schema_v": "1.0",
  "fecha": "2026-09-03",
  "turno": "noche",
  "frente": "A",
  "manto": "Manto 3",
  "produccion_ton": 42.5,
  "produccion_origen": "contada",
  "indice_gasificacion_m3_ton": 8.2,
  "ventilador_principal_on": true,
  "caudal_m3_s": 12.4,
  "voladuras": [ { "hora": "22:15", "cantidad_kg": 25 } ],
  "observaciones": "Se detectó filtración en zona sellada norte",
  "registrado_por": "usuario_id"
}
```

- `turno` ∈ `manana` | `tarde` | `noche` (sin tilde, para no pelear con encodings en serial).
- `produccion_origen` ∈ `contada` (suma de nodos `contador`) | `estimada` (digitada por el
  ingeniero). El modelo debe saber la diferencia: un dato contado y uno estimado no merecen
  el mismo peso.
- El nodo contador **no reemplaza** este formulario. Solo mejora `produccion_ton`.
  `indice_gasificacion_m3_ton`, voladuras, ventilador y observaciones siguen viniendo de aquí.

---

## 10. Contrato #3 — Predicción (servicio ML → web)

Schema: `contrato/schemas/prediccion.schema.json`.

### Alcance predictivo v1

- El sistema **monitorea** CH₄, CO, H₂S, O₂ y CO₂ mediante el contrato de telemetría.
- El servicio ML **predice únicamente CH₄ y CO** en esta versión. Son las dos salidas admitidas
  por el contrato de predicción.
- El O₂ se mantiene bajo **control crítico reactivo**: se mide, se visualiza y dispara alarma
  local por debajo de 19.5 % vol. No se predice en esta fase.
- H₂S se monitorea y conserva alarma reactiva, pero no tiene salida predictiva en esta fase.
- CO₂ alimenta el contexto del modelo dentro del rango útil del SCD41; no se presenta como
  alarma certificada ni como objetivo predictivo.
- SO₂ y NO₂ permanecen como `no monitoreado` por ausencia de hardware.

```json
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
  "recomienda_evacuar": true,
  "confianza": "reducida",
  "nodos_faltantes": ["S3"],
  "generada_en": "2026-09-03T14:30:20Z",
  "factores": [
    { "nombre": "produccion_turno", "peso": 0.28, "valor": "+35% sobre promedio" },
    { "nombre": "presion_barometrica", "peso": 0.24, "valor": "-4 hPa / 12h" },
    { "nombre": "indice_gasificacion", "peso": 0.18, "valor": "8.2 m³/ton" }
  ]
}
```

- `gas` ∈ `ch4` | `co`. Una salida para otro gas viola el contrato v1.
- `horizonte_h` ∈ `1` | `6` | `12` | `24`.
- `sentido` = `max` en las predicciones v1, porque CH₄ y CO exceden hacia arriba. La inversión
  de O₂ pertenece al monitoreo y alarma reactiva del contrato de telemetría.
- `nivel` ∈ `normal` | `atencion` | `evacuar` — banda de alerta temprana sobre la probabilidad
  predicha, contra el umbral de decisión del propio modelo (no un umbral normativo del Decreto
  1886: para eso está `/nodos.proximidad_normativa`, que mira la lectura actual, no la
  predicción a futuro). `atencion` avisa **antes** de que el modelo llegue a recomendar
  evacuar, con margen para que el ingeniero verifique en sitio sin esperar la alarma máxima.
  Añadido en v1.7 porque un solo booleano no distingue "todavía tranquilo" de "subiendo rápido,
  hay que mirar" — la v1.5 lo había simplificado a un booleano; v1.7 reintroduce un nivel
  intermedio, pero solo uno (no los tres niveles arbitrarios de antes de v1.5), atado
  directamente a la probabilidad del modelo, no a un texto libre.
- `recomienda_evacuar`: `true` si y solo si `nivel = "evacuar"` (se mantiene por compatibilidad
  y porque sigue siendo el campo más simple de consumir cuando no importa la banda intermedia).
  Es una **recomendación preventiva**, no una orden: la decisión real de evacuar o suspender
  depende siempre de la medición en sitio y del responsable de higiene y seguridad, nunca de
  este booleano por sí solo. La UI debe presentarlo como "se recomienda verificar / evacuar
  preventivamente", jamás como "evacuando" o una acción ya en curso.
- `confianza` ∈ `normal` | `reducida` — `reducida` cuando hay nodos caídos. XGBoost tolera
  faltantes de forma nativa: **el modelo sigue prediciendo, nunca lanza error**. La UI muestra
  "predicción con confianza reducida", no un error.
- `factores` — salida de LASSO, ordenada por peso descendente. **Siempre se muestran.** La
  explicabilidad es lo que hace que un ingeniero de minas confíe en el número.
- **Nunca presentar la predicción como certeza.** Es probabilidad y así debe leerse.

---

## 11. Manejo de fallas — comportamiento acordado

| Situación | Firmware | ML | Web |
|---|---|---|---|
| Sensor fuera de rango | `sensor_ok: false`, gases igual se envían | descarta los gases de ese paquete como faltantes | muestra "sensor en falla" |
| Nodo sin reportar > 60 s | — | trata como faltante, sigue prediciendo | nodo en gris, "sin datos hace X" |
| Gateway caído | sigue alarmando local | último snapshot, `confianza: reducida` | banner "sin conexión", timestamp del último dato |
| `schema_v` desconocido | — | rechaza y loguea | rechaza y loguea |
| Batería < 15 % | alarma visual propia | ignora | badge de batería baja |

Principio transversal: **datos faltantes son estado normal, no excepción.** Todo el pipeline
debe correr con nodos caídos. Y **la alarma local nunca depende de la red**: buzzer y LED se
disparan en el ESP32 aunque toda la cadena esté muerta.

---

## 12. Malla LoRa — reglas mínimas para que no se sature

- 915 MHz (banda ISM Colombia).
- **Flooding controlado**: cada nodo retransmite un paquete solo si (a) `ttl > 0` y (b) no ha
  visto ya ese `(node_id, seq)` en su caché reciente (últimos ~64 pares).
- `ttl` inicial: 5. Se decrementa en cada salto. Se refleja en `estado.saltos`.
- Sin dedup por `(node_id, seq)` la malla entra en tormenta de broadcast en minutos. Esto no
  es opcional.
- Alcance real por tramo: **se mide en campo**, no se asume. Referencia: 80–150 m en túnel
  irregular, 150–300 m en tramo recto.
- Redundancia deseada: cada nodo alcanza ≥ 2 vecinos, para que la caída de un repetidor no
  corte la cadena.

---

## 13. Seguridad intrínseca — límite honesto del prototipo

- El prototipo actual sirve para **recolección de datos en condiciones controladas y prueba de
  concepto**. No reemplaza sistemas de seguridad certificados en mina activa.
- Los sensores cerca de zonas con riesgo de grisú requieren **barrera de seguridad intrínseca
  certificada**, calculada con los Ui/Ii/Pi del datasheet.
- La certificación de fábrica (p. ej. GB3836 china) **no cubre automáticamente** uso en minas
  de carbón colombianas.
- El encapsulado en resina **no sustituye** la seguridad intrínseca: son mecanismos distintos
  (limitación eléctrica vs. barrera física).
- Esto se dice igual en el pitch. En Saskatoon, decirlo abiertamente suma credibilidad; que
  te lo saquen en preguntas la destruye.

---

## 14. Qué NO se promete (los tres tracks, igual)

- **No inventar precisión.** Las métricas sobre datos sintéticos demuestran que el pipeline
  funciona, no la exactitud en mina real. Se comunica exactamente así.
- **No prometer un horizonte fijo.** El horizonte real se determina con el piloto. La UI maneja
  1/6/12/24 h, no asume uno.
- **Nada de tono de vigilancia.** Ni textos, ni iconos, ni presentación de la ubicación del
  personal. Se muestra qué respira el trabajador y qué va a pasar con el aire — no dónde estuvo
  ni qué hizo. *La IA es un coach del minero, no un vigilante.*

---

## 15. Fixtures compartidos

`contrato/fixtures/` contiene los mismos JSON en los tres repos. Los tests de cada track los
cargan y validan contra el schema. Mínimo obligatorio:

```
fixtures/
├── telemetria_fijo_normal.json
├── telemetria_fijo_alarma_ch4.json
├── telemetria_casco_co2_null.json
├── telemetria_superficie_solo_presion.json
├── telemetria_contador.json
├── telemetria_sensor_falla.json
├── telemetria_o2_bajo.json          # el caso invertido, obligatorio
├── variables_operativas_turno.json
├── prediccion_alarma_ch4_6h.json
└── prediccion_o2_min_confianza_reducida.json
```

Si los tres repos pasan estos fixtures, la integración de septiembre es conectar cables.

---

## Changelog del contrato

Extensión v1.8 del servicio ML: `GET /api/riesgo-conjunto` devuelve una sola
probabilidad del evento `ch4_6h_o_co_1h` bajo `schema_v: riesgo-conjunto-1.0`.
No se publica esta probabilidad en los slots por gas de `/predicciones`.
Cada resultado incluye node_id, ubicacion, objetivo, probabilidad, nivel,
recomienda_evacuar, umbral_clasificacion, generada_en, datos_hasta, confianza,
features_faltantes, factores y experimental=true. El último campo identifica
el artefacto sintético aún no validado en mina. Schema: `riesgo_conjunto.schema.json`. Los factores son coeficientes
LASSO normalizados (importancia global, no atribución causal de esa lectura).
El motor espera 12 h de historia; si el sensor falla o falta alguno de los gases
actuales, se abstiene. Los resultados vencen a los 10 min y se ocultan al
perder actualidad del nodo (>60 s). La cadencia de cálculo es 5 min.

El puente USB del host asigna UTC al recibir, antes de encolar para HTTP;
los reintentos mantienen ese timestamp. La deduplicación sin boot_id compara
node_id, seq y t_ms dentro de 60 s; sin t_ms solo deduplica igual timestamp.
Dos arranques indistinguibles (misma secuencia y uptime en esa ventana)
requieren un boot_id futuro para distinguirse. No se garantiza deduplicación
de retransmisiones radio demoradas más de 60 s.

| Versión | Fecha | Cambio |
|---|---|---|
| 1.8 | 2026-09-12 | CO₂ numérico permitido en casco del piloto; extensión de riesgo conjunto separada de predicciones por gas; UTC del host, vigencia y limitación explícita de deduplicación sin boot_id. |
| 1.7 | 2026-09-12 | **Contrato #3:** nuevo campo `nivel` (`normal`/`atencion`/`evacuar`) — banda de alerta temprana sobre la probabilidad del modelo, para avisar antes de llegar al corte de evacuar. `recomienda_evacuar` se mantiene, ahora definido como `nivel == "evacuar"`. No afecta el contrato #1 (telemetría) ni el #2 (variables operativas). |
| 1.6 | 2026-09-10 | **D1 revisado:** el modelo ML deja de correr en el computador de superficie y pasa a correr **localmente en el gateway**, que ahora es una Raspberry Pi + ESP32-LoRa (no un módulo LoRa "tonto" como decía D2 original — D2 queda superada). El aplicativo web consulta la API de la Pi por red local en vez de `localhost`. §7 se reformula: ya no hay que llevar telemetría cruda a superficie, solo tráfico HTTP liviano de la API. Sigue pendiente el medio físico exacto del enlace de red bocamina→superficie. |
| 1.5 | 2026-09-06 | Contrato #3 simplificado: `nivel` (normal/precaucion/alarma) y `recomendacion` (texto libre) se reemplazan por un solo booleano `recomienda_evacuar` (`probabilidad > 0.60`). Sigue siendo una recomendación preventiva, nunca una orden de evacuación automática — ese principio de la v1.4 no cambia, solo la forma en que se expone. |
| 1.4 | 2026-08-13 | Umbrales corregidos contra los artículos 38 y 39 del Decreto 1886 consolidado: O₂ 19.5–23.5 %, CO 25 ppm TWA, H₂S 1 ppm TWA/5 ppm STEL, SO₂ 0.25 ppm STEL y NO₂ 0.2 ppm TWA. Recomendaciones predictivas deben ser preventivas, trazables por artículo y nunca convertir una probabilidad en una orden de evacuación. |
| 1.3 | 2026-08-12 | Alcance de gases formalizado: monitoreo de CH₄/CO/H₂S/O₂/CO₂; predicción v1 limitada a CH₄ y CO; O₂ bajo control crítico reactivo invertido; H₂S reactivo; CO₂ como contexto no certificado; SO₂/NO₂ no monitoreados. |
| 1.2 | 2026-08-11 | Segundo ZCE04B comprado. **El diferencial retorno − entrada (D4) pasa a ser demostrable con hardware real.** Nueva arquitectura de demo: N1 en retorno, N2 en bocamina haciendo triple función (gases de entrada + barométrica + gateway). Añadido el procedimiento de **calibración cruzada obligatoria** entre los dos ZCE04B. La brecha restante es solo de placas para la malla multi-salto. |
| 1.1 | 2026-08-11 | **BOM cerrado** con las compras reales (2× ESP32 LoRa V3, 1× ZCE04B, 1× SCD41, 1× BME280). Añadidas las tres consecuencias técnicas del ZCE04B: CH₄ en % LEL (conversión ×0.05), UART a 2.8 V (divisor obligatorio), consumo. Añadido §7.5: **el JSON del contrato no cabe en un paquete LoRa** — trama binaria en el aire, JSON a la salida del gateway. Documentadas las limitaciones del SCD41 y del rango de humedad. Registrada la brecha de cantidades (2 placas de 11, 1 sensor de gases). |
| 1.0 | 2026-08-10 | Contrato unificado. Resuelve las divergencias entre los tres MD: nombres de campo (`node_id` vs `id_nodo`), timestamp ISO-8601 asignado por gateway, CO₂ en `pct` (no ppm), `h2s_ppm` obligatorio, `alarma_local` incorporada desde el firmware, `presion_hpa` y nodo `superficie` obligatorios, `node_type: contador` propagado a los tres tracks, Jetson eliminado, cadencia fija en 15 s, `schema_v` y `seq` añadidos. |

### Pendientes para v1.2

1. **Decidir si se compran más placas y un segundo ZCE04B** (ver `PLAN-HARDWARE.md`). Plazo
   crítico: envío desde China a Cúcuta antes del 24 de septiembre.
2. Decidir el enlace bocamina → superficie del §7.
3. Decidir si la malla se implementa a mano (RadioLib) o sobre Meshtastic — las placas vienen
   con Meshtastic preinstalado.
4. Definir `capacidad_ton_vagoneta` por frente.
5. Medir alcance LoRa real en campo y ajustar cantidad de repetidores.
6. Fijar la tabla `node_id` → (`ubicacion`, `frente`) que resuelve el gateway (§7.5).
