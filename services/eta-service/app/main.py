"""HotSot ETA Service — ML-Based ETA Prediction Engine.

V2: Includes Kafka producer for eta.updated events so downstream services
(notifications, realtime dashboard) can react to ETA changes in real-time.
"""

import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from kafka import KafkaProducer

from app.core.redis_client import init_redis, close_redis
from app.core.predictor import load_model
from app.routes.eta import router as eta_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s")

# ─── Shared Kafka Producer ────────────────────────────────────
kafka_producer: KafkaProducer | None = None


def _create_kafka_producer() -> KafkaProducer | None:
    """Create a Kafka producer for ETA update events."""
    bootstrap = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    try:
        producer = KafkaProducer(
            bootstrap_servers=bootstrap.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            retries=3,
            linger_ms=10,
            acks="all",
        )
        logger.info("Kafka producer connected: %s", bootstrap)
        return producer
    except Exception as exc:
        logger.warning("Kafka producer init failed (non-fatal): %s", exc)
        return None


def emit_eta_event(payload: dict):
    """Emit an eta.updated event to Kafka.

    Payload includes: order_id, eta_seconds, confidence, risk_level,
    delay_probability, model_version, recalculated flag.
    """
    if kafka_producer is None:
        logger.debug("Kafka producer unavailable, skipping eta.updated event")
        return
    try:
        kafka_producer.send("eta.updated", payload)
        kafka_producer.flush(timeout=5)
        logger.info("Emitted eta.updated for order %s", payload.get("order_id"))
    except Exception as exc:
        logger.warning("Failed to emit eta.updated event: %s", exc)


# ─── FastAPI Lifespan ────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global kafka_producer

    logger.info("ETAService starting — ML Prediction Engine V2")
    await init_redis()
    load_model()

    # Initialize Kafka producer for eta.updated events
    kafka_producer = _create_kafka_producer()

    yield

    # Shutdown
    if kafka_producer:
        try:
            kafka_producer.flush()
            kafka_producer.close()
        except Exception:
            pass
    await close_redis()
    logger.info("ETAService shutting down …")


app = FastAPI(title="HotSot ETA Service", version="2.0.0", lifespan=lifespan)
app.include_router(eta_router, prefix="/eta", tags=["eta"])


@app.get("/health")
async def health():
    return {
        "service": "eta-service",
        "version": "2.0.0",
        "status": "healthy",
        "kafka_connected": kafka_producer is not None,
    }
