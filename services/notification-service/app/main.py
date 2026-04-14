"""HotSot Notification Service — Multi-channel Notification Dispatcher."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from kafka import KafkaProducer
import json
import os

from app.routes.notifications import router as notif_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s")

# Shared Kafka producer for notification event emission
kafka_producer: KafkaProducer | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global kafka_producer
    logger.info("NotificationService starting …")
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    try:
        kafka_producer = KafkaProducer(
            bootstrap_servers=bootstrap.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            retries=3,
            linger_ms=10,
        )
        logger.info("Kafka producer connected: %s", bootstrap)
    except Exception as exc:
        logger.warning("Kafka producer failed (non-fatal): %s", exc)
        kafka_producer = None
    yield
    if kafka_producer:
        try:
            kafka_producer.flush()
            kafka_producer.close()
        except Exception:
            pass
    logger.info("NotificationService shutting down …")


app = FastAPI(title="HotSot Notification Service", version="2.0.0", lifespan=lifespan)
app.include_router(notif_router, prefix="/notifications", tags=["notifications"])


@app.get("/health")
async def health():
    return {
        "service": "notification-service",
        "version": "2.0.0",
        "status": "healthy",
        "kafka_connected": kafka_producer is not None,
    }
