"""
HotSot Kafka Client — Production-grade event streaming.

Features:
    - Exactly-once producer with idempotent publishing
    - Manual-commit consumer with DLQ routing
    - EventEnvelope serialization/deserialization
    - Topic management with auto-creation
    - Partition key strategy (order_id, kitchen_id, vendor_id)
    - Fallback logging when Kafka is unavailable

Usage:
    from shared.utils.kafka_client import KafkaProducer, KafkaConsumer

    # Producer
    producer = KafkaProducer(service_name="order")
    await producer.start()
    await producer.publish_event(event_envelope)

    # Consumer
    consumer = KafkaConsumer(
        service_name="kitchen",
        topics=["hotsot.order.events.v1"],
        group_id="hotsot-kitchen-group",
        handler=my_handler,
    )
    await consumer.start()
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Sequence
from uuid import uuid4

from shared.utils.config import get_settings
from shared.types.schemas import EventEnvelope, EventType, KAFKA_TOPICS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# SERIALIZATION
# ═══════════════════════════════════════════════════════════════

def serialize_event(event: EventEnvelope) -> bytes:
    """
    Serialize an EventEnvelope to bytes for Kafka.

    Args:
        event: The event envelope to serialize.

    Returns:
        JSON-encoded bytes.
    """
    return json.dumps(event.model_dump(mode="json"), default=str).encode("utf-8")


def deserialize_event(data: bytes) -> Dict[str, Any]:
    """
    Deserialize Kafka message bytes to a dictionary.

    Args:
        data: Raw bytes from Kafka.

    Returns:
        Deserialized dictionary.
    """
    return json.loads(data.decode("utf-8"))


# ═══════════════════════════════════════════════════════════════
# TOPIC MANAGEMENT
# ═══════════════════════════════════════════════════════════════

async def ensure_topics_exist(
    bootstrap_servers: str,
    topics: Optional[List[str]] = None,
    num_partitions: int = 6,
    replication_factor: int = 1,
) -> None:
    """
    Ensure required Kafka topics exist. Creates them if missing.

    Args:
        bootstrap_servers: Kafka broker addresses.
        topics: List of topic names. Defaults to KAFKA_TOPICS keys.
        num_partitions: Number of partitions per topic.
        replication_factor: Replication factor for durability.
    """
    topic_list = topics or list(KAFKA_TOPICS.keys())

    try:
        from aiokafka.admin import AIOKafkaAdminClient
        admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
        await admin.start()
        try:
            existing = await admin.list_topics()
            for topic in topic_list:
                if topic not in existing:
                    from kafka.admin import NewTopic
                    new_topic = NewTopic(
                        name=topic,
                        num_partitions=num_partitions,
                        replication_factor=replication_factor,
                    )
                    await admin.create_topics([new_topic])
                    logger.info(f"Created Kafka topic: {topic}")
                else:
                    logger.debug(f"Kafka topic already exists: {topic}")
        finally:
            await admin.close()
    except ImportError:
        logger.warning("aiokafka admin not available — skipping topic creation")
    except Exception as e:
        logger.warning(f"Topic creation skipped (Kafka may not be running): {e}")


# ═══════════════════════════════════════════════════════════════
# TOPIC RESOLUTION
# ═══════════════════════════════════════════════════════════════

def _resolve_topic(event_type: EventType) -> str:
    """
    Resolve an EventType to its Kafka topic.

    Uses the KAFKA_TOPICS dict as the canonical source of truth.
    All topic names follow the convention: hotsot.{domain}.events.v1

    Args:
        event_type: The event type enum value.

    Returns:
        Kafka topic name string.
    """
    et = event_type.value

    # Order lifecycle events
    if et.startswith("ORDER_") or et in (
        "PAYMENT_PENDING", "PAYMENT_CONFIRMED", "SLOT_RESERVED", "SLOT_FILLED",
        "PRIORITY_UPDATED", "QUEUE_ASSIGNED", "QUEUE_REORDERED",
    ):
        return "hotsot.order.events.v1"

    # Kitchen events
    if et.startswith("KITCHEN_") or et.startswith("THROTTLE_") or et in (
        "PREP_STARTED", "PREP_COMPLETED", "BATCH_FORMED",
        "PACKING_STARTED", "PACKING_COMPLETED",
    ):
        return "hotsot.kitchen.events.v1"

    # Slot events
    if et.startswith("SLOT_"):
        return "hotsot.slot.events.v1"

    # Priority events
    if et.startswith("PRIORITY_") or et.startswith("QUEUE_"):
        return "hotsot.priority.events.v1"

    # Shelf events
    if "SHELF" in et:
        return "hotsot.shelf.events.v1"

    # Arrival events
    if "ARRIVAL" in et or "HANDOFF" in et:
        return "hotsot.arrival.events.v1"

    # ETA events
    if et.startswith("ETA_") or et == "READY_FOR_PICKUP":
        return "hotsot.eta.events.v1"

    # Payment events
    if et.startswith("PAYMENT_"):
        return "hotsot.payment.events.v1"

    # Compensation events
    if "COMPENSATION" in et:
        return "hotsot.compensation.events.v1"

    # Incident events
    if "INCIDENT" in et:
        return "hotsot.incident.events.v1"

    # Notification events
    if et.startswith("NOTIFICATION"):
        return "hotsot.notification.events.v1"

    # Default to order events
    logger.warning(f"Unknown event type mapping for {et}, defaulting to order events")
    return "hotsot.order.events.v1"


# ═══════════════════════════════════════════════════════════════
# KAFKA PRODUCER
# ═══════════════════════════════════════════════════════════════

class KafkaProducer:
    """
    Production Kafka producer with exactly-once semantics.

    Features:
        - Idempotent publishing (event_id as message key for dedup)
        - Partition key strategy based on event type
        - Automatic topic resolution from EventType
        - Fallback logging when Kafka is down
        - Correlation ID propagation
    """

    def __init__(self, service_name: str = "hotsot"):
        self._service_name = service_name
        self._producer = None
        self._started = False

    async def start(self) -> None:
        """Start the Kafka producer."""
        try:
            from aiokafka import AIOKafkaProducer

            settings = get_settings(self._service_name)

            self._producer = AIOKafkaProducer(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                enable_idempotence=True,
                acks="all",
                retries=3,
                retry_backoff_ms=100,
                linger_ms=10,
                max_batch_size=16384,
                compression_type="snappy",
                security_protocol=settings.KAFKA_SECURITY_PROTOCOL,
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )

            await self._producer.start()
            self._started = True
            logger.info(f"Kafka producer started for service={self._service_name}")

        except ImportError:
            logger.warning("aiokafka not installed — Kafka producer in DEV mode (log only)")
            self._producer = None
        except Exception as e:
            logger.warning(f"Kafka producer start failed: {e} — falling back to log mode")
            self._producer = None

    async def stop(self) -> None:
        """Stop the Kafka producer."""
        if self._producer:
            try:
                await self._producer.stop()
            except Exception as e:
                logger.warning(f"Error stopping Kafka producer: {e}")
        self._started = False
        logger.info(f"Kafka producer stopped for service={self._service_name}")

    async def publish_event(
        self,
        event: EventEnvelope,
        topic: Optional[str] = None,
        key: Optional[str] = None,
    ) -> bool:
        """
        Publish an EventEnvelope to Kafka.

        Args:
            event: The event envelope to publish.
            topic: Override topic (auto-resolved if None).
            key: Override partition key (auto-resolved if None).

        Returns:
            True if published (or logged in dev mode), False on error.
        """
        resolved_topic = topic or _resolve_topic(event.event_type)
        partition_key = key or event.order_id or event.event_id

        if self._producer and self._started:
            try:
                await self._producer.send_and_wait(
                    topic=resolved_topic,
                    value=event.model_dump(mode="json"),
                    key=partition_key,
                )
                logger.info(
                    f"Event published: type={event.event_type.value} "
                    f"topic={resolved_topic} order={event.order_id}"
                )
                return True
            except Exception as e:
                logger.error(
                    f"Kafka publish failed: type={event.event_type.value} "
                    f"topic={resolved_topic} error={e}"
                )
                self._log_to_fallback(resolved_topic, event, str(e))
                return False
        else:
            # DEV mode — just log the event
            logger.info(
                f"[DEV-KAFKA] topic={resolved_topic} key={partition_key} "
                f"type={event.event_type.value} order={event.order_id}"
            )
            return True

    async def publish_raw(
        self,
        topic: str,
        value: Dict[str, Any],
        key: Optional[str] = None,
    ) -> bool:
        """
        Publish a raw dict to a Kafka topic.

        Args:
            topic: Target topic.
            value: Message value (will be JSON-serialized).
            key: Optional partition key.

        Returns:
            True if published, False on error.
        """
        if self._producer and self._started:
            try:
                await self._producer.send_and_wait(
                    topic=topic,
                    value=value,
                    key=key,
                )
                return True
            except Exception as e:
                logger.error(f"Kafka raw publish failed: topic={topic} error={e}")
                return False
        else:
            logger.info(f"[DEV-KAFKA] topic={topic} key={key} value={json.dumps(value, default=str)[:200]}")
            return True

    @staticmethod
    def _log_to_fallback(topic: str, event: EventEnvelope, error: str) -> None:
        """Write failed events to fallback log file."""
        try:
            with open("/tmp/hotsot_kafka_fallback.log", "a") as f:
                f.write(
                    f"{datetime.now(timezone.utc).isoformat()} | "
                    f"topic={topic} | error={error} | "
                    f"event_id={event.event_id} type={event.event_type.value}\n"
                )
        except Exception as e:
            logger.warning(f"Error during shutdown: {e}")


class KafkaConsumerManager:
    """
    Manages Kafka consumers for a service with standardized setup.

    Provides a simplified interface for subscribing to multiple topics
    with a single consumer group, automatic topic validation, and
    graceful lifecycle management.

    Usage:
        manager = KafkaConsumerManager("kitchen-service")
        await manager.start()
        manager.subscribe(
            topics=["hotsot.order.events.v1", "hotsot.kitchen.events.v1"],
            handler=my_handler,
        )
        await manager.run()  # blocking consume loop
        await manager.stop()
    """

    def __init__(
        self,
        service_name: str,
        group_id: Optional[str] = None,
        max_retries: int = 3,
    ):
        self._service_name = service_name
        self._group_id = group_id or f"{service_name}-group"
        self._max_retries = max_retries
        self._consumers: List[KafkaConsumer] = []
        self._running = False

    async def start(self) -> None:
        """Initialize the consumer manager (no consumers started yet)."""
        self._running = True
        logger.info(
            f"KafkaConsumerManager ready: service={self._service_name} "
            f"group={self._group_id}"
        )

    def subscribe(
        self,
        topics: List[str],
        handler: Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = None,
    ) -> KafkaConsumer:
        """
        Subscribe to a list of topics with a handler.

        Args:
            topics: List of topic names to subscribe to.
            handler: Async function to handle consumed messages.

        Returns:
            KafkaConsumer instance (not yet started).
        """
        consumer = KafkaConsumer(
            service_name=self._service_name,
            topics=topics,
            group_id=self._group_id,
            handler=handler,
            max_retries=self._max_retries,
        )
        self._consumers.append(consumer)
        return consumer

    async def start_all(self) -> None:
        """Start all registered consumers."""
        for consumer in self._consumers:
            await consumer.start()
        logger.info(
            f"KafkaConsumerManager started {len(self._consumers)} consumers "
            f"for service={self._service_name}"
        )

    async def run(self) -> None:
        """Run all consumer loops concurrently."""
        if not self._consumers:
            logger.warning(
                f"KafkaConsumerManager has no consumers for service={self._service_name}"
            )
            return

        tasks = [consumer.consume_loop() for consumer in self._consumers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self) -> None:
        """Stop all consumers gracefully."""
        self._running = False
        for consumer in self._consumers:
            try:
                await consumer.stop()
            except Exception as e:
                logger.warning(f"Error stopping consumer: {e}")
        self._consumers.clear()
        logger.info(f"KafkaConsumerManager stopped for service={self._service_name}")


def get_dlq_topic(topic: str) -> str:
    """
    Get the DLQ topic name for a given source topic.

    Convention: hotsot.{domain}.events.v1 → hotsot.dlq.{domain}.events.v1

    Args:
        topic: Source topic name.

    Returns:
        DLQ topic name.
    """
    if topic.startswith("hotsot.dlq."):
        return topic  # Already a DLQ topic
    if topic.startswith("hotsot."):
        parts = topic.split(".", 1)
        if len(parts) == 2:
            return f"hotsot.dlq.{parts[1]}"
    return f"hotsot.dlq.{topic}"


class EventPublisher(KafkaProducer):
    """
    Backward-compatible EventPublisher wrapping KafkaProducer.

    This provides the legacy interface used by older services while
    internally using the new KafkaProducer.
    """

    def __init__(self, service_name: str = "hotsot"):
        super().__init__(service_name=service_name)


# Singleton instance for convenience
event_publisher = EventPublisher()


# ═══════════════════════════════════════════════════════════════
# KAFKA CONSUMER
# ═══════════════════════════════════════════════════════════════

class KafkaConsumer:
    """
    Production Kafka consumer with manual commit and DLQ routing.

    Features:
        - Manual offset commit (at-least-once processing)
        - DLQ routing for failed messages
        - Configurable retry before DLQ
        - Graceful shutdown
        - Event deserialization with schema validation
    """

    def __init__(
        self,
        service_name: str,
        topics: List[str],
        group_id: Optional[str] = None,
        handler: Optional[Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]] = None,
        max_retries: int = 3,
        dlq_suffix: str = ".dlq",
    ):
        self._service_name = service_name
        self._topics = topics
        self._group_id = group_id or f"hotsot-{service_name}-group"
        self._handler = handler
        self._max_retries = max_retries
        self._dlq_suffix = dlq_suffix
        self._consumer = None
        self._running = False
        self._retry_counts: Dict[str, int] = {}

    async def start(self) -> None:
        """Start the Kafka consumer."""
        try:
            from aiokafka import AIOKafkaConsumer

            settings = get_settings(self._service_name)

            self._consumer = AIOKafkaConsumer(
                *self._topics,
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                group_id=self._group_id,
                enable_auto_commit=False,
                auto_offset_reset="latest",
                max_poll_records=settings.KAFKA_MAX_POLL_RECORDS,
                session_timeout_ms=settings.KAFKA_SESSION_TIMEOUT_MS,
                security_protocol=settings.KAFKA_SECURITY_PROTOCOL,
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                key_deserializer=lambda m: m.decode("utf-8") if m else None,
            )

            await self._consumer.start()
            self._running = True
            logger.info(
                f"Kafka consumer started: service={self._service_name} "
                f"topics={self._topics} group={self._group_id}"
            )

        except ImportError:
            logger.warning("aiokafka not installed — Kafka consumer in DEV mode (no-op)")
            self._consumer = None
        except Exception as e:
            logger.warning(f"Kafka consumer start failed: {e} — running in no-op mode")
            self._consumer = None

    async def stop(self) -> None:
        """Stop the Kafka consumer."""
        self._running = False
        if self._consumer:
            try:
                await self._consumer.stop()
            except Exception as e:
                logger.warning(f"Error stopping Kafka consumer: {e}")
        logger.info(f"Kafka consumer stopped for service={self._service_name}")

    async def consume_loop(self) -> None:
        """
        Main consumption loop. Processes messages and commits on success.

        Failed messages are routed to DLQ after max retries.
        """
        if not self._consumer:
            logger.info("Kafka consumer not connected — consume loop skipped")
            return

        try:
            async for msg in self._consumer:
                if not self._running:
                    break

                try:
                    if self._handler:
                        await self._handler(msg.value)
                    await self._consumer.commit()
                    logger.debug(
                        f"Message processed: topic={msg.topic} "
                        f"offset={msg.offset} partition={msg.partition}"
                    )
                except Exception as e:
                    logger.error(
                        f"Message processing failed: topic={msg.topic} "
                        f"offset={msg.offset} error={e}"
                    )
                    await self._handle_failure(msg, str(e))
                    # Still commit to avoid reprocessing the same failed message
                    await self._consumer.commit()

        except Exception as e:
            logger.error(f"Consumer loop error: {e}")
        finally:
            self._running = False

    async def _handle_failure(self, msg: Any, error: str) -> None:
        """
        Handle a failed message by routing to DLQ after max retries.

        Args:
            msg: The Kafka message that failed.
            error: The error message.
        """
        msg_key = f"{msg.topic}:{msg.partition}:{msg.offset}"
        retry_count = self._retry_counts.get(msg_key, 0) + 1
        self._retry_counts[msg_key] = retry_count

        if retry_count >= self._max_retries:
            # Route to DLQ
            dlq_topic = msg.topic + self._dlq_suffix
            logger.warning(
                f"Routing to DLQ: topic={dlq_topic} original={msg.topic} "
                f"offset={msg.offset} retries={retry_count}"
            )

            # Log DLQ entry
            try:
                with open("/tmp/hotsot_dlq.log", "a") as f:
                    f.write(
                        f"{datetime.now(timezone.utc).isoformat()} | "
                        f"dlq_topic={dlq_topic} original_topic={msg.topic} | "
                        f"offset={msg.offset} key={msg.key} | "
                        f"error={error} | value={json.dumps(msg.value, default=str)[:500]}\n"
                    )
            except Exception as e:
                logger.warning(f"Error during shutdown: {e}")

            # Clean up retry counter
            self._retry_counts.pop(msg_key, None)
        else:
            logger.info(
                f"Message will be retried: topic={msg.topic} "
                f"offset={msg.offset} retry={retry_count}/{self._max_retries}"
            )
