"""HotSot Realtime Service — Broadcast Routes.

REST API for broadcasting updates to WebSocket subscribers.
"""

from typing import Dict
from datetime import datetime
from fastapi import APIRouter

from app.routes.websocket import active_connections, kitchen_connections

router = APIRouter()


@router.post("/broadcast/order/{order_id}")
async def broadcast_order_update(order_id: str, event_type: str, payload: Dict = {}):
    """Broadcast update to all connections subscribed to an order."""
    connections = active_connections.get(order_id, set())
    message = {
        "type": event_type,
        "order_id": order_id,
        "payload": payload,
        "timestamp": datetime.utcnow().isoformat()
    }
    disconnected = set()
    for ws in connections:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        connections.discard(ws)
    return {"order_id": order_id, "delivered_to": len(connections) - len(disconnected)}


@router.post("/broadcast/kitchen/{kitchen_id}")
async def broadcast_kitchen_update(kitchen_id: str, event_type: str, payload: Dict = {}):
    """Broadcast update to all staff connections for a kitchen."""
    connections = kitchen_connections.get(kitchen_id, set())
    message = {
        "type": event_type,
        "kitchen_id": kitchen_id,
        "payload": payload,
        "timestamp": datetime.utcnow().isoformat()
    }
    disconnected = set()
    for ws in connections:
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.add(ws)
    for ws in disconnected:
        connections.discard(ws)
    return {"kitchen_id": kitchen_id, "delivered_to": len(connections) - len(disconnected)}
