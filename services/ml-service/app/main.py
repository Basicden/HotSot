"""HotSot ML Service — Model Training + Inference + Feature Store.

V2: Includes Kafka consumer for ML feedback loop. Subscribes to
order.picked and shelf.expired events to record actual vs predicted
ETA for model retraining.
"""

import json
import logging
import os
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
import redis as redis_lib
from aiokafka import AIOKafkaConsumer

from app.routes.ml import router as ml_router
from shared.auth.jwt import setup_token_revocation
from shared.utils.observability import setup_metrics
from shared.utils.redis_client import RedisClient as SharedRedisClient
from shared.types.schemas import EventType

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s")

redis_client: redis_lib.Redis | None = None

# ─── Kafka Consumer for Feedback Loop ────────────────────────
_consumer_task: asyncio.Task | None = None
_consumer_running = False

# Feedback events that improve ETA model accuracy
# V2: Subscribe to standardized domain topics and filter by event_type
FEEDBACK_TOPICS = [
    "hotsot.order.events.v1",   # filter: ORDER_PICKED
    "hotsot.shelf.events.v1",   # filter: SHELF_EXPIRED
]


async def _kafka_consumer_loop():
    """Async Kafka consumer for ML model feedback loop.

    - order.picked: Records actual prep time (IN_PREP → PICKED) vs predicted
      ETA. This is the primary positive training signal.
    - shelf.expired: Records overestimation error (order was ready but not
      picked within TTL). This negative signal is weighted 3x in training.
    """
    global _consumer_running
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"))
    try:
        consumer = AIOKafkaConsumer(
            *FEEDBACK_TOPICS,
            bootstrap_servers=bootstrap.split(","),
            group_id="ml-service-feedback",
            auto_offset_reset="latest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            enable_auto_commit=True,
        )
        await consumer.start()
        logger.info("ML Kafka consumer started: listening on %s", FEEDBACK_TOPICS)
    except Exception as exc:
        logger.warning("ML Kafka consumer init failed (non-fatal): %s", exc)
        return

    try:
        async for msg in consumer:
            if not _consumer_running:
                break
            event = msg.value
            order_id = event.get("order_id")
            kitchen_id = event.get("kitchen_id")
            if not order_id:
                continue

            # V2: Filter by event_type instead of topic name
            event_type = event.get("event_type", "")
            if event_type == EventType.ORDER_PICKED.value:
                _handle_order_picked(event)
            elif event_type == EventType.SHELF_EXPIRED.value:
                _handle_shelf_expired(event)
    except Exception as exc:
        logger.error("ML consumer loop error: %s", exc)
    finally:
        try:
            await consumer.stop()
        except Exception as e:
            logger.warning(f"Error during shutdown: {e}")
    logger.info("ML Kafka consumer stopped")


def _handle_order_picked(event: dict):
    """Process order.picked — record actual vs predicted ETA.

    Fail-CLOSED: When Redis is unavailable, feedback data is NOT silently
    lost. An error is logged at ERROR level to ensure the feedback gap
    is visible in monitoring and alerting.
    """
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

            # Store in Redis for next training run (fail-closed)
            if redis_client is None:
                logger.error(
                    "ML Feedback LOST: Redis unavailable — cannot store positive feedback for order=%s",
                    order_id,
                )
                return
            try:
                redis_client.lpush("ml:feedback:positive", json.dumps({
                    "order_id": order_id,
                    "kitchen_id": kitchen_id,
                    "predicted_seconds": predicted_eta,
                    "actual_seconds": actual_seconds,
                    "error_seconds": error_seconds,
                    "weight": 1,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
            except redis_lib.RedisError as exc:
                logger.error(
                    "ML Feedback LOST: Redis error — cannot store positive feedback for order=%s: %s",
                    order_id, exc,
                )
        except Exception as exc:
            logger.warning("Failed to process order.picked feedback: %s", exc)


def _handle_shelf_expired(event: dict):
    """Process shelf.expired — record overestimation (negative signal, 3x weight).

    Fail-CLOSED: When Redis is unavailable, feedback data is NOT silently
    lost. An error is logged at ERROR level to ensure the feedback gap
    is visible in monitoring and alerting.
    """
    order_id = event.get("order_id")
    kitchen_id = event.get("kitchen_id")

    logger.info(
        "ML Feedback (negative): order=%s expired on shelf — ETA was too long",
        order_id,
    )

    # Store as negative feedback with 3x weight (fail-closed)
    if redis_client is None:
        logger.error(
            "ML Feedback LOST: Redis unavailable — cannot store negative feedback for order=%s",
            order_id,
        )
        return
    try:
        redis_client.lpush("ml:feedback:negative", json.dumps({
            "order_id": order_id,
            "kitchen_id": kitchen_id,
            "reason": "shelf_expired",
            "weight": 3,  # 3x weight in training
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }))
    except redis_lib.RedisError as exc:
        logger.error(
            "ML Feedback LOST: Redis error — cannot store negative feedback for order=%s: %s",
            order_id, exc,
        )


# ─── FastAPI Lifespan ────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, _consumer_task, _consumer_running

    logger.info("MLService starting — Training + Inference + Feedback Loop ready")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/5")
    try:
        redis_client = redis_lib.from_url(redis_url, decode_responses=True)
        redis_client.ping()
        logger.info("Redis connected: %s", redis_url)
    except Exception as exc:
        # Fail-CLOSED: Redis is REQUIRED for ML feedback storage.
        # Running without Redis means feedback data WILL be silently lost.
        logger.error(
            "Redis connection FAILED — ML feedback storage unavailable: %s. "
            "Feedback data will be LOST until Redis is restored.",
            exc,
        )
        redis_client = None

    # Initialize shared RedisClient for JWT token revocation
    jwt_redis_client = SharedRedisClient(service_name="ml")
    try:
        await jwt_redis_client.connect()
        setup_token_revocation(jwt_redis_client)
        logger.info("JWT token revocation Redis connected")
    except Exception as exc:
        logger.error("JWT token revocation Redis connection failed: %s", exc)

    # Start Kafka consumer for feedback loop (async)
    _consumer_running = True
    _consumer_task = asyncio.create_task(_kafka_consumer_loop())

    yield

    # Shutdown
    _consumer_running = False
    if _consumer_task and not _consumer_task.done():
        _consumer_task.cancel()
        try:
            await asyncio.wait_for(_consumer_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    if redis_client:
        try:
            redis_client.close()
        except Exception as e:
            logger.warning(f"Error during shutdown: {e}")
    logger.info("MLService shutting down …")


app = FastAPI(title="HotSot ML Service", version="2.0.0", lifespan=lifespan)
setup_metrics(app, "ml-service")
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
