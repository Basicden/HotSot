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

from shared.types.schemas import EventType

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
        await _consumer_manager.start()

        topics = [
            "hotsot.order.events.v1",
            "hotsot.kitchen.events.v1",
            "hotsot.priority.events.v1",
        ]

        # Subscribe to all kitchen-relevant topics with a unified handler
        _consumer_manager.subscribe(
            topics=topics,
            handler=_handle_event,
        )

        # Start all consumers
        await _consumer_manager.start_all()

        # Run consumer loop in background
        _consumer_task = asyncio.create_task(
            _consumer_manager.run(),
            name="kitchen-kafka-consumer",
        )
        logger.info("Kitchen Kafka consumers started for topics: %s", topics)

    except Exception as e:
        logger.error("Failed to start kitchen Kafka consumers: %s", e)
        # Non-fatal: service can still accept HTTP requests
        _consumer_manager = None


async def _handle_event(event: Dict[str, Any]):
    """Route incoming events to the appropriate handler based on event_type."""
    event_type = event.get("event_type", "")

    # Order lifecycle events
    if event_type in (
        EventType.ORDER_CREATED.value,
        EventType.PAYMENT_CONFIRMED.value,
        EventType.ORDER_CANCELLED.value,
        EventType.ORDER_EXPIRED.value,
    ):
        await handle_order_event(event)

    # Priority/queue events
    elif event_type in (
        EventType.PRIORITY_UPDATED.value,
        EventType.QUEUE_ASSIGNED.value,
        EventType.QUEUE_REORDERED.value,
    ):
        await handle_priority_event(event)

    # Kitchen-specific events
    elif event_type in (
        EventType.KITCHEN_OVERLOAD.value,
        EventType.KITCHEN_DEGRADED.value,
        EventType.KITCHEN_RECOVERED.value,
        EventType.THROTTLE_APPLIED.value,
        EventType.KITCHEN_THROUGHPUT_UPDATE.value,
    ):
        await handle_kitchen_event(event)

    else:
        logger.debug("Kitchen received unhandled event type: %s", event_type)


async def stop_consumers():
    """Stop Kafka consumers gracefully."""
    global _consumer_task, _consumer_manager

    if _consumer_task and not _consumer_task.done():
        _consumer_task.cancel()
        try:
            await asyncio.wait_for(_consumer_task, timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
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
        # Kitchen load is incremented via Redis INCR in the kitchen service routes

    elif event_type == EventType.PAYMENT_CONFIRMED.value:
        logger.info(
            "Kitchen received PAYMENT_CONFIRMED: order=%s kitchen=%s",
            order_id, kitchen_id,
        )
        # Queue manager will be notified through priority service

    elif event_type == EventType.ORDER_CANCELLED.value:
        logger.info(
            "Kitchen received ORDER_CANCELLED: order=%s kitchen=%s",
            order_id, kitchen_id,
        )
        # Kitchen load decremented via Redis DECR in the kitchen service routes

    elif event_type == EventType.ORDER_EXPIRED.value:
        logger.info(
            "Kitchen received ORDER_EXPIRED: order=%s kitchen=%s",
            order_id, kitchen_id,
        )
        # Kitchen capacity released via Redis in the kitchen service routes


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
        # Queue re-sorting is handled by the kitchen service queue routes


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
        # Throttling is applied via Redis state in kitchen service routes

    elif event_type == EventType.KITCHEN_RECOVERED.value:
        logger.info(
            "Kitchen KITCHEN_RECOVERED: kitchen=%s tenant=%s",
            kitchen_id, tenant_id,
        )
        # Normal throughput restored via Redis state in kitchen service routes
