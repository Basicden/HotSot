"""HotSot Shelf Service — Unit Tests."""
from app.core.ttl_engine import TTLEngine, ShelfSlot

def test_register_shelf():
    eng = TTLEngine()
    eng.register_shelf("A1", "k_001", "HOT")
    assert "A1" in eng.shelves
    assert eng.shelves["A1"].temperature_zone == "HOT"
    assert eng.shelves["A1"].ttl_seconds == 600

def test_assign_order():
    eng = TTLEngine()
    eng.register_shelf("A1", "k_001", "HOT")
    assert eng.assign_order("A1", "o_001") is True
    assert eng.shelves["A1"].status == "OCCUPIED"
    assert eng.shelves["A1"].current_order_id == "o_001"

def test_assign_occupied_shelf():
    eng = TTLEngine()
    eng.register_shelf("A1", "k_001", "HOT")
    eng.assign_order("A1", "o_001")
    assert eng.assign_order("A1", "o_002") is False

def test_release_shelf():
    eng = TTLEngine()
    eng.register_shelf("A1", "k_001", "HOT")
    eng.assign_order("A1", "o_001")
    order_id = eng.release_shelf("A1")
    assert order_id == "o_001"
    assert eng.shelves["A1"].status == "AVAILABLE"

def test_find_available():
    eng = TTLEngine()
    eng.register_shelf("A1", "k_001", "HOT")
    eng.register_shelf("A2", "k_001", "HOT")
    eng.assign_order("A1", "o_001")
    found = eng.find_available("k_001", "HOT")
    assert found == "A2"

def test_no_available_shelf():
    eng = TTLEngine()
    eng.register_shelf("A1", "k_001", "HOT")
    eng.assign_order("A1", "o_001")
    assert eng.find_available("k_001", "HOT") is None

def test_ttl_by_zone():
    eng = TTLEngine()
    assert eng.TTL_BY_ZONE["HOT"] == 600
    assert eng.TTL_BY_ZONE["COLD"] == 900
    assert eng.TTL_BY_ZONE["AMBIENT"] == 1200

def test_shelf_slot_remaining_ttl():
    slot = ShelfSlot(shelf_id="A1", kitchen_id="k_001", temperature_zone="HOT", ttl_seconds=600)
    assert slot.remaining_ttl <= 600
    assert slot.is_available is True

def test_expiry_warning():
    eng = TTLEngine()
    eng.register_shelf("A1", "k_001", "HOT")
    eng.assign_order("A1", "o_001")
    # After assignment, should not be expired yet
    warnings = eng.check_expiry_warnings("k_001")
    assert isinstance(warnings, list)
