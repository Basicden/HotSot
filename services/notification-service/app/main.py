"""HotSot Notification Service — Multi-channel Notification Dispatcher.

V2: Includes Kafka consumer for auto-triggering notifications on order
lifecycle events. Subscribes to order.state_changed and eta.updated
topics and dispatches notifications via the configured channel priority.
"""

import json
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from kafka import KafkaProducer, KafkaConsumer

from app.routes.notifications import router as notif_router
from app.core.dispatcher import NotificationDispatcher
from app.core.channel_router import ChannelRouter
from app.core.templates import TemplateEngine

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(name)s  │  %(message)s")

# ─── Shared Components ───────────────────────────────────────
kafka_producer: KafkaProducer | None = None
dispatcher = NotificationDispatcher()
channel_router = ChannelRouter()
template_engine = TemplateEngine()

# ─── Kafka Consumer (background thread) ──────────────────────
_consumer_thread: threading.Thread | None = None
_consumer_running = False

# Events that trigger notifications
NOTIFICATION_TOPICS = [
    "order.state_changed",
    "eta.updated",
    "order.arrived",
    "shelf.expired",
]

# Map event types to notification template IDs
EVENT_TEMPLATE_MAP = {
    "PAYMENT_CONFIRMED": "payment.confirmed",
    "IN_PREP": "order.prep_started",
    "READY": "order.ready",
    "ON_SHELF": "order.on_shelf",
    "ARRIVED": "order.arrived",
    "PICKED": "order.picked",
    "EXPIRED": "order.expired",
    "REFUNDED": "order.refunded",
    "CANCELLED": "order.cancelled",
    "eta.updated": "eta.update",
}


def _kafka_consumer_loop():
    """Background thread: consume order events and auto-dispatch notifications.

    For each state change event, looks up the appropriate notification
    template and dispatches via the channel priority list.
    """
    global _consumer_running
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    try:
        consumer = KafkaConsumer(
            *NOTIFICATION_TOPICS,
            bootstrap_servers=bootstrap.split(","),
            group_id="notification-service-events",
            auto_offset_reset="latest",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            enable_auto_commit=True,
            consumer_timeout_ms=1000,
        )
        logger.info("Kafka consumer started: listening on %s", NOTIFICATION_TOPICS)
    except Exception as exc:
        logger.warning("Kafka consumer init failed (non-fatal): %s", exc)
        return

    while _consumer_running:
        try:
            for msg in consumer:
                if not _consumer_running:
                    break
                event = msg.value
                event_type = event.get("event_type") or event.get("to_state") or msg.topic
                order_id = event.get("order_id")
                user_id = event.get("user_id")
                if not order_id:
                    continue

                template_id = EVENT_TEMPLATE_MAP.get(event_type)
                if not template_id:
                    logger.debug("No notification template for event: %s", event_type)
                    continue

                # Render notification message
                message = template_engine.render(template_id, event)

                # Get channel priority for this event
                channels = channel_router.get_channels_for_event(template_id)

                logger.info("Auto-notification: order=%s event=%s template=%s channels=%s",
                            order_id, event_type, template_id, channels)

                # Dispatch (in production this would be async)
                try:
                    dispatcher.dispatch(
                        user_id=user_id or "unknown",
                        order_id=order_id,
                        template_id=template_id,
                        channels=channels,
                        message=message,
                    )
                except Exception as exc:
                    logger.warning("Auto-notification dispatch failed: %s", exc)

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

    logger.info("NotificationService starting …")
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

    # Kafka Producer
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

    # Start Kafka consumer for auto-notification
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
        "consumer_running": _consumer_running,
    }
