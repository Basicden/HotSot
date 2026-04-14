# ── Build stage ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ───────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="HotSot Engineering"
LABEL description="HotSot Arrival Service — user arrival detection for pickup-first food commerce"

# Non-root user for security
RUN groupadd -r hotsot && useradd -r -g hotsot -d /app hotsot

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app/ ./app/

# Switch to non-root user
USER hotsot

# Expose the default port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--loop", "uvloop", "--log-level", "info"]
