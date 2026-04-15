"""HotSot Realtime Service — WebSocket for Vendors."""
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.core.connection_manager import manager

router = APIRouter()

@router.websocket("/{kitchen_id}")
async def vendor_websocket(websocket: WebSocket, kitchen_id: str, tenant_id: str = Query(default="")):
    """WebSocket endpoint for vendor/kitchen staff real-time updates.

    Receives: kitchen_id in path, tenant_id as query param
    Sends: order events, queue updates, arrival notifications
    """
    await manager.connect_vendor(websocket, kitchen_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Vendor can send commands (e.g., ack order)
            try:
                msg = json.loads(data)
                # Process vendor message (e.g., ACK, status update)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect_vendor(websocket, kitchen_id)
