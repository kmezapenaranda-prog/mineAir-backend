FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts
COPY *.schema.json ./
COPY models/evacuar ./models/evacuar

EXPOSE 8000
CMD ["sh", "-c", "exec python -m uvicorn src.servicio.api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
