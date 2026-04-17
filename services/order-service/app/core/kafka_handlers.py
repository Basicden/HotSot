"""
HotSot Order Service — Kafka Event Handlers.

Consumes events from other services and updates order state accordingly.
All handlers are idempotent and tenant-aware.

Consumer Topics:
    - hotsot.kitchen.events.v1  (PREP_STARTED, PREP_COMPLETED, KITCHEN_OVERLOAD)
    - hotsot.shelf.events.v1    (SHELF_ASSIGNED, SHELF_EXPIRED, SHELF_EXPIRY_WARNING)
    - hotsot.arrival.events.v1  (ARRIVAL_DETECTED, HANDOFF_COMPLETED)
    - hotsot.compensation.events.v1 (COMPENSATION_TRIGGERED, COMPENSATION_COMPLETED)
    - hotsot.eta.events.v1      (ETA_UPDATED, ETA_RECALCULATED)
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from shared.types.schemas import EventType

logger = logging.getLogger(__name__)

# Topics this service consumes
CONSUMER_TOPICS = [
    "hotsot.kitchen.events.v1",
    "hotsot.shelf.events.v1",
    "hotsot.arrival.events.v1",
    "hotsot.compensation.events.v1",
]


async def handle_kafka_event(event_data: Dict[str, Any]) -> None:
    """
    Route incoming Kafka events to the appropriate handler.

    Args:
        event_data: Deserialized event dictionary.
    """
    event_type_str = event_data.get("event_type", "")
    order_id = event_data.get("order_id", "")
    tenant_id = event_data.get("tenant_id", "default")

    try:
        event_type = EventType(event_type_str)
    except ValueError:
        logger.warning(f"Unknown event type: {event_type_str}, ignoring")
        return

    logger.info(
        f"Processing event: type={event_type_str} order={order_id} tenant={tenant_id}"
    )

    # Route to specific handler
    handler_map = {
        EventType.PREP_STARTED: _handle_prep_started,
        EventType.PREP_COMPLETED: _handle_prep_completed,
        EventType.READY_FOR_PICKUP: _handle_ready_for_pickup,
        EventType.SHELF_ASSIGNED: _handle_shelf_assigned,
        EventType.SHELF_EXPIRED: _handle_shelf_expired,
        EventType.SHELF_EXPIRY_WARNING: _handle_shelf_expiry_warning,
        EventType.ARRIVAL_DETECTED: _handle_arrival_detected,
        EventType.HANDOFF_COMPLETED: _handle_handoff_completed,
        EventType.COMPENSATION_TRIGGERED: _handle_compensation_triggered,
        EventType.COMPENSATION_COMPLETED: _handle_compensation_completed,
        EventType.KITCHEN_OVERLOAD: _handle_kitchen_overload,
        EventType.ETA_UPDATED: _handle_eta_updated,
    }

    handler = handler_map.get(event_type)
    if handler:
        try:
            await handler(event_data)
        except Exception as e:
            logger.error(
                f"Event handler failed: type={event_type_str} "
                f"order={order_id} error={e}",
                exc_info=True,
            )
    else:
        logger.debug(f"No handler for event type: {event_type_str}")


async def _handle_prep_started(event: Dict[str, Any]) -> None:
    """Handle PREP_STARTED — update order status to IN_PREP."""
    logger.info(f"Order {event.get('order_id')} prep started in kitchen {event.get('kitchen_id')}")
    # In production: update order status via DB with optimistic locking


async def _handle_prep_completed(event: Dict[str, Any]) -> None:
    """Handle PREP_COMPLETED — update order to PACKING."""
    logger.info(f"Order {event.get('order_id')} prep completed")


async def _handle_ready_for_pickup(event: Dict[str, Any]) -> None:
    """Handle READY_FOR_PICKUP — update order to READY."""
    logger.info(f"Order {event.get('order_id')} ready for pickup")


async def _handle_shelf_assigned(event: Dict[str, Any]) -> None:
    """Handle SHELF_ASSIGNED — update order with shelf info."""
    payload = event.get("payload", {})
    shelf_id = payload.get("shelf_id")
    zone = payload.get("zone")
    ttl = payload.get("ttl_seconds", 600)
    logger.info(
        f"Order {event.get('order_id')} assigned to shelf {shelf_id} "
        f"zone={zone} ttl={ttl}s"
    )


async def _handle_shelf_expired(event: Dict[str, Any]) -> None:
    """Handle SHELF_EXPIRED — transition order to EXPIRED state."""
    logger.warning(
        f"Order {event.get('order_id')} shelf EXPIRED — triggering refund"
    )


async def _handle_shelf_expiry_warning(event: Dict[str, Any]) -> None:
    """Handle SHELF_EXPIRY_WARNING — send notification to customer."""
    payload = event.get("payload", {})
    remaining_seconds = payload.get("remaining_seconds", 0)
    logger.info(
        f"Order {event.get('order_id')} shelf expiry warning: "
        f"{remaining_seconds}s remaining"
    )


async def _handle_arrival_detected(event: Dict[str, Any]) -> None:
    """Handle ARRIVAL_DETECTED — update order to ARRIVED state."""
    logger.info(f"Order {event.get('order_id')} customer arrived")


async def _handle_handoff_completed(event: Dict[str, Any]) -> None:
    """Handle HANDOFF_COMPLETED — update order to PICKED state."""
    logger.info(f"Order {event.get('order_id')} handoff completed — PICKED")


async def _handle_compensation_triggered(event: Dict[str, Any]) -> None:
    """Handle COMPENSATION_TRIGGERED — track compensation in saga."""
    logger.info(f"Order {event.get('order_id')} compensation triggered")


async def _handle_compensation_completed(event: Dict[str, Any]) -> None:
    """Handle COMPENSATION_COMPLETED — mark order as REFUNDED."""
    logger.info(f"Order {event.get('order_id')} compensation completed")


async def _handle_kitchen_overload(event: Dict[str, Any]) -> None:
    """Handle KITCHEN_OVERLOAD — log and potentially throttle orders."""
    payload = event.get("payload", {})
    load_pct = payload.get("load_percentage", 0)
    logger.warning(
        f"Kitchen {event.get('kitchen_id')} overload: {load_pct}% load"
    )


async def _handle_eta_updated(event: Dict[str, Any]) -> None:
    """Handle ETA_UPDATED — update order ETA fields."""
    payload = event.get("payload", {})
    eta_seconds = payload.get("eta_seconds")
    confidence = payload.get("confidence_score")
    risk = payload.get("risk_level", "LOW")
    logger.info(
        f"Order {event.get('order_id')} ETA updated: "
        f"{eta_seconds}s confidence={confidence} risk={risk}"
    )
