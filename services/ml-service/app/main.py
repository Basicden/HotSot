"""HotSot ML Service — Model Training + Inference + Feature Store."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
import redis as redis_lib

from app.routes.ml import router as ml_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s")

redis_client: redis_lib.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    logger.info("MLService starting — Training + Inference ready")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/5")
    try:
        redis_client = redis_lib.from_url(redis_url, decode_responses=True)
        redis_client.ping()
        logger.info("Redis connected: %s", redis_url)
    except Exception as exc:
        logger.warning("Redis connection failed (non-fatal): %s", exc)
        redis_client = None
    yield
    if redis_client:
        try:
            redis_client.close()
        except Exception:
            pass
    logger.info("MLService shutting down …")


app = FastAPI(title="HotSot ML Service", version="2.0.0", lifespan=lifespan)
app.include_router(ml_router, prefix="/ml", tags=["ml"])


@app.get("/health")
async def health():
    return {
        "service": "ml-service",
        "version": "2.0.0",
        "status": "healthy",
        "redis_connected": redis_client is not None,
    }
