"""HotSot Realtime Service — Core Modules.

Provides Redis pub/sub, Kafka consumer, and WebSocket connection management
for broadcasting live updates to connected clients.

Modules:
    connection_manager — WebSocket connection lifecycle & broadcasting
    redis_client       — Redis pub/sub for cross-instance coordination
    kafka_client       — Kafka consumer for event-driven updates
"""

from app.core.connection_manager import ConnectionManager
from app.core.redis_client import RedisPubSub
from app.core.kafka_client import KafkaEventConsumer

__all__ = ["ConnectionManager", "RedisPubSub", "KafkaEventConsumer"]
