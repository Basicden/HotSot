"""HotSot Realtime Service — WebSocket Layer for Live Updates.

V2: Includes Redis pub/sub for cross-instance broadcasting and Kafka
consumer for automatic event propagation to connected WebSocket clients.
When a Kafka event arrives (order state change, ETA update, arrival
detection), the service pushes it to all relevant WebSocket subscribers.
"""

import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from kafka import KafkaConsumer
import redis.asyncio as aioredis

from app.routes.websocket import router as ws_router, active_connections, kitchen_connections
from app.routes.broadcast import router as broadcast_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s")

# ─── Redis Client for Pub/Sub ─────────────────────────────────
redis_client: aioredis.Redis | None = None

# ─── Kafka Consumer (background thread) ──────────────────────
_consumer_thread: threading.Thread | None = None
_consumer_running = False

# Topics the realtime service listens to
KAFKA_TOPICS = [
    "order.state_changed",
    "eta.updated",
    "order.arrived",
    "shelf.expired",
    "shelf.assigned",
]

# Reference to the running FastAPI event loop — needed by the
# Kafka consumer thread to schedule async broadcasts.
_main_loop: asyncio.AbstractEventLoop | None = None


def _kafka_consumer_loop():
    """Background thread: consume Kafka events and push to WebSocket clients.

    Subscribes to order lifecycle, ETA, arrival, and shelf events.
    For each event, identifies the target WebSocket connections (per-order
    or per-kitchen) and broadcasts the update.
    """
    global _consumer_running
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"))
    try:
        consumer = KafkaConsumer(
            *KAFKA_TOPICS,
            bootstrap_servers=bootstrap.split(","),
            group_id="realtime-service-events",
            auto_offset_reset="latest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            enable_auto_commit=True,
            consumer_timeout_ms=1000,
        )
        logger.info("Kafka consumer started: listening on %s", KAFKA_TOPICS)
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
                event_type = event.get("event_type", msg.topic)

                # Schedule broadcast on the main event loop
                if _main_loop and not _main_loop.is_closed():
                    if order_id:
                        _main_loop.call_soon_threadsafe(
                            lambda oid=order_id, et=event_type, p=event:
                                asyncio.ensure_future(_broadcast_to_order(oid, et, p), loop=_main_loop)
                        )
                    if kitchen_id:
                        _main_loop.call_soon_threadsafe(
                            lambda kid=kitchen_id, et=event_type, p=event:
                                asyncio.ensure_future(_broadcast_to_kitchen(kid, et, p), loop=_main_loop)
                        )
        except StopIteration:
            pass
        except Exception as exc:
            logger.error("Kafka consumer loop error: %s", exc)

    try:
        consumer.close()
    except Exception:
        pass
    logger.info("Kafka consumer stopped")


async def _broadcast_to_order(order_id: str, event_type: str, payload: dict):
    """Push an event to all WebSocket connections subscribed to an order."""
    connections = active_connections.get(order_id, set())
    if not connections:
        return
    message = {"type": event_type, "order_id": order_id, "payload": payload}
    disconnected = set()
    for ws in connections:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        connections.discard(ws)


async def _broadcast_to_kitchen(kitchen_id: str, event_type: str, payload: dict):
    """Push an event to all kitchen dashboard WebSocket connections."""
    connections = kitchen_connections.get(kitchen_id, set())
    if not connections:
        return
    message = {"type": event_type, "kitchen_id": kitchen_id, "payload": payload}
    disconnected = set()
    for ws in connections:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        connections.discard(ws)


# ─── Redis Pub/Sub for Cross-Instance Broadcasting ────────────
_pubsub_task: asyncio.Task | None = None


async def _redis_pubsub_listener():
    """Listen to Redis pub/sub channels for cross-instance broadcasts.

    In a multi-pod deployment, WebSocket connections are distributed
    across instances. Redis pub/sub ensures that events from one
    instance reach subscribers on all other instances.
    """
    if redis_client is None:
        return
    try:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("hotsot:events")
        logger.info("Redis pub/sub subscribed to hotsot:events")
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                order_id = data.get("order_id")
                kitchen_id = data.get("kitchen_id")
                event_type = data.get("event_type", "UNKNOWN")
                if order_id:
                    await _broadcast_to_order(order_id, event_type, data)
                if kitchen_id:
                    await _broadcast_to_kitchen(kitchen_id, event_type, data)
            except Exception as exc:
                logger.error("Redis pub/sub message error: %s", exc)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("Redis pub/sub listener error: %s", exc)


# ─── FastAPI Lifespan ────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, _consumer_thread, _consumer_running, _main_loop, _pubsub_task

    _main_loop = asyncio.get_running_loop()
    logger.info("RealtimeService starting — WebSocket + Kafka + Redis pub/sub")

    # Initialize Redis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/4")
    try:
        redis_client = aioredis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connected: %s", redis_url)
    except Exception as exc:
        logger.warning("Redis connection failed (non-fatal): %s", exc)
        redis_client = None

    # Start Redis pub/sub listener
    if redis_client:
        _pubsub_task = asyncio.create_task(_redis_pubsub_listener())

    # Start Kafka consumer in background thread
    _consumer_running = True
    _consumer_thread = threading.Thread(target=_kafka_consumer_loop, daemon=True)
    _consumer_thread.start()

    yield

    # Shutdown
    _consumer_running = False
    if _pubsub_task:
        _pubsub_task.cancel()
    if _consumer_thread:
        _consumer_thread.join(timeout=5)
    if redis_client:
        try:
            await redis_client.close()
        except Exception:
            pass
    # Close all WebSocket connections
    for order_id, connections in list(active_connections.items()):
        for ws in connections:
            try:
                await ws.close()
            except Exception:
                pass
    for kitchen_id, connections in list(kitchen_connections.items()):
        for ws in connections:
            try:
                await ws.close()
            except Exception:
                pass
    logger.info("RealtimeService shut down")


app = FastAPI(title="HotSot Realtime Service", version="2.0.0", lifespan=lifespan)

# Include routes
app.include_router(ws_router)
app.include_router(broadcast_router)


@app.get("/health")
async def health():
    return {
        "service": "realtime-service",
        "version": "2.0.0",
        "status": "healthy",
        "redis_connected": redis_client is not None,
        "kafka_consumer_running": _consumer_running,
        "active_order_connections": sum(len(v) for v in active_connections.values()),
        "active_kitchen_connections": sum(len(v) for v in kitchen_connections.values()),
    }
