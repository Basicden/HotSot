"""HotSot Arrival Service — Tests."""

from app.core.geo import haversine_distance, is_within_geofence, classify_proximity


def test_haversine_same_point():
    dist = haversine_distance(28.6139, 77.2090, 28.6139, 77.2090)
    assert dist == 0.0


def test_haversine_known_distance():
    # Delhi to Agra ~175 km
    dist = haversine_distance(28.6139, 77.2090, 27.1767, 78.0081)
    assert 170000 < dist < 185000


def test_geofence_within():
    # Same location
    assert is_within_geofence(28.6139, 77.2090, 28.6139, 77.2090, 150)


def test_geofence_outside():
    # 200+ km away
    assert not is_within_geofence(28.6139, 77.2090, 27.1767, 78.0081, 150)


def test_proximity_classification():
    assert classify_proximity(20) == "IMMEDIATE"
    assert classify_proximity(100) == "NEARBY"
    assert classify_proximity(300) == "APPROACHING"
    assert classify_proximity(600) == "FAR"
