"""HotSot Kitchen Service — Kafka Consumer Handlers.

Consumes events from order, priority, and kitchen topics to drive
kitchen operations: queue management, batch cooking, throughput tracking.

Consumer Groups:
    - kitchen-service-group: listens to hotsot.order.events.v1, hotsot.kitchen.events.v1,
      hotsot.priority.events.v1
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

from shared.types.schemas import KAFKA_TOPICS, EventType

logger = logging.getLogger(__name__)

# Reference to Kafka consumer manager (set during startup)
_consumer_manager = None
_consumer_task: Optional[asyncio.Task] = None


async def start_consumers():
    """
    Start Kafka consumers for the kitchen service.

    Subscribes to:
    - hotsot.order.events.v1 — ORDER_CREATED, PAYMENT_CONFIRMED, ORDER_CANCELLED
    - hotsot.kitchen.events.v1 — KITCHEN_OVERLOAD, KITCHEN_DEGRADED, KITCHEN_RECOVERED
    - hotsot.priority.events.v1 — PRIORITY_UPDATED, QUEUE_ASSIGNED, QUEUE_REORDERED
    """
    from shared.utils.kafka_client import KafkaConsumerManager

    global _consumer_manager, _consumer_task

    try:
        _consumer_manager = KafkaConsumerManager("kitchen-service")

        topics = [
            KAFKA_TOPICS.get("hotsot.order.events.v1", "hotsot.order.events.v1"),
            KAFKA_TOPICS.get("hotsot.kitchen.events.v1", "hotsot.kitchen.events.v1"),
            KAFKA_TOPICS.get("hotsot.priority.events.v1", "hotsot.priority.events.v1"),
        ]

        _consumer_task = asyncio.create_task(
            _consume_loop(topics),
            name="kitchen-kafka-consumer",
        )
        logger.info("Kitchen Kafka consumers started for topics: %s", topics)

    except Exception as e:
        logger.error("Failed to start kitchen Kafka consumers: %s", e)
        # Non-fatal: service can still accept HTTP requests
        _consumer_manager = None


async def _consume_loop(topics: list):
    """Background loop that consumes Kafka events."""
    while True:
        try:
            if _consumer_manager is None:
                await asyncio.sleep(5)
                continue

            # In production, this would use the consumer_manager to poll messages
            # For now, sleep to avoid busy-waiting
            await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("Kitchen Kafka consumer loop cancelled")
            break
        except Exception as e:
            logger.error("Kitchen Kafka consumer error: %s", e)
            await asyncio.sleep(5)  # Back off on error


async def stop_consumers():
    """Stop Kafka consumers gracefully."""
    global _consumer_task, _consumer_manager

    if _consumer_task and not _consumer_task.done():
        _consumer_task.cancel()
        try:
            await asyncio.wait_for(_consumer_task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Kitchen Kafka consumer did not stop in time")

    if _consumer_manager:
        try:
            await _consumer_manager.stop()
        except Exception as e:
            logger.warning("Error stopping kitchen Kafka consumer manager: %s", e)

    _consumer_manager = None
    _consumer_task = None
    logger.info("Kitchen Kafka consumers stopped")


async def handle_order_event(event: Dict[str, Any]):
    """
    Handle order events that affect kitchen operations.

    Events:
    - ORDER_CREATED: Increment kitchen load counter
    - PAYMENT_CONFIRMED: Prepare for order queue assignment
    - ORDER_CANCELLED: Decrement kitchen load counter
    - ORDER_EXPIRED: Release kitchen capacity
    """
    event_type = event.get("event_type", "")
    order_id = event.get("order_id", "")
    kitchen_id = event.get("kitchen_id", "")
    tenant_id = event.get("tenant_id", "default")
    payload = event.get("payload", {})

    if event_type == EventType.ORDER_CREATED.value:
        logger.info(
            "Kitchen received ORDER_CREATED: order=%s kitchen=%s tenant=%s",
            order_id, kitchen_id, tenant_id,
        )
        # TODO: Increment kitchen load in Redis

    elif event_type == EventType.PAYMENT_CONFIRMED.value:
        logger.info(
            "Kitchen received PAYMENT_CONFIRMED: order=%s kitchen=%s",
            order_id, kitchen_id,
        )
        # TODO: Notify queue manager to expect this order

    elif event_type == EventType.ORDER_CANCELLED.value:
        logger.info(
            "Kitchen received ORDER_CANCELLED: order=%s kitchen=%s",
            order_id, kitchen_id,
        )
        # TODO: Decrement kitchen load, remove from queue if present

    elif event_type == EventType.ORDER_EXPIRED.value:
        logger.info(
            "Kitchen received ORDER_EXPIRED: order=%s kitchen=%s",
            order_id, kitchen_id,
        )
        # TODO: Release kitchen capacity


async def handle_priority_event(event: Dict[str, Any]):
    """
    Handle priority events that affect kitchen queue ordering.

    Events:
    - PRIORITY_UPDATED: Re-sort kitchen queue
    - QUEUE_ASSIGNED: Add to kitchen queue
    - QUEUE_REORDERED: Re-sort kitchen queue
    """
    event_type = event.get("event_type", "")
    order_id = event.get("order_id", "")
    tenant_id = event.get("tenant_id", "default")

    if event_type in (EventType.PRIORITY_UPDATED.value, EventType.QUEUE_REORDERED.value):
        logger.info(
            "Kitchen received %s: order=%s tenant=%s",
            event_type, order_id, tenant_id,
        )
        # TODO: Re-sort queue based on new priority scores


async def handle_kitchen_event(event: Dict[str, Any]):
    """
    Handle kitchen-specific events.

    Events:
    - KITCHEN_OVERLOAD: Apply throttle
    - KITCHEN_DEGRADED: Reduce acceptance rate
    - KITCHEN_RECOVERED: Restore normal throughput
    """
    event_type = event.get("event_type", "")
    kitchen_id = event.get("kitchen_id", "")
    tenant_id = event.get("tenant_id", "default")

    if event_type == EventType.KITCHEN_OVERLOAD.value:
        logger.warning(
            "Kitchen KITCHEN_OVERLOAD: kitchen=%s tenant=%s",
            kitchen_id, tenant_id,
        )
        # TODO: Apply throttle to incoming orders

    elif event_type == EventType.KITCHEN_RECOVERED.value:
        logger.info(
            "Kitchen KITCHEN_RECOVERED: kitchen=%s tenant=%s",
            kitchen_id, tenant_id,
        )
        # TODO: Restore normal throughput
