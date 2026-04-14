"""
HotSot Arrival Service — FastAPI Application
=============================================
Main entry point for the arrival detection microservice.

Startup:
  - Initialise Redis connection pool
  - Verify Kafka connectivity (best-effort)

Shutdown:
  - Close Redis and Kafka connections gracefully
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.routes.arrival import router as arrival_router

# ── Logging ────────────────────────────────────────────────────────────

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(settings.service_name)


# ── Lifespan (startup / shutdown) ──────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage resources that live for the duration of the application."""
    logger.info("🚀 %s starting up", settings.service_name)
    logger.info("   Redis URL        : %s", settings.redis_url)
    logger.info("   Kafka brokers    : %s", settings.kafka_bootstrap_servers)
    logger.info("   Arrival radius   : %sm", settings.arrival_radius_m)
    logger.info("   QR rotation      : %ss", settings.qr_token_rotation_s)
    logger.info("   Dedup window     : %ss", settings.dedup_window_s)
    logger.info("   Priority boost   : +%d", settings.priority_boost_amount)

    # Pre-warm Redis connection
    from app.core.dedup import ArrivalDedupEngine
    engine = ArrivalDedupEngine()
    try:
        r = await engine._get_redis()
        await r.ping()
        logger.info("   Redis connection  : OK")
    except Exception:
        logger.warning("   Redis connection  : FAILED (will retry on demand)")

    yield  # ── app is running ──────────────────────────────────────────

    # Graceful shutdown
    logger.info("🛑 %s shutting down", settings.service_name)
    try:
        if engine._redis:
            await engine._redis.close()
            logger.info("   Redis connection closed")
    except Exception:
        logger.exception("   Error closing Redis")


# ── App ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="HotSot Arrival Service",
    description=(
        "Detects user arrivals at kitchen pickup locations via GPS or QR scan. "
        "Hard arrivals trigger priority boosts; soft arrivals are logged only. "
        "Deduplication prevents duplicate events within configurable time windows."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Routes ─────────────────────────────────────────────────────────────

app.include_router(arrival_router)


# ── Health / readiness ─────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health():
    """Liveness probe."""
    return {"status": "ok", "service": settings.service_name}


@app.get("/ready", tags=["ops"])
async def readiness():
    """Readiness probe — checks Redis connectivity."""
    from app.core.dedup import ArrivalDedupEngine
    engine = ArrivalDedupEngine()
    try:
        r = await engine._get_redis()
        await r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    return {
        "status": "ok" if redis_ok else "degraded",
        "service": settings.service_name,
        "redis": redis_ok,
    }
