"""HotSot Arrival Service — Unit Tests."""
from app.core.geo import haversine_distance
from app.core.dedup import DedupEngine

def test_haversine_same_point():
    dist = haversine_distance(12.9352, 77.6245, 12.9352, 77.6245)
    assert dist == 0.0

def test_haversine_known_distance():
    # Koramangala → Indiranagar ≈ 5 km
    dist = haversine_distance(12.9352, 77.6245, 12.9784, 77.6408)
    assert 3000 < dist < 8000  # 3-8 km range

def test_haversine_hard_arrival_threshold():
    # 100m distance — should be within hard arrival
    lat_offset = 0.0009  # ~100m
    dist = haversine_distance(12.9352, 77.6245, 12.9352 + lat_offset, 77.6245)
    assert dist < 200  # Within 150m hard arrival

def test_dedup_engine_first_event():
    de = DedupEngine()
    assert de.is_duplicate("o_001") is False

def test_dedup_engine_second_event():
    de = DedupEngine()
    de.record("o_001")
    assert de.is_duplicate("o_001") is True

def test_dedup_different_orders():
    de = DedupEngine()
    de.record("o_001")
    assert de.is_duplicate("o_002") is False
