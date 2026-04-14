"""HotSot Kitchen Service — Kafka Client."""

import json
from typing import Dict, Any, Optional
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer

producer: Optional[AIOKafkaProducer] = None


async def init_kafka():
    global producer
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
    )
    await producer.start()


async def close_kafka():
    global producer
    if producer:
        await producer.stop()


async def publish_event(topic: str, event: Dict[str, Any]):
    if producer is None:
        return
    key = str(event.get("order_id", "unknown")).encode("utf-8")
    await producer.send_and_wait(topic, event, key=key)
