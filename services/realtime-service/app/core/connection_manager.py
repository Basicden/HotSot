"""HotSot Realtime Service — Connection Manager."""
import json
import logging
from typing import Dict, Set, Optional
from fastapi import WebSocket

logger = logging.getLogger("realtime-service.manager")


class ConnectionManager:
    """Manages WebSocket connections for vendors and SSE for customers.

    Architecture:
    - Vendors (kitchen staff): WebSocket for bidirectional real-time
    - Customers: SSE for unidirectional updates (simpler, more reliable)
    - Redis pub/sub for cross-instance routing
    """

    def __init__(self):
        # kitchen_id -> set of websocket connections
        self._vendor_connections: Dict[str, Set[WebSocket]] = {}
        # user_id -> set of SSE queues
        self._customer_connections: Dict[str, Set] = {}

    async def connect_vendor(self, websocket: WebSocket, kitchen_id: str):
        """Accept and register vendor WebSocket connection."""
        await websocket.accept()
        if kitchen_id not in self._vendor_connections:
            self._vendor_connections[kitchen_id] = set()
        self._vendor_connections[kitchen_id].add(websocket)
        logger.info("vendor_connected", kitchen_id=kitchen_id)

    def disconnect_vendor(self, websocket: WebSocket, kitchen_id: str):
        """Remove vendor WebSocket connection."""
        if kitchen_id in self._vendor_connections:
            self._vendor_connections[kitchen_id].discard(websocket)
            if not self._vendor_connections[kitchen_id]:
                del self._vendor_connections[kitchen_id]
        logger.info("vendor_disconnected", kitchen_id=kitchen_id)

    async def send_to_kitchen(self, kitchen_id: str, message: dict):
        """Send message to all connections for a kitchen."""
        connections = self._vendor_connections.get(kitchen_id, set())
        dead = set()
        for ws in connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        # Clean up dead connections
        for ws in dead:
            connections.discard(ws)

    async def register_customer(self, user_id: str, queue):
        """Register customer SSE queue."""
        if user_id not in self._customer_connections:
            self._customer_connections[user_id] = set()
        self._customer_connections[user_id].add(queue)

    def unregister_customer(self, user_id: str, queue):
        """Unregister customer SSE queue."""
        if user_id in self._customer_connections:
            self._customer_connections[user_id].discard(queue)

    async def send_to_customer(self, user_id: str, message: dict):
        """Send update to customer via SSE."""
        queues = self._customer_connections.get(user_id, set())
        for q in queues:
            try:
                await q.put(message)
            except Exception:
                pass

    async def broadcast(self, message: dict, kitchen_id: str = None, user_id: str = None):
        """Broadcast message to relevant connections."""
        if kitchen_id:
            await self.send_to_kitchen(kitchen_id, message)
        if user_id:
            await self.send_to_customer(user_id, message)

    @property
    def vendor_count(self) -> int:
        return sum(len(conns) for conns in self._vendor_connections.values())

    @property
    def customer_count(self) -> int:
        return sum(len(conns) for conns in self._customer_connections.values())


manager = ConnectionManager()
