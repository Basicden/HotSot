"""
HotSot Event Emitter — Kafka-based event publishing.
"""

import json
import logging
from datetime import datetime
from uuid import UUID

from shared.types.models import EventType, OrderEvent

logger = logging.getLogger(__name__)


class EventPublisher:
    """
    Publishes domain events to Kafka topics.
    
    Topic naming convention: hotsot.<domain>.<event_type>
    Example: hotsot.orders.ORDER_CREATED
    """

    def __init__(self, kafka_producer=None):
        self._producer = kafka_producer

    async def publish(self, event: OrderEvent) -> None:
        """Publish an event to the appropriate Kafka topic."""
        topic = f"hotsot.orders.{event.event_type.value}"

        message = {
            "event_id": str(event.id),
            "order_id": str(event.order_id),
            "event_type": event.event_type.value,
            "payload": event.payload,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if self._producer:
            try:
                await self._producer.send_and_wait(
                    topic,
                    key=str(event.order_id).encode(),
                    value=json.dumps(message).encode(),
                )
                logger.info(
                    f"Event published: {event.event_type.value} for order {event.order_id}"
                )
            except Exception as e:
                logger.error(f"Failed to publish event: {e}")
                # Fallback: log to file for recovery
                self._log_to_fallback(topic, message, error=str(e))
        else:
            # Development mode — just log
            logger.info(f"[DEV] Event: {topic} → {json.dumps(message, indent=2)}")

    @staticmethod
    def _log_to_fallback(topic: str, message: dict, error: str) -> None:
        """Fallback logging when Kafka is unavailable."""
        fallback_path = "/tmp/hotsot_event_fallback.log"
        with open(fallback_path, "a") as f:
            f.write(f"{datetime.utcnow().isoformat()} | {topic} | ERROR: {error} | {json.dumps(message)}\n")


# Singleton
event_publisher = EventPublisher()
