"""HotSot Shelf Service — Tests."""

from app.core.ttl_engine import TTLEngine, SHELF_TTL_SECONDS


def test_ttl_seconds_by_zone():
    assert SHELF_TTL_SECONDS["HOT"] == 600
    assert SHELF_TTL_SECONDS["COLD"] == 900
    assert SHELF_TTL_SECONDS["AMBIENT"] == 1200


def test_warning_levels():
    engine = TTLEngine(None)
    assert engine._get_warning_level(30) == "OK"
    assert engine._get_warning_level(55) == "INFO"
    assert engine._get_warning_level(80) == "WARNING"
    assert engine._get_warning_level(95) == "CRITICAL"
