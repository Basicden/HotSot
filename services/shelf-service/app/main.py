"""HotSot Shelf Service — Physical Shelf Allocation + TTL Enforcement.

V2: Includes Kafka producer for shelf lifecycle events (assigned, released,
expired) and Kafka consumer for order.ready events to auto-assign shelves.
"""

import json
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from kafka import KafkaProducer, KafkaConsumer

from app.core.redis_client import init_redis, close_redis
from app.core.ttl_engine import ttl_engine
from app.routes.shelf import router as shelf_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s")

# ─── Shared Kafka Producer ────────────────────────────────────
kafka_producer: KafkaProducer | None = None


def _create_kafka_producer() -> KafkaProducer | None:
    """Create a Kafka producer for shelf event emission."""
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


def emit_shelf_event(event_type: str, payload: dict):
    """Emit a shelf lifecycle event to Kafka.

    Topics:
      - shelf.assigned  — order placed on shelf
      - shelf.released  — shelf slot freed
      - shelf.expired   — TTL exceeded, order expired
    """
    if kafka_producer is None:
        logger.debug("Kafka producer unavailable, skipping event: %s", event_type)
        return
    topic = f"shelf.{event_type}"
    try:
        kafka_producer.send(topic, payload)
        kafka_producer.flush(timeout=5)
        logger.info("Emitted %s event for order %s", event_type, payload.get("order_id"))
    except Exception as exc:
        logger.warning("Failed to emit %s event: %s", event_type, exc)


# ─── Kafka Consumer (background thread) ──────────────────────
_consumer_thread: threading.Thread | None = None
_consumer_running = False


def _kafka_consumer_loop():
    """Background thread: consume order.ready events and auto-assign shelves.

    When an order becomes READY, the shelf service listens for the
    `order.ready` Kafka event and attempts to find an available shelf
    slot for the order's kitchen, then emits `shelf.assigned` on success.
    """
    global _consumer_running
    bootstrap = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    try:
        consumer = KafkaConsumer(
            "order.ready",
            bootstrap_servers=bootstrap.split(","),
            group_id="shelf-service-order-ready",
            auto_offset_reset="latest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            enable_auto_commit=True,
            consumer_timeout_ms=1000,
        )
        logger.info("Kafka consumer started: listening on order.ready")
    except Exception as exc:
        logger.warning("Kafka consumer init failed (non-fatal): %s", exc)
        return

    while _consumer_running:
        try:
            for msg in consumer:
                if not _consumer_running:
                    break
                event = msg.value
                order_id = event.get("order_id")
                kitchen_id = event.get("kitchen_id")
                if not order_id or not kitchen_id:
                    continue
                # Attempt to find and assign a shelf slot
                shelf_id = ttl_engine.find_available(kitchen_id)
                if shelf_id:
                    success = ttl_engine.assign_order(shelf_id, order_id)
                    if success:
                        emit_shelf_event("assigned", {
                            "shelf_id": shelf_id,
                            "order_id": order_id,
                            "kitchen_id": kitchen_id,
                            "zone": ttl_engine.shelves[shelf_id].temperature_zone,
                            "ttl_seconds": ttl_engine.shelves[shelf_id].ttl_seconds,
                        })
                        logger.info("Auto-assigned shelf %s for order %s", shelf_id, order_id)
                    else:
                        logger.warning("Shelf assign failed for order %s", order_id)
                else:
                    logger.warning("No shelf available for order %s at kitchen %s", order_id, kitchen_id)
        except StopIteration:
            pass
        except Exception as exc:
            logger.error("Consumer loop error: %s", exc)

    try:
        consumer.close()
    except Exception:
        pass
    logger.info("Kafka consumer stopped")


# ─── FastAPI Lifespan ────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global kafka_producer, _consumer_thread, _consumer_running

    logger.info("ShelfService starting …")
    await init_redis()

    # Initialize Kafka producer
    kafka_producer = _create_kafka_producer()

    # Start Kafka consumer in background thread
    _consumer_running = True
    _consumer_thread = threading.Thread(target=_kafka_consumer_loop, daemon=True)
    _consumer_thread.start()

    yield

    # Shutdown
    _consumer_running = False
    if _consumer_thread:
        _consumer_thread.join(timeout=5)
    if kafka_producer:
        try:
            kafka_producer.flush()
            kafka_producer.close()
        except Exception:
            pass
    await close_redis()
    logger.info("ShelfService shutting down …")


app = FastAPI(title="HotSot Shelf Service", version="2.0.0", lifespan=lifespan)
app.include_router(shelf_router, prefix="/shelf", tags=["shelf"])


@app.get("/health")
async def health():
    return {
        "service": "shelf-service",
        "version": "2.0.0",
        "status": "healthy",
        "kafka_connected": kafka_producer is not None,
        "consumer_running": _consumer_running,
    }
