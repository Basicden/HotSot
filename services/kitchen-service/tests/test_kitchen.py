"""HotSot Kitchen Service — Unit Tests."""
import pytest
from app.core.engine import KitchenEngine

engine = KitchenEngine()

def test_enqueue_normal():
    result = engine.enqueue("k_001", "o_001", priority="NORMAL")
    assert result["queue_position"] >= 1
    assert result["queue_name"] == "NORMAL"

def test_enqueue_urgent():
    result = engine.enqueue("k_001", "o_002", priority="URGENT", priority_score=80)
    assert result["queue_name"] == "URGENT"

def test_enqueue_batch():
    result = engine.enqueue("k_001", "o_003", priority="BATCH", priority_score=15)
    assert result["queue_name"] == "BATCH"

def test_dequeue_urgent_first():
    engine.enqueue("k_test", "o_normal", priority="NORMAL", priority_score=40)
    engine.enqueue("k_test", "o_urgent", priority="URGENT", priority_score=85)
    result = engine.dequeue("k_test")
    assert result["order_id"] == "o_urgent"

def test_dequeue_fifo_within_queue():
    engine.enqueue("k_fifo", "o_first", priority="NORMAL", priority_score=40)
    engine.enqueue("k_fifo", "o_second", priority="NORMAL", priority_score=40)
    first = engine.dequeue("k_fifo")
    assert first["order_id"] == "o_first"

def test_empty_queue():
    result = engine.dequeue("k_empty")
    assert result is None

def test_kitchen_status():
    engine.enqueue("k_status", "o_001", priority="NORMAL")
    status = engine.get_status("k_status")
    assert "queue_depth" in status
    assert status["queue_depth"] >= 1
