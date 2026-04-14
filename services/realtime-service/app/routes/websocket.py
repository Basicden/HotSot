"""HotSot Realtime Service — WebSocket Routes.

WebSocket endpoints for order tracking and kitchen dashboard live updates.
"""

import json
from typing import Dict, Set
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# Connection pools
active_connections: Dict[str, Set[WebSocket]] = {}
kitchen_connections: Dict[str, Set[WebSocket]] = {}


@router.websocket("/ws/order/{order_id}")
async def order_websocket(websocket: WebSocket, order_id: str):
    """WebSocket for user order tracking. India fallback: polling if WS fails."""
    await websocket.accept()
    if order_id not in active_connections:
        active_connections[order_id] = set()
    active_connections[order_id].add(websocket)
    try:
        await websocket.send_json({
            "type": "CONNECTED",
            "order_id": order_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "PING":
                await websocket.send_json({"type": "PONG"})
    except WebSocketDisconnect:
        pass
    finally:
        active_connections[order_id].discard(websocket)
        if not active_connections[order_id]:
            del active_connections[order_id]


@router.websocket("/ws/kitchen/{kitchen_id}")
async def kitchen_websocket(websocket: WebSocket, kitchen_id: str):
    """WebSocket for kitchen staff dashboard — live orders, alerts, arrivals."""
    await websocket.accept()
    if kitchen_id not in kitchen_connections:
        kitchen_connections[kitchen_id] = set()
    kitchen_connections[kitchen_id].add(websocket)
    try:
        await websocket.send_json({
            "type": "CONNECTED",
            "kitchen_id": kitchen_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "PING":
                await websocket.send_json({"type": "PONG"})
    except WebSocketDisconnect:
        pass
    finally:
        kitchen_connections[kitchen_id].discard(websocket)
        if not kitchen_connections[kitchen_id]:
            del kitchen_connections[kitchen_id]
