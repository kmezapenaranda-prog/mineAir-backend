# API MineAir en Railway

En Railway crea un servicio desde este repositorio y configura el Dockerfile
como `deploy/railway/Dockerfile`. Añade un servicio MySQL al proyecto y define
`DATABASE_URL=${{MySQL.MYSQL_URL}}` en la API. No necesitas montar un volumen.

Variables recomendadas:

- `MINEAIR_INTERNAL_TOKEN`: secreto usado por el publicador de predicciones.
- `MINEAIR_CORS_ORIGINS`: dominio público de la web.
- `PORT`: Railway lo inyecta automáticamente.

Configura el health check en `/api/estado` y comprueba `/api/estado` con el
dominio público antes de conectar la web.

El modelo está ignorado por Git (`models/**/*.json`). Hay que incluir el modelo
y su `.metadata.json` en el repositorio antes de desplegar.
