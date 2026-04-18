FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python deps — root first, then service-specific (if exists)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared library
COPY shared/ /app/shared/

# All services
COPY services/ /app/services/

# Install service-specific requirements if they exist
# This ensures each service gets its own dependencies in Docker
ARG SERVICE=order
RUN if [ -f "services/${SERVICE}-service/requirements.txt" ]; then \
        pip install --no-cache-dir -r "services/${SERVICE}-service/requirements.txt"; \
    fi

# Default: run order-service (override SERVICE env var per container)
ENV SERVICE=order
ENV PORT=8000

CMD uvicorn services.${SERVICE}-service.app.main:app --host 0.0.0.0 --port ${PORT}
