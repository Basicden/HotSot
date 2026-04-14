"""HotSot Realtime Service — Unit Tests."""
from app.core.connection_manager import ConnectionManager

def test_connection_manager_init():
    cm = ConnectionManager()
    stats = cm.get_stats()
    assert stats["total_connections"] == 0
    assert stats["total_order_connections"] == 0

def test_order_connections_empty():
    cm = ConnectionManager()
    assert cm.get_order_connections("o_001") == 0

def test_kitchen_connections_empty():
    cm = ConnectionManager()
    assert cm.get_kitchen_connections("k_001") == 0

def test_stats_structure():
    cm = ConnectionManager()
    stats = cm.get_stats()
    assert "total_order_connections" in stats
    assert "total_kitchen_connections" in stats
    assert "unique_orders_tracked" in stats
    assert "unique_kitchens_online" in stats
