"""HotSot ML Service — Model Training + Inference + Feature Store.

V2: Includes Kafka consumer for ML feedback loop. Subscribes to
order.picked and shelf.expired events to record actual vs predicted
ETA for model retraining.
"""

import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
import redis as redis_lib
from kafka import KafkaConsumer

from app.routes.ml import router as ml_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s")

redis_client: redis_lib.Redis | None = None

# ─── Kafka Consumer for Feedback Loop ────────────────────────
_consumer_thread: threading.Thread | None = None
_consumer_running = False

# Feedback events that improve ETA model accuracy
FEEDBACK_TOPICS = [
    "order.picked",
    "shelf.expired",
]


def _kafka_consumer_loop():
    """Background thread: consume feedback events for ML model improvement.

    - order.picked: Records actual prep time (IN_PREP → PICKED) vs predicted
      ETA. This is the primary positive training signal.
    - shelf.expired: Records overestimation error (order was ready but not
      picked within TTL). This negative signal is weighted 3x in training.
    """
    global _consumer_running
    bootstrap = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    try:
        consumer = KafkaConsumer(
            *FEEDBACK_TOPICS,
            bootstrap_servers=bootstrap.split(","),
            group_id="ml-service-feedback",
            auto_offset_reset="latest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            enable_auto_commit=True,
            consumer_timeout_ms=1000,
        )
        logger.info("ML Kafka consumer started: listening on %s", FEEDBACK_TOPICS)
    except Exception as exc:
        logger.warning("ML Kafka consumer init failed (non-fatal): %s", exc)
        return

    while _consumer_running:
        try:
            for msg in consumer:
                if not _consumer_running:
                    break
                event = msg.value
                order_id = event.get("order_id")
                kitchen_id = event.get("kitchen_id")
                if not order_id:
                    continue

                if msg.topic == "order.picked":
                    _handle_order_picked(event)
                elif msg.topic == "shelf.expired":
                    _handle_shelf_expired(event)

        except StopIteration:
            pass
        except Exception as exc:
            logger.error("ML consumer loop error: %s", exc)

    try:
        consumer.close()
    except Exception:
        pass
    logger.info("ML Kafka consumer stopped")


def _handle_order_picked(event: dict):
    """Process order.picked — record actual vs predicted ETA."""
    order_id = event.get("order_id")
    kitchen_id = event.get("kitchen_id")

    # Compute actual prep duration
    prep_started = event.get("prep_started_at")
    picked_at = event.get("picked_at")
    predicted_eta = event.get("eta_seconds")

    if prep_started and picked_at:
        try:
            # Parse ISO timestamps and compute actual duration
            start = datetime.fromisoformat(prep_started.replace("Z", "+00:00"))
            end = datetime.fromisoformat(picked_at.replace("Z", "+00:00"))
            actual_seconds = int((end - start).total_seconds())
            error_seconds = abs(actual_seconds - (predicted_eta or 0))

            logger.info(
                "ML Feedback: order=%s predicted=%ds actual=%ds error=%ds",
                order_id, predicted_eta, actual_seconds, error_seconds,
            )

            # Store in Redis for next training run
            if redis_client:
                redis_client.lpush("ml:feedback:positive", json.dumps({
                    "order_id": order_id,
                    "kitchen_id": kitchen_id,
                    "predicted_seconds": predicted_eta,
                    "actual_seconds": actual_seconds,
                    "error_seconds": error_seconds,
                    "weight": 1,
                    "timestamp": datetime.utcnow().isoformat(),
                }))
        except Exception as exc:
            logger.warning("Failed to process order.picked feedback: %s", exc)


def _handle_shelf_expired(event: dict):
    """Process shelf.expired — record overestimation (negative signal, 3x weight)."""
    order_id = event.get("order_id")
    kitchen_id = event.get("kitchen_id")

    logger.info(
        "ML Feedback (negative): order=%s expired on shelf — ETA was too long",
        order_id,
    )

    # Store as negative feedback with 3x weight
    if redis_client:
        try:
            redis_client.lpush("ml:feedback:negative", json.dumps({
                "order_id": order_id,
                "kitchen_id": kitchen_id,
                "reason": "shelf_expired",
                "weight": 3,  # 3x weight in training
                "timestamp": datetime.utcnow().isoformat(),
            }))
        except Exception as exc:
            logger.warning("Failed to store shelf.expired feedback: %s", exc)


# ─── FastAPI Lifespan ────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, _consumer_thread, _consumer_running

    logger.info("MLService starting — Training + Inference + Feedback Loop ready")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/5")
    try:
        redis_client = redis_lib.from_url(redis_url, decode_responses=True)
        redis_client.ping()
        logger.info("Redis connected: %s", redis_url)
    except Exception as exc:
        logger.warning("Redis connection failed (non-fatal): %s", exc)
        redis_client = None

    # Start Kafka consumer for feedback loop
    _consumer_running = True
    _consumer_thread = threading.Thread(target=_kafka_consumer_loop, daemon=True)
    _consumer_thread.start()

    yield

    # Shutdown
    _consumer_running = False
    if _consumer_thread:
        _consumer_thread.join(timeout=5)
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
        "kafka_consumer_running": _consumer_running,
    }
