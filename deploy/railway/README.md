# API MineAir en Railway

En Railway crea un servicio desde este repositorio y configura el Dockerfile
como `deploy/railway/Dockerfile`. Define `MINEAIR_DB_PATH=/data/estado_servicio.db`
y monta un Volume en `/data`; así la telemetría sobrevive a los reinicios.

Variables recomendadas:

- `MINEAIR_INTERNAL_TOKEN`: secreto usado por el publicador de predicciones.
- `MINEAIR_CORS_ORIGINS`: dominio público de la web.
- `PORT`: Railway lo inyecta automáticamente.

Configura el health check en `/api/estado` y comprueba `/api/estado` con el
dominio público antes de conectar la web.

El modelo está ignorado por Git (`models/**/*.json`). Hay que incluir el modelo
y su `.metadata.json` en el repositorio antes de desplegar.
