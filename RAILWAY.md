# Despliegue de MineAir en Railway

MineAir se despliega como dos servicios independientes:

1. **mineair-api** desde este repositorio, con `deploy/railway/Dockerfile`.
2. **mineair-web** desde el repositorio de la web, con
   `deploy/railway/Dockerfile`.

## API

En `https://railway.com/new` selecciona **Deploy from GitHub repo**, elige
`mineair-ml` y abre **Settings → Build → Dockerfile path**. Usa
`deploy/railway/Dockerfile`. Añade un servicio MySQL al proyecto y, en
**Variables** de la API, referencia su URL:

```text
DATABASE_URL=${{MySQL.MYSQL_URL}}
MINEAIR_INTERNAL_TOKEN=<secreto-largo-y-aleatorio>
MINEAIR_CORS_ORIGINS=https://<dominio-de-la-web>
```

No hace falta un volumen para la API: los datos viven en MySQL. En **Healthcheck**
usa `/api/estado`. Haz **Generate Domain** y prueba
`https://<dominio>/api/estado`.

El modelo se copia dentro de la imagen. Como `models/**/*.json` está ignorado
por Git, incluye previamente estos dos archivos en el repositorio:

```text
models/evacuar/evacuar_woa_xgboost.json
models/evacuar/evacuar_woa_xgboost.metadata.json
```

## Web

Crea otro servicio desde el repositorio `mineair-web`. Selecciona
`deploy/railway/Dockerfile` como Dockerfile path y añade como variables de
**build**:

```text
VITE_DATA_SOURCE=edge
VITE_EDGE_API_URL=https://<dominio-de-la-api>/api
```

Después de desplegar, genera el dominio de la web y actualiza
`MINEAIR_CORS_ORIGINS` en la API con ese dominio.

## Raspberry Pi y ESP32

Railway no puede abrir el COM8 ni recibir USB. El ESP32 y
`scripts/recibir_gateway.py` deben permanecer conectados a la Raspberry Pi.
Si la API también vive en Railway, ejecuta el puente en la Pi apuntando al
dominio público de la API y conserva una cola local para cuando no haya
Internet. Para operación crítica, deja la API y el modelo en la Pi y usa
Railway solo para la web o como entorno de demostración.
