"""HotSot Order Service — Event System."""

import time
from typing import Dict, Any, Optional, List, Callable
from app.core.schemas import EventType


class Event:
    """Immutable event representing a state change in the system."""

    def __init__(
        self,
        order_id: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ):
        self.order_id = order_id
        self.event_type = event_type
        self.payload = payload or {}
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    def __repr__(self):
        return f"Event({self.event_type}, order={self.order_id})"


# ─── EVENT HANDLER REGISTRY ───
_handlers: Dict[str, List[Callable]] = {}


def register_handler(event_type: str, handler: Callable):
    """Register a handler for a specific event type."""
    if event_type not in _handlers:
        _handlers[event_type] = []
    _handlers[event_type].append(handler)


async def emit_event(event: Event):
    """
    Emit an event to:
    1. Local handlers (in-process)
    2. Kafka (cross-service)
    3. Redis (real-time projection)
    """
    # 1. Local handlers
    handlers = _handlers.get(event.event_type, [])
    for handler in handlers:
        try:
            result = handler(event)
            if hasattr(result, "__await__"):
                await result
        except Exception as e:
            print(f"[EventHandler] Error in {handler.__name__}: {e}")

    # 2. Kafka emission
    try:
        from app.core.kafka_client import publish_event
        await publish_event(f"orders.{event.event_type.lower()}", event.to_dict())
    except Exception as e:
        print(f"[Kafka] Failed to emit event: {e}")

    # 3. Redis projection update
    try:
        from app.core.redis_client import set_order_state
        await set_order_state(event.order_id, {
            "status": event.event_type,
            "updated_at": event.timestamp,
        })
    except Exception as e:
        print(f"[Redis] Failed to update projection: {e}")
