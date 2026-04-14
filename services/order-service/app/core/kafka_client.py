"""HotSot Order Service — Kafka Client (Event Backbone)."""

import json
from typing import Dict, Any, Optional
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer

from app.core.config import config

producer: Optional[AIOKafkaProducer] = None


async def init_kafka():
    """Initialize Kafka producer."""
    global producer
    producer = AIOKafkaProducer(
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        acks="all",
        retries=3,
        retry_backoff_ms=1000,
    )
    await producer.start()


async def close_kafka():
    """Close Kafka producer."""
    global producer
    if producer:
        await producer.stop()


async def publish_event(topic: str, event: Dict[str, Any]):
    """Publish an event to a Kafka topic."""
    if producer is None:
        print(f"[Kafka] Producer not initialized, skipping event to {topic}")
        return

    try:
        # Use order_id as key for partitioning (same order → same partition)
        key = str(event.get("order_id", "unknown")).encode("utf-8")
        await producer.send_and_wait(topic, event, key=key)
    except Exception as e:
        print(f"[Kafka] Failed to publish to {topic}: {e}")
        # In production: retry queue, dead letter queue, or alert


async def create_consumer(
    topics: list[str],
    group_id: Optional[str] = None,
) -> AIOKafkaConsumer:
    """Create a Kafka consumer for given topics."""
    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        group_id=group_id or config.KAFKA_GROUP_ID,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="latest",
        enable_auto_commit=True,
    )
    await consumer.start()
    return consumer
