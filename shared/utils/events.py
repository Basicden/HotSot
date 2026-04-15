"""
HotSot Event Emitter — LEGACY (Deprecated).

This module is kept for backward compatibility only.
New code should use shared.utils.kafka_client.KafkaProducer instead.

Migration guide:
    - EventPublisher()       → KafkaProducer(service_name="order")
    - event_publisher.publish(event) → producer.publish_event(envelope)
    - V1 OrderEvent           → V2 EventEnvelope

The new KafkaProducer provides:
    - Exactly-once semantics (idempotent producer)
    - Proper partition key strategy
    - DLQ routing for failed messages
    - Fallback logging when Kafka is down
    - Schema version validation

This file will be removed in v3.0.0.
"""

import json
import logging
import warnings
from datetime import datetime, timezone
from uuid import UUID

from shared.types.models import EventType, OrderEvent

warnings.warn(
    "shared.utils.events is deprecated. Use shared.utils.kafka_client.KafkaProducer instead. "
    "This module will be removed in v3.0.0.",
    DeprecationWarning,
    stacklevel=2,
)

logger = logging.getLogger(__name__)


class EventPublisher:
    """
    Publishes domain events to Kafka topics. DEPRECATED.

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
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
                self._log_to_fallback(topic, message, error=str(e))
        else:
            logger.info(f"[DEV] Event: {topic} → {json.dumps(message, indent=2)}")

    @staticmethod
    def _log_to_fallback(topic: str, message: dict, error: str) -> None:
        """Fallback logging when Kafka is unavailable."""
        fallback_path = "/tmp/hotsot_event_fallback.log"
        with open(fallback_path, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} | {topic} | ERROR: {error} | {json.dumps(message)}\n")


# Singleton
event_publisher = EventPublisher()
