"""HotSot Realtime Service — Unit Tests.

Tests cover:
1. ConnectionManager: vendor connect/disconnect, customer register/unregister
2. ConnectionManager: send_to_kitchen, send_to_customer, broadcast
3. ConnectionManager: dead connection cleanup
4. ConnectionManager: connection counts
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.connection_manager import ConnectionManager


# ═══════════════════════════════════════════════════════════════
# VENDOR WEBSOCKET CONNECTIONS
# ═══════════════════════════════════════════════════════════════

class TestVendorConnections:
    """Test vendor WebSocket connection management."""

    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_connect_vendor_accepts_and_registers(self, manager, mock_ws):
        """Connecting vendor should accept WebSocket and register."""
        await manager.connect_vendor(mock_ws, "kitchen-1")
        mock_ws.accept.assert_called_once()
        assert "kitchen-1" in manager._vendor_connections
        assert mock_ws in manager._vendor_connections["kitchen-1"]

    @pytest.mark.asyncio
    async def test_disconnect_vendor_removes_connection(self, manager, mock_ws):
        """Disconnecting vendor should remove the WebSocket."""
        await manager.connect_vendor(mock_ws, "kitchen-1")
        manager.disconnect_vendor(mock_ws, "kitchen-1")
        assert "kitchen-1" not in manager._vendor_connections

    @pytest.mark.asyncio
    async def test_multiple_vendors_same_kitchen(self, manager):
        """Multiple connections for same kitchen should be tracked."""
        ws1, ws2 = AsyncMock(), AsyncMock()
        ws1.accept = AsyncMock()
        ws2.accept = AsyncMock()
        await manager.connect_vendor(ws1, "kitchen-1")
        await manager.connect_vendor(ws2, "kitchen-1")
        assert len(manager._vendor_connections["kitchen-1"]) == 2

    @pytest.mark.asyncio
    async def test_disconnect_one_of_multiple_vendors(self, manager):
        """Disconnecting one vendor should not affect others."""
        ws1, ws2 = AsyncMock(), AsyncMock()
        ws1.accept = AsyncMock()
        ws2.accept = AsyncMock()
        await manager.connect_vendor(ws1, "kitchen-1")
        await manager.connect_vendor(ws2, "kitchen-1")
        manager.disconnect_vendor(ws1, "kitchen-1")
        assert len(manager._vendor_connections["kitchen-1"]) == 1
        assert ws2 in manager._vendor_connections["kitchen-1"]

    def test_disconnect_nonexistent_kitchen_no_error(self, manager):
        """Disconnecting from nonexistent kitchen should not raise."""
        ws = MagicMock()
        manager.disconnect_vendor(ws, "nonexistent")

    @pytest.mark.asyncio
    async def test_send_to_kitchen_delivers_to_all(self, manager):
        """send_to_kitchen should deliver message to all connections."""
        ws1, ws2 = AsyncMock(), AsyncMock()
        ws1.accept = AsyncMock()
        ws2.accept = AsyncMock()
        ws1.send_json = AsyncMock()
        ws2.send_json = AsyncMock()
        await manager.connect_vendor(ws1, "kitchen-1")
        await manager.connect_vendor(ws2, "kitchen-1")
        message = {"type": "NEW_ORDER", "order_id": "o1"}
        await manager.send_to_kitchen("kitchen-1", message)
        ws1.send_json.assert_called_once_with(message)
        ws2.send_json.assert_called_once_with(message)

    @pytest.mark.asyncio
    async def test_send_to_kitchen_removes_dead_connections(self, manager):
        """Dead connections should be cleaned up during send."""
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock(side_effect=Exception("Connection closed"))
        await manager.connect_vendor(ws, "kitchen-1")
        await manager.send_to_kitchen("kitchen-1", {"type": "PING"})
        assert len(manager._vendor_connections["kitchen-1"]) == 0

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_kitchen_no_error(self, manager):
        """Sending to nonexistent kitchen should not raise."""
        await manager.send_to_kitchen("nonexistent", {"type": "PING"})


# ═══════════════════════════════════════════════════════════════
# CUSTOMER SSE CONNECTIONS
# ═══════════════════════════════════════════════════════════════

class TestCustomerConnections:
    """Test customer SSE connection management."""

    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_register_customer_adds_queue(self, manager):
        """Registering customer should add their SSE queue."""
        queue = asyncio.Queue()
        await manager.register_customer("user-1", queue)
        assert "user-1" in manager._customer_connections
        assert queue in manager._customer_connections["user-1"]

    @pytest.mark.asyncio
    async def test_unregister_customer_removes_queue(self, manager):
        """Unregistering customer should remove their SSE queue."""
        queue = asyncio.Queue()
        await manager.register_customer("user-1", queue)
        manager.unregister_customer("user-1", queue)
        assert "user-1" not in manager._customer_connections

    @pytest.mark.asyncio
    async def test_send_to_customer_puts_in_queue(self, manager):
        """send_to_customer should put message in the SSE queue."""
        queue = asyncio.Queue()
        await manager.register_customer("user-1", queue)
        message = {"type": "ORDER_UPDATE", "order_id": "o1"}
        await manager.send_to_customer("user-1", message)
        assert queue.qsize() == 1
        assert queue.get_nowait() == message


# ═══════════════════════════════════════════════════════════════
# BROADCAST
# ═══════════════════════════════════════════════════════════════

class TestBroadcast:
    """Test broadcast functionality."""

    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_broadcast_to_kitchen_only(self, manager):
        """Broadcast with kitchen_id should send to kitchen."""
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        await manager.connect_vendor(ws, "kitchen-1")
        await manager.broadcast({"type": "ALERT"}, kitchen_id="kitchen-1")
        ws.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_to_customer_only(self, manager):
        """Broadcast with user_id should send to customer."""
        queue = asyncio.Queue()
        await manager.register_customer("user-1", queue)
        await manager.broadcast({"type": "UPDATE"}, user_id="user-1")
        assert queue.qsize() == 1


# ═══════════════════════════════════════════════════════════════
# CONNECTION COUNTS
# ═══════════════════════════════════════════════════════════════

class TestConnectionCounts:
    """Test connection count properties."""

    @pytest.mark.asyncio
    async def test_vendor_count_empty(self):
        """Empty manager should have 0 vendor connections."""
        manager = ConnectionManager()
        assert manager.vendor_count == 0

    @pytest.mark.asyncio
    async def test_vendor_count_with_connections(self):
        """vendor_count should reflect active connections."""
        manager = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        ws1.accept = AsyncMock()
        ws2.accept = AsyncMock()
        await manager.connect_vendor(ws1, "k1")
        await manager.connect_vendor(ws2, "k1")
        assert manager.vendor_count == 2

    @pytest.mark.asyncio
    async def test_customer_count_with_connections(self):
        """customer_count should reflect active SSE queues."""
        manager = ConnectionManager()
        q1, q2 = asyncio.Queue(), asyncio.Queue()
        await manager.register_customer("u1", q1)
        await manager.register_customer("u2", q2)
        assert manager.customer_count == 2
