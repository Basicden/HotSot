FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared library
COPY shared/ /app/shared/

# All services
COPY services/ /app/services/

# Default: run order-service (override SERVICE env var per container)
ENV SERVICE=order
ENV PORT=8000

CMD uvicorn services.${SERVICE}-service.app.main:app --host 0.0.0.0 --port ${PORT}
