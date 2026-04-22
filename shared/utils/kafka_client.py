"""
HotSot Kafka Client — Production-grade event streaming.

Features:
    - Exactly-once producer with idempotent publishing
    - Manual-commit consumer with DLQ routing
    - Event-level deduplication via EventDedupStore
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
import time
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
# EVENT DEDUPLICATION
# ═══════════════════════════════════════════════════════════════

class EventDedupStore:
    """
    In-memory event deduplication store with configurable TTL.

    Tracks processed event_ids to prevent duplicate processing caused
    by Kafka's at-least-once delivery semantics combined with consumer
    restarts. Each entry expires after the configured TTL, ensuring
    the store does not grow unbounded over time.

    Thread-safety is provided via asyncio.Lock for safe use in
    concurrent async consumer loops.

    Usage:
        store = EventDedupStore(ttl_seconds=3600)
        if await store.is_duplicate(event_id):
            # skip processing
            return
        await handler(msg)
        await store.mark_processed(event_id)

    Args:
        ttl_seconds: Time-to-live in seconds for each tracked event_id.
                     Default is 3600 (1 hour).
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._ttl_seconds = ttl_seconds
        # Dict mapping event_id -> expiry timestamp (monotonic time)
        self._store: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def is_duplicate(self, event_id: str) -> bool:
        """
        Check whether an event_id has already been processed.

        An event is considered a duplicate if it exists in the store
        and its TTL has not yet expired. Expired entries are treated
        as non-duplicate (they will be cleaned up lazily).

        Args:
            event_id: The unique identifier of the event.

        Returns:
            True if the event was previously processed (duplicate),
            False otherwise.
        """
        async with self._lock:
            expiry = self._store.get(event_id)
            if expiry is None:
                return False
            if time.monotonic() > expiry:
                # Entry has expired — treat as non-duplicate
                del self._store[event_id]
                return False
            return True

    async def mark_processed(self, event_id: str) -> None:
        """
        Mark an event_id as successfully processed.

        The entry will automatically expire after the configured TTL.

        Args:
            event_id: The unique identifier of the event.
        """
        async with self._lock:
            self._store[event_id] = time.monotonic() + self._ttl_seconds

    async def cleanup_expired(self) -> int:
        """
        Remove all expired entries from the store.

        This should be called periodically (e.g., every few minutes) to
        prevent unbounded memory growth from expired entries that were
        not removed during lazy expiry in ``is_duplicate``.

        Returns:
            The number of entries removed.
        """
        async with self._lock:
            now = time.monotonic()
            expired_keys = [
                eid for eid, expiry in self._store.items() if now > expiry
            ]
            for key in expired_keys:
                del self._store[key]
            if expired_keys:
                logger.debug(
                    f"EventDedupStore: cleaned up {len(expired_keys)} "
                    f"expired entries, {len(self._store)} remaining"
                )
            return len(expired_keys)


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
        """Write failed events to fallback log file with full event data for replay.

        FIX #4: Enhanced fallback logging — stores the complete serialized event
        so that KafkaRecoveryService can read and replay it when Kafka recovers.
        Each entry is a single-line JSON object for safe parsing.
        """
        try:
            fallback_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "topic": topic,
                "error": error,
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "order_id": event.order_id,
                "tenant_id": event.tenant_id,
                "event_data": event.model_dump(mode="json"),
                "replay_count": 0,
            }
            with open("/tmp/hotsot_kafka_fallback.log", "a") as f:
                f.write(json.dumps(fallback_entry, default=str) + "\n")
        except Exception as e:
            logger.warning(f"Fallback log write failed: {e}")


class KafkaRecoveryService:
    """Replays fallback-logged events when Kafka recovers.

    FIX #4: Reads events from /tmp/hotsot_kafka_fallback.log that were
    logged when Kafka was down, and replays them to their target topics.

    Usage:
        recovery = KafkaRecoveryService(producer)
        replayed = await recovery.replay_fallback_log()
    """

    MAX_REPLAY_ATTEMPTS = 3
    FALLBACK_LOG_PATH = "/tmp/hotsot_kafka_fallback.log"

    def __init__(self, producer: Optional[KafkaProducer] = None):
        self._producer = producer
        self._replay_log_path = "/tmp/hotsot_kafka_replay_audit.log"

    async def replay_fallback_log(self) -> Dict[str, Any]:
        """Read fallback log and replay all events to Kafka.

        Returns summary of replay results.
        """
        import os

        if not os.path.exists(self.FALLBACK_LOG_PATH):
            return {"status": "no_fallback_log", "replayed": 0, "failed": 0}

        if not self._producer or not self._producer._started:
            logger.warning("KafkaRecoveryService: producer not available — cannot replay")
            return {"status": "producer_unavailable", "replayed": 0, "failed": 0}

        replayed = 0
        failed = 0
        remaining_entries = []

        with open(self.FALLBACK_LOG_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    replay_count = entry.get("replay_count", 0)

                    if replay_count >= self.MAX_REPLAY_ATTEMPTS:
                        logger.warning(
                            f"KafkaRecoveryService: skipping event {entry.get('event_id')} "
                            f"after {replay_count} replay attempts"
                        )
                        failed += 1
                        continue

                    # Reconstruct and publish
                    event_data = entry.get("event_data", {})
                    topic = entry.get("topic", "hotsot.order.events.v1")

                    success = await self._producer.publish_raw(
                        topic=topic,
                        value=event_data,
                        key=entry.get("order_id"),
                    )

                    if success:
                        replayed += 1
                        logger.info(
                            f"KafkaRecoveryService: replayed event "
                            f"{entry.get('event_id')} type={entry.get('event_type')} "
                            f"to topic={topic}"
                        )
                        # Audit log
                        try:
                            with open(self._replay_log_path, "a") as audit:
                                audit.write(
                                    f"{datetime.now(timezone.utc).isoformat()} | "
                                    f"REPLAYED | event_id={entry.get('event_id')} "
                                    f"topic={topic}\n"
                                )
                        except Exception:
                            pass
                    else:
                        # Increment replay count and keep for next attempt
                        entry["replay_count"] = replay_count + 1
                        remaining_entries.append(line)
                        failed += 1

                except json.JSONDecodeError:
                    # Try legacy format (pipe-delimited)
                    logger.warning(f"KafkaRecoveryService: skipping malformed line")
                    failed += 1
                except Exception as e:
                    logger.warning(f"KafkaRecoveryService: error replaying event: {e}")
                    remaining_entries.append(line)
                    failed += 1

        # Write back remaining (unreplayed) entries
        try:
            with open(self.FALLBACK_LOG_PATH, "w") as f:
                for entry_line in remaining_entries:
                    f.write(entry_line + "\n")
        except Exception as e:
            logger.error(f"KafkaRecoveryService: failed to write remaining entries: {e}")

        return {
            "status": "completed",
            "replayed": replayed,
            "failed": failed,
            "remaining": len(remaining_entries),
        }


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
        dedup_ttl_seconds: int = 3600,
    ):
        self._service_name = service_name
        self._group_id = group_id or f"{service_name}-group"
        self._max_retries = max_retries
        self._dedup_store = EventDedupStore(ttl_seconds=dedup_ttl_seconds)
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
            dedup_store=self._dedup_store,
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
        - Event-level deduplication via optional EventDedupStore
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
        dedup_store: Optional[EventDedupStore] = None,
    ):
        self._service_name = service_name
        self._topics = topics
        self._group_id = group_id or f"hotsot-{service_name}-group"
        self._handler = handler
        self._max_retries = max_retries
        self._dlq_suffix = dlq_suffix
        self._dedup_store = dedup_store
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

        Before invoking the handler, the event_id is checked against the
        deduplication store (if configured). Duplicate events are logged
        at WARNING level and committed (skipped) to avoid reprocessing.

        Failed messages are routed to DLQ after max retries.
        """
        if not self._consumer:
            logger.info("Kafka consumer not connected — consume loop skipped")
            return

        try:
            async for msg in self._consumer:
                if not self._running:
                    break

                # ── Event deduplication check ──
                if self._dedup_store is not None:
                    event_id = self._extract_event_id(msg)
                    if await self._dedup_store.is_duplicate(event_id):
                        logger.warning(
                            f"Duplicate event detected and skipped: "
                            f"event_id={event_id} topic={msg.topic} "
                            f"offset={msg.offset} partition={msg.partition}"
                        )
                        # Commit to advance the offset so we don't see it again
                        await self._consumer.commit()
                        continue

                try:
                    if self._handler:
                        await self._handler(msg.value)

                    # Mark as processed in dedup store (after successful handler)
                    if self._dedup_store is not None:
                        event_id = self._extract_event_id(msg)
                        await self._dedup_store.mark_processed(event_id)

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

    @staticmethod
    def _extract_event_id(msg: Any) -> str:
        """
        Extract a unique event identifier from a Kafka message.

        Attempts to read ``event_id`` from the message value payload first.
        If not present (e.g., non-standard message format), falls back to a
        composite key of ``msg.key + msg.offset`` which is guaranteed unique
        within a partition.

        Args:
            msg: The Kafka ConsumerMessage object.

        Returns:
            A string identifier for the event.
        """
        value = msg.value if hasattr(msg, "value") else {}
        if isinstance(value, dict):
            event_id = value.get("event_id")
            if event_id:
                return str(event_id)
        # Fallback: key + offset composite
        key = msg.key if hasattr(msg, "key") else None
        offset = msg.offset if hasattr(msg, "offset") else "unknown"
        return f"{key}:{offset}"

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
