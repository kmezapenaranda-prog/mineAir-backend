# Instrucciones para mineair-web — consumir /api/riesgo-conjunto

El schema (`riesgo_conjunto.schema.json`) ya está sincronizado en los tres repos.
Falta el código que lo consume — nadie lo escribió todavía. Esto es nuevo (no
reemplaza `/predicciones`), así que no rompe nada existente.

Forma real de la respuesta (confirmada contra el servicio en vivo):
```json
[{
  "schema_v": "riesgo-conjunto-1.0",
  "objetivo": "ch4_6h_o_co_1h",
  "node_id": "S1", "ubicacion": "Retorno frente A",
  "probabilidad": 0.117, "nivel": "normal", "recomienda_evacuar": false,
  "umbral_clasificacion": 0.55,
  "generada_en": "2026-09-13T00:37:15Z", "datos_hasta": "2026-09-13T00:36:56Z",
  "experimental": true, "confianza": "reducida",
  "features_faltantes": ["produccion_ton_h", "trabajadores", "..."],
  "factores": [{"nombre": "ch4_pct_lag_12h", "peso": 0.129, "valor": "0.37 % vol"}, "..."]
}]
```

Nota importante: **no tiene campo `gas`.** Es una probabilidad conjunta
(CH₄ en 6h O CO en 1h), no una probabilidad por gas — no intentar mostrarlo
con los mismos componentes que usan `p.gas` (como `Predicciones.jsx` hoy).

## 1. Adaptador — `src/data/edge/index.js`

Agregar junto a `getPredicciones`:
```js
async function getRiesgoConjunto() {
  return solicitar('/riesgo-conjunto')
}
```
Y añadirlo al objeto exportado `edgeDataSource`.

## 2. Mock — `src/data/mock/index.js`

El componente nuevo va a llamar `getRiesgoConjunto()` sin importar el origen
de datos (mock o edge). Agregar un stub para que no truene en desarrollo:
```js
getRiesgoConjunto: async () => [],
```
(Es "experimental" — no vale la pena fabricar datos falsos realistas para el
mock; una lista vacía es honesta y el componente ya debe manejar el caso sin
datos.)

## 3. Re-exportar — `src/data/index.js`

Añadir `getRiesgoConjunto` a la lista desestructurada de `fuente`.

## 4. Componente nuevo — `src/components/dashboard/RiesgoConjunto.jsx`

```jsx
import { useCargaPeriodica } from '../../lib/useCargaPeriodica.js'
import { getRiesgoConjunto } from '../../data/index.js'
import NivelBadge from '../NivelBadge.jsx'
import { NIVEL } from '../../config/umbrales.js'

// El vocabulario del modelo (normal/atencion/evacuar) no es el de la web
// (normal/precaucion/alarma/no_monitoreado) — se mapea, no se duplica.
const MAPA_NIVEL = { normal: NIVEL.NORMAL, atencion: NIVEL.PRECAUCION, evacuar: NIVEL.ALARMA }

export default function RiesgoConjunto() {
  const { datos, error, cargando } = useCargaPeriodica(getRiesgoConjunto, 20000)
  if (cargando && !datos) return null
  if (error || !datos?.length) return null

  return (
    <section className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold">Riesgo conjunto CH₄/CO</h2>
        <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-xs font-semibold text-amber-700">
          Experimental
        </span>
      </div>
      <p className="mb-3 text-xs text-muted-foreground">
        Probabilidad única de excedencia de CH₄ en 6h O de CO en 1h — es una
        decisión operativa conjunta, no una probabilidad por gas. Modelo en
        validación; no reemplaza las predicciones de /predicciones.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {datos.map((r) => (
          <div key={r.node_id} className="rounded-md border p-3">
            <div className="mb-1 flex items-center justify-between">
              <span className="font-medium">{r.ubicacion ?? r.node_id}</span>
              <NivelBadge nivel={MAPA_NIVEL[r.nivel] ?? NIVEL.NO_MONITOREADO} />
            </div>
            <p className="text-2xl font-bold">{(r.probabilidad * 100).toFixed(1)}%</p>
            {r.confianza === 'reducida' && (
              <p className="text-xs text-muted-foreground">
                Confianza reducida — faltan variables operativas ({r.features_faltantes.length})
              </p>
            )}
            <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
              {r.factores.slice(0, 3).map((f) => (
                <li key={f.nombre}>{f.nombre}: {f.valor}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  )
}
```

## 5. Montarlo — `src/pages/Dashboard.jsx`

Importar y renderizar `<RiesgoConjunto />` en algún punto del layout (recomendado:
arriba, junto al resto de tarjetas de estado — es la señal más nueva y experimental,
no debe quedar escondida al fondo de la página).

## Notas

- `datos_hasta` indica hasta qué instante de telemetría llega el cálculo — útil
  para mostrar "calculado con datos hasta las HH:MM" si se quiere dar contexto
  de qué tan al día está.
- El endpoint devuelve `[]` cuando el motor no tiene aún 12h de historia densa
  o cuando el nodo no tiene telemetría reciente (<60s) — el componente ya
  contempla `!datos?.length` para no mostrar nada en ese caso, no es un error.
- No hace falta tocar `contrato.test.js` de forma obligatoria, pero si se
  quiere replicar el patrón de "las predicciones mock cumplen el schema",
  sería un test nuevo validando fixtures de riesgo-conjunto contra
  `riesgo_conjunto.schema.json` — opcional, no bloqueante.
