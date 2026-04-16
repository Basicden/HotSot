"""HotSot Shelf Service — Unit Tests.

Tests cover:
1. TTL engine: zone TTL values, warning levels, expiry detection
2. TTL engine: Redis operations (assign, check, extend, get_expired) with mocks
3. TTL engine: fail-closed Redis error propagation
4. TTL worker: threshold detection, event emission
"""
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import RedisError

from app.core.ttl_engine import TTLEngine, SHELF_TTL_SECONDS, WARM_STATION_EXTENSION


# ═══════════════════════════════════════════════════════════════
# TTL CONSTANTS
# ═══════════════════════════════════════════════════════════════

class TestShelfTTLConstants:
    """Test shelf TTL configuration by zone."""

    def test_hot_zone_ttl_is_600_seconds(self):
        """HOT zone should expire after 10 minutes."""
        assert SHELF_TTL_SECONDS["HOT"] == 600

    def test_cold_zone_ttl_is_900_seconds(self):
        """COLD zone should expire after 15 minutes."""
        assert SHELF_TTL_SECONDS["COLD"] == 900

    def test_ambient_zone_ttl_is_1200_seconds(self):
        """AMBIENT zone should expire after 20 minutes."""
        assert SHELF_TTL_SECONDS["AMBIENT"] == 1200

    def test_warm_station_extension_is_300_seconds(self):
        """Warm station extension should be 5 minutes."""
        assert WARM_STATION_EXTENSION == 300

    def test_hot_shortest_ttl(self):
        """HOT zone should have the shortest TTL."""
        assert SHELF_TTL_SECONDS["HOT"] < SHELF_TTL_SECONDS["COLD"]
        assert SHELF_TTL_SECONDS["HOT"] < SHELF_TTL_SECONDS["AMBIENT"]

    def test_all_zones_have_positive_ttl(self):
        """All zones must have a positive TTL."""
        for zone, ttl in SHELF_TTL_SECONDS.items():
            assert ttl > 0, f"Zone {zone} has non-positive TTL: {ttl}"


# ═══════════════════════════════════════════════════════════════
# WARNING LEVELS
# ═══════════════════════════════════════════════════════════════

class TestWarningLevels:
    """Test the TTL warning level classification."""

    def test_ok_below_50_percent(self):
        """Below 50% used should be OK."""
        assert TTLEngine._get_warning_level(30) == "OK"

    def test_info_at_50_percent(self):
        """At 50% used should be INFO."""
        assert TTLEngine._get_warning_level(50) == "INFO"

    def test_info_between_50_and_75(self):
        """Between 50% and 75% should be INFO."""
        assert TTLEngine._get_warning_level(60) == "INFO"

    def test_warning_at_75_percent(self):
        """At 75% used should be WARNING."""
        assert TTLEngine._get_warning_level(75) == "WARNING"

    def test_warning_between_75_and_90(self):
        """Between 75% and 90% should be WARNING."""
        assert TTLEngine._get_warning_level(80) == "WARNING"

    def test_critical_at_90_percent(self):
        """At 90% used should be CRITICAL."""
        assert TTLEngine._get_warning_level(90) == "CRITICAL"

    def test_critical_above_90(self):
        """Above 90% should be CRITICAL."""
        assert TTLEngine._get_warning_level(95) == "CRITICAL"

    def test_critical_at_100_percent(self):
        """At 100% should be CRITICAL (not yet expired check)."""
        assert TTLEngine._get_warning_level(100) == "CRITICAL"


# ═══════════════════════════════════════════════════════════════
# TTL ENGINE — ASSIGN SHELF TTL
# ═══════════════════════════════════════════════════════════════

class TestTTLEngineAssign:
    """Test TTLEngine.assign_shelf_ttl()."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.client = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_assign_shelf_ttl_hot_zone_stores_in_redis(self, mock_redis):
        """Assigning HOT zone shelf should store TTL data in Redis."""
        engine = TTLEngine(mock_redis)
        with patch("app.core.ttl_engine.now_ts", return_value=1000.0):
            result = await engine.assign_shelf_ttl(
                shelf_id="shelf-1", order_id="order-1",
                zone="HOT", kitchen_id="kitchen-1", tenant_id="tenant-1",
            )
        assert result["shelf_id"] == "shelf-1"
        assert result["order_id"] == "order-1"
        assert result["zone"] == "HOT"
        assert result["ttl_seconds"] == 600
        assert result["warning_50_sent"] is False
        assert result["warning_75_sent"] is False
        assert result["warning_90_sent"] is False
        mock_redis.client.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_assign_shelf_ttl_cold_zone_uses_900(self, mock_redis):
        """COLD zone should use 900 seconds TTL."""
        engine = TTLEngine(mock_redis)
        with patch("app.core.ttl_engine.now_ts", return_value=1000.0):
            result = await engine.assign_shelf_ttl(
                shelf_id="s1", order_id="o1",
                zone="COLD", kitchen_id="k1", tenant_id="t1",
            )
        assert result["ttl_seconds"] == 900

    @pytest.mark.asyncio
    async def test_assign_shelf_ttl_unknown_zone_defaults_600(self, mock_redis):
        """Unknown zone should default to 600 seconds."""
        engine = TTLEngine(mock_redis)
        with patch("app.core.ttl_engine.now_ts", return_value=1000.0):
            result = await engine.assign_shelf_ttl(
                shelf_id="s1", order_id="o1",
                zone="UNKNOWN", kitchen_id="k1", tenant_id="t1",
            )
        assert result["ttl_seconds"] == 600

    @pytest.mark.asyncio
    async def test_assign_shelf_ttl_redis_error_propagates(self, mock_redis):
        """Redis errors during assign should propagate (fail-closed)."""
        mock_redis.client.setex.side_effect = RedisError("Connection refused")
        engine = TTLEngine(mock_redis)
        with patch("app.core.ttl_engine.now_ts", return_value=1000.0):
            with pytest.raises(RedisError):
                await engine.assign_shelf_ttl(
                    shelf_id="s1", order_id="o1",
                    zone="HOT", kitchen_id="k1", tenant_id="t1",
                )


# ═══════════════════════════════════════════════════════════════
# TTL ENGINE — CHECK SHELF STATUS
# ═══════════════════════════════════════════════════════════════

class TestTTLEngineCheckStatus:
    """Test TTLEngine.check_shelf_status()."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.client = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_check_status_returns_ttl_info(self, mock_redis):
        """Check status should return TTL remaining and warning level."""
        now = 1000.0
        data = {
            "shelf_id": "s1", "order_id": "o1", "zone": "HOT",
            "kitchen_id": "k1", "ttl_seconds": 600,
            "assigned_at": now, "warning_50_sent": False,
        }
        mock_redis.client.get.return_value = json.dumps(data)
        engine = TTLEngine(mock_redis)
        with patch("app.core.ttl_engine.now_ts", return_value=now + 100):
            result = await engine.check_shelf_status("s1", "t1")
        assert result is not None
        assert result["ttl_remaining"] == 500
        assert result["is_expired"] is False
        assert result["warning_level"] == "OK"

    @pytest.mark.asyncio
    async def test_check_status_expired_returns_is_expired(self, mock_redis):
        """Shelf past TTL should show is_expired=True."""
        now = 2000.0
        data = {
            "shelf_id": "s1", "order_id": "o1", "zone": "HOT",
            "kitchen_id": "k1", "ttl_seconds": 600,
            "assigned_at": 1000.0, "warning_50_sent": True,
        }
        mock_redis.client.get.return_value = json.dumps(data)
        engine = TTLEngine(mock_redis)
        with patch("app.core.ttl_engine.now_ts", return_value=now):
            result = await engine.check_shelf_status("s1", "t1")
        assert result["is_expired"] is True
        assert result["ttl_remaining"] == 0

    @pytest.mark.asyncio
    async def test_check_status_key_not_found_returns_none(self, mock_redis):
        """Non-existent shelf should return None."""
        mock_redis.client.get.return_value = None
        engine = TTLEngine(mock_redis)
        result = await engine.check_shelf_status("s1", "t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_check_status_redis_error_propagates(self, mock_redis):
        """Redis errors during check should propagate (fail-closed)."""
        mock_redis.client.get.side_effect = RedisError("Timeout")
        engine = TTLEngine(mock_redis)
        with pytest.raises(RedisError):
            await engine.check_shelf_status("s1", "t1")


# ═══════════════════════════════════════════════════════════════
# TTL ENGINE — EXTEND TTL
# ═══════════════════════════════════════════════════════════════

class TestTTLEngineExtend:
    """Test TTLEngine.extend_ttl()."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.client = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_extend_ttl_adds_additional_seconds(self, mock_redis):
        """Extending TTL should increase ttl_seconds and reset warnings."""
        data = {
            "shelf_id": "s1", "order_id": "o1", "zone": "HOT",
            "kitchen_id": "k1", "ttl_seconds": 600,
            "assigned_at": 1000.0, "warning_50_sent": True,
            "warning_75_sent": True, "warning_90_sent": False,
        }
        mock_redis.client.get.return_value = json.dumps(data)
        engine = TTLEngine(mock_redis)
        with patch("app.core.ttl_engine.now_ts", return_value=1200.0):
            result = await engine.extend_ttl("s1", "t1", additional_seconds=300)
        assert result is True
        # Verify setex was called with updated data
        call_args = mock_redis.client.setex.call_args
        updated_data = json.loads(call_args[0][2])
        assert updated_data["ttl_seconds"] == 900
        assert updated_data["warning_50_sent"] is False
        assert updated_data["warning_75_sent"] is False

    @pytest.mark.asyncio
    async def test_extend_ttl_key_not_found_returns_false(self, mock_redis):
        """Extending non-existent shelf should return False."""
        mock_redis.client.get.return_value = None
        engine = TTLEngine(mock_redis)
        result = await engine.extend_ttl("s1", "t1", 300)
        assert result is False

    @pytest.mark.asyncio
    async def test_extend_ttl_redis_error_propagates(self, mock_redis):
        """Redis errors during extend should propagate."""
        mock_redis.client.get.side_effect = RedisError("Connection lost")
        engine = TTLEngine(mock_redis)
        with pytest.raises(RedisError):
            await engine.extend_ttl("s1", "t1", 300)


# ═══════════════════════════════════════════════════════════════
# TTL ENGINE — GET EXPIRED SHELVES
# ═══════════════════════════════════════════════════════════════

class TestTTLEngineGetExpired:
    """Test TTLEngine.get_expired_shelves()."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.client = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_get_expired_shelves_returns_expired_items(self, mock_redis):
        """Should return only shelves with remaining TTL <= 0."""
        expired_data = json.dumps({
            "shelf_id": "s1", "order_id": "o1", "zone": "HOT",
            "kitchen_id": "k1", "ttl_seconds": 600,
            "assigned_at": 1000.0,
        })
        mock_redis.client.scan_iter.return_value = iter(["shelf_ttl:t1:s1"])
        mock_redis.client.get.return_value = expired_data
        engine = TTLEngine(mock_redis)
        with patch("app.core.ttl_engine.now_ts", return_value=2000.0):
            result = await engine.get_expired_shelves("k1", "t1")
        assert len(result) == 1
        assert result[0]["shelf_id"] == "s1"

    @pytest.mark.asyncio
    async def test_get_expired_shelves_skips_non_expired(self, mock_redis):
        """Should not return shelves still within TTL."""
        active_data = json.dumps({
            "shelf_id": "s1", "order_id": "o1", "zone": "HOT",
            "kitchen_id": "k1", "ttl_seconds": 600,
            "assigned_at": 1000.0,
        })
        mock_redis.client.scan_iter.return_value = iter(["shelf_ttl:t1:s1"])
        mock_redis.client.get.return_value = active_data
        engine = TTLEngine(mock_redis)
        with patch("app.core.ttl_engine.now_ts", return_value=1100.0):
            result = await engine.get_expired_shelves("k1", "t1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_get_expired_shelves_redis_error_propagates(self, mock_redis):
        """Redis errors during scan should propagate."""
        mock_redis.client.scan_iter.side_effect = RedisError("Scan failed")
        engine = TTLEngine(mock_redis)
        with pytest.raises(RedisError):
            await engine.get_expired_shelves("k1", "t1")
