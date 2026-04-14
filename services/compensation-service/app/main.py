"""
HotSot Compensation Service — FastAPI Application
===================================================
Handles refund decisions, UPI refund processing, kitchen penalty tracking,
and compensation event emission.  Fully idempotent via Redis dedup.

Endpoints
---------
  POST /compensation/evaluate   — Evaluate a compensation request
  GET  /compensation/{order_id} — Lookup compensation status for an order
  GET  /kitchen/{kitchen_id}/penalty — Get kitchen penalty status
  GET  /health                  — Service health check
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.engine import CompensationEngine, compensation_engine
from app.core.config import settings
from app.routes import compensation

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s",
)


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialise Redis + Kafka.  Shutdown: flush & close."""
    logger.info("CompensationService starting …")
    compensation_engine.init_connections()
    yield
    logger.info("CompensationService shutting down …")
    compensation_engine.close_connections()


# ── App ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="HotSot Compensation Service",
    version="2.0.0",
    description="Refund decisions, UPI refund processing, kitchen penalty tracking",
    lifespan=lifespan,
)

app.include_router(compensation.router)


@app.get("/health")
async def health():
    return {
        "service": "compensation-service",
        "version": "2.0.0",
        "status": "healthy",
        "redis_connected": compensation_engine.redis_client is not None,
        "kafka_connected": compensation_engine.kafka_producer is not None,
    }
