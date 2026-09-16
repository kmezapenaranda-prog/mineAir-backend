# Instrucciones para mineair-web — contrato v1.7

Contexto: el servicio ML (mineair-ml) subió el contrato de predicción a v1.7
(nuevo campo `nivel`) y agregó `proximidad_normativa` a `GET /nodos`. Nada de
esto rompe la web hoy (el cliente ignora campos que no conoce), pero hay que
sincronizar y decidir un par de cosas. Ver `mineair-ml/MINEAIR-SHARED.md`
changelog v1.7 y `mineair-ml/CLAUDE-ml.md` para el detalle del lado ML.

---

## 1. Sincronizar el contrato (hacer sí o sí)

Copiar de mineair-ml a mineair-web, sin modificar:

- `prediccion.schema.json`
- `MINEAIR-SHARED.md`

Esto es solo higiene de contrato — los tres repos deben tener copias
idénticas. No cambia comportamiento por sí solo.

---

## 2. Decisión de producto: ¿quién decide `nivel`?

**El conflicto real:** `src/data/predicciones.js::presentarPrediccion` ya
calcula su propio `nivel` en el cliente (`clasificarPrediccion`, en
`src/config/umbrales.js`), con cortes editables por el ingeniero desde
Configuración (`CORTES_NIVEL_PREDICCION`, persistido en localStorage). El
spread `{ ...prediccion, nivel, ... }` PISA cualquier `nivel` que mande el
servicio con el calculado en el cliente. Además `tests/contrato.test.js:42`
afirma explícitamente que el servicio nunca manda `nivel`:
```js
assert.equal(Object.hasOwn(originales[i], 'nivel'), false)
```
Y el vocabulario no coincide: servicio usa `normal/atencion/evacuar`, web usa
`normal/precaucion/alarma/no_monitoreado`.

### Opción A — Mínima, recomendada por ahora: el cliente sigue decidiendo

No tocar `presentarPrediccion` ni `umbrales.js`. El campo `nivel` del
servicio simplemente se ignora (ya pasa hoy). Solo hace falta:

- Nada más. Ya funciona así. Es la opción de menor riesgo si no hay tiempo
  antes de Saskatoon.

Desventaja: se pierde el criterio del modelo (que usa su propio umbral
optimizado, hoy 0.455, no el 0.60 fijo que asume `CORTES_NIVEL_PREDICCION`
por defecto) — el cliente sigue clasificando con un corte que no coincide
con el que realmente usa el modelo entrenado.

### Opción B — Consolidar: el servicio decide, el cliente confía

1. En `src/config/umbrales.js`: no hace falta agregar valores a `NIVEL`
   (`precaucion`/`alarma` ya existen) — el mapeo es
   `servicio.atencion → precaucion`, `servicio.evacuar → alarma`.
2. En `src/data/predicciones.js::presentarPrediccion`: mapear el `nivel` del
   servicio al vocabulario de la web en vez de recalcularlo:
   ```js
   const MAPA_NIVEL_SERVICIO = { normal: NIVEL.NORMAL, atencion: NIVEL.PRECAUCION, evacuar: NIVEL.ALARMA }
   const nivel = MAPA_NIVEL_SERVICIO[prediccion.nivel] ?? clasificarPrediccion(prediccion.probabilidad)
   ```
   (el `??` es fallback para el mock, que no manda `nivel` — ver punto 3).
3. En `src/data/mock/predicciones.js`: agregar un campo `nivel` calculado
   igual que hoy (`clasificarPrediccion`) para que el mock siga siendo
   consistente con el shape real del servicio.
4. En `tests/contrato.test.js:42`: cambiar la aserción — ya no es cierto que
   el servicio nunca mande `nivel`. Reemplazar por una prueba de que
   `presentarPrediccion` respeta el `nivel` del servicio cuando viene.
5. Aceptar que `CORTES_NIVEL_PREDICCION` en Configuración deja de tener
   efecto para predicciones reales (el servicio ya decidió) — solo seguiría
   aplicando al mock. Si el ingeniero necesita ajustar el corte de alarma,
   eso ahora vive en el modelo (`models/evacuar/*.metadata.json` en
   mineair-ml), no en la web. Avisar esto explícitamente en la UI de
   Configuración si se toma esta opción.

**Recomendación:** A ahora (no hay nada urgente que arreglar), B después del
piloto si se quiere que el umbral del modelo real mande sobre el corte fijo
del cliente.

---

## 3. `proximidad_normativa` de `/nodos` — mayormente redundante

`src/config/umbrales.js::clasificarLectura` + `src/lib/nivelNodo.js` YA
calculan esto client-side (normal/precaución/alarma por gas, desde la
lectura actual, con cortes configurables). El campo nuevo del servicio no
aporta nada ahí — **no hace falta consumirlo para eso.**

Lo único que la web no tiene: una banda extra para CH4 acercándose al LEL
completo (5.0 %vol), más grave que exceder el límite regulatorio (1.0 %vol =
20% LEL). Dos formas de cerrar ese hueco, elegir una:

- **Extender lo propio** (consistente con la arquitectura actual): agregar
  `lel: 5.0` a `UMBRALES_DECRETO_1886.ch4` en `umbrales.js` y una cuarta
  categoría en `clasificarLectura` (o una función nueva
  `riesgoExplosivo(valor)`) para CH4 específicamente. No depende de la API.
- **Consumir el campo nuevo:** leer
  `nodo.proximidad_normativa.ch4.nivel === "riesgo_explosivo"` desde
  `GET /nodos` y mostrarlo aparte del semáforo normal. Requiere que
  `edgeDataSource.getNodos` (ya lo hace, pasa la respuesta tal cual) no se
  toque; solo el componente que renderiza el nodo necesita leer ese campo
  nuevo (candidato: `DetalleNodo.jsx` o el marcador en `Mapa.jsx`).

**Recomendación:** la primera opción (extender `umbrales.js`) es más
consistente con cómo ya está construida la web — el semáforo agregado sigue
siendo 100% client-side y no depende de que la API esté arriba para mostrar
algo.

---

## Resumen ejecutivo

| Ítem | Urgencia | Esfuerzo |
|---|---|---|
| 1. Sincronizar schema + MINEAIR-SHARED.md | Alta (higiene de contrato) | Trivial |
| 2. Decidir A vs B para `nivel` predictivo | Media | A: ninguno / B: ~1-2h |
| 3. Banda CH4 cerca del LEL | Baja (nice-to-have) | ~30 min si se extiende `umbrales.js` |
