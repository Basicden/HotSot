"""HotSot Realtime Service — WebSocket Connection Manager.

Manages WebSocket connection lifecycle, connection pools, and message
broadcasting for both order-tracking and kitchen-dashboard clients.

Features:
  - Per-order connection pools (user tracking)
  - Per-kitchen connection pools (staff dashboard)
  - Health-check / heartbeat management
  - Automatic cleanup on disconnect
  - Atomic broadcast with disconnect tracking
  - Connection count metrics
"""

import logging
import time
from typing import Dict, Set, List, Any, Optional
from datetime import datetime
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages all WebSocket connections for the Realtime Service.

    Maintains two connection pools:
      - order_connections: order_id → Set[WebSocket]
      - kitchen_connections: kitchen_id → Set[WebSocket]

    Thread-safety: Designed for single-process async (FastAPI/uvicorn).
    For multi-process, connections are kept local and Redis pub/sub is used
    for cross-instance broadcasting.
    """

    def __init__(self):
        # Order tracking connections: user subscribes to their order
        self.order_connections: Dict[str, Set[WebSocket]] = {}

        # Kitchen dashboard connections: staff subscribes to their kitchen
        self.kitchen_connections: Dict[str, Set[WebSocket]] = {}

        # Metadata for each connection
        self._connection_meta: Dict[int, Dict[str, Any]] = {}

    # ──────────────────────────────────────────
    # Order connections
    # ──────────────────────────────────────────

    async def connect_order(self, order_id: str, websocket: WebSocket) -> None:
        """Accept and register an order-tracking WebSocket connection.

        Args:
            order_id: The order to subscribe to
            websocket: The WebSocket connection
        """
        await websocket.accept()
        if order_id not in self.order_connections:
            self.order_connections[order_id] = set()
        self.order_connections[order_id].add(websocket)
        self._connection_meta[id(websocket)] = {
            "type": "order",
            "order_id": order_id,
            "connected_at": time.time(),
        }
        logger.info(
            "Order WS connected: order=%s, total=%d",
            order_id,
            len(self.order_connections[order_id]),
        )

    def disconnect_order(self, order_id: str, websocket: WebSocket) -> None:
        """Remove an order-tracking WebSocket connection.

        Args:
            order_id: The order being subscribed to
            websocket: The WebSocket connection to remove
        """
        if order_id in self.order_connections:
            self.order_connections[order_id].discard(websocket)
            if not self.order_connections[order_id]:
                del self.order_connections[order_id]
        self._connection_meta.pop(id(websocket), None)
        logger.info("Order WS disconnected: order=%s", order_id)

    async def broadcast_to_order(
        self, order_id: str, event_type: str, payload: Dict[str, Any]
    ) -> Dict[str, int]:
        """Broadcast a message to all connections subscribed to an order.

        Args:
            order_id: Target order
            event_type: Event type string (e.g. 'ORDER_STATUS_CHANGED')
            payload: Event data

        Returns:
            Dict with delivered and failed counts
        """
        connections = self.order_connections.get(order_id, set())
        message = {
            "type": event_type,
            "order_id": order_id,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
        }
        return await self._broadcast(connections, message)

    # ──────────────────────────────────────────
    # Kitchen connections
    # ──────────────────────────────────────────

    async def connect_kitchen(self, kitchen_id: str, websocket: WebSocket) -> None:
        """Accept and register a kitchen-dashboard WebSocket connection.

        Args:
            kitchen_id: The kitchen to subscribe to
            websocket: The WebSocket connection
        """
        await websocket.accept()
        if kitchen_id not in self.kitchen_connections:
            self.kitchen_connections[kitchen_id] = set()
        self.kitchen_connections[kitchen_id].add(websocket)
        self._connection_meta[id(websocket)] = {
            "type": "kitchen",
            "kitchen_id": kitchen_id,
            "connected_at": time.time(),
        }
        logger.info(
            "Kitchen WS connected: kitchen=%s, total=%d",
            kitchen_id,
            len(self.kitchen_connections[kitchen_id]),
        )

    def disconnect_kitchen(self, kitchen_id: str, websocket: WebSocket) -> None:
        """Remove a kitchen-dashboard WebSocket connection."""
        if kitchen_id in self.kitchen_connections:
            self.kitchen_connections[kitchen_id].discard(websocket)
            if not self.kitchen_connections[kitchen_id]:
                del self.kitchen_connections[kitchen_id]
        self._connection_meta.pop(id(websocket), None)
        logger.info("Kitchen WS disconnected: kitchen=%s", kitchen_id)

    async def broadcast_to_kitchen(
        self, kitchen_id: str, event_type: str, payload: Dict[str, Any]
    ) -> Dict[str, int]:
        """Broadcast a message to all connections for a kitchen.

        Args:
            kitchen_id: Target kitchen
            event_type: Event type string (e.g. 'NEW_ORDER', 'ARRIVAL_ALERT')
            payload: Event data

        Returns:
            Dict with delivered and failed counts
        """
        connections = self.kitchen_connections.get(kitchen_id, set())
        message = {
            "type": event_type,
            "kitchen_id": kitchen_id,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
        }
        return await self._broadcast(connections, message)

    # ──────────────────────────────────────────
    # Global broadcast
    # ──────────────────────────────────────────

    async def broadcast_global(
        self, event_type: str, payload: Dict[str, Any]
    ) -> Dict[str, int]:
        """Broadcast a message to ALL connected clients.

        Use sparingly — only for system-wide announcements.
        """
        message = {
            "type": event_type,
            "payload": payload,
            "timestamp": datetime.utcnow().isoformat(),
        }
        total_delivered = 0
        total_failed = 0

        for connections in list(self.order_connections.values()):
            result = await self._broadcast(connections, message)
            total_delivered += result["delivered"]
            total_failed += result["failed"]

        for connections in list(self.kitchen_connections.values()):
            result = await self._broadcast(connections, message)
            total_delivered += result["delivered"]
            total_failed += result["failed"]

        return {"delivered": total_delivered, "failed": total_failed}

    # ──────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────

    async def _broadcast(
        self, connections: Set[WebSocket], message: Dict[str, Any]
    ) -> Dict[str, int]:
        """Send a message to a set of WebSocket connections.

        Tracks disconnected clients and removes them from the pool.

        Returns:
            Dict with 'delivered' and 'failed' counts
        """
        delivered = 0
        disconnected = set()

        for ws in connections:
            try:
                await ws.send_json(message)
                delivered += 1
            except Exception as exc:
                logger.debug("WS send failed: %s", exc)
                disconnected.add(ws)

        # Clean up disconnected
        for ws in disconnected:
            connections.discard(ws)
            self._connection_meta.pop(id(ws), None)

        return {
            "delivered": delivered,
            "failed": len(disconnected),
        }

    # ──────────────────────────────────────────
    # Metrics
    # ──────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return connection statistics."""
        total_order = sum(len(conns) for conns in self.order_connections.values())
        total_kitchen = sum(len(conns) for conns in self.kitchen_connections.values())

        return {
            "total_order_connections": total_order,
            "total_kitchen_connections": total_kitchen,
            "unique_orders_tracked": len(self.order_connections),
            "unique_kitchens_online": len(self.kitchen_connections),
            "total_connections": total_order + total_kitchen,
        }

    def get_order_connections(self, order_id: str) -> int:
        """Return number of active connections for an order."""
        return len(self.order_connections.get(order_id, set()))

    def get_kitchen_connections(self, kitchen_id: str) -> int:
        """Return number of active connections for a kitchen."""
        return len(self.kitchen_connections.get(kitchen_id, set()))

    async def close_all(self) -> None:
        """Close all WebSocket connections (graceful shutdown)."""
        for connections in list(self.order_connections.values()):
            for ws in connections:
                try:
                    await ws.close(code=1001, reason="Server shutting down")
                except Exception:
                    pass
        for connections in list(self.kitchen_connections.values()):
            for ws in connections:
                try:
                    await ws.close(code=1001, reason="Server shutting down")
                except Exception:
                    pass
        self.order_connections.clear()
        self.kitchen_connections.clear()
        self._connection_meta.clear()
