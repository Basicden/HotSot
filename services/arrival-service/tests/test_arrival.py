"""HotSot Arrival Service — Unit Tests.

Tests cover:
1. Geofence: haversine distance, geofence check, proximity classification
2. Deduplication: duplicate detection, idempotency, Redis fail-closed
3. Arrival detection: QR scan, GPS validation
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import RedisError

from app.core.geo import haversine_distance, is_within_geofence, classify_proximity
from app.core.dedup import ArrivalDeduplicator


# ═══════════════════════════════════════════════════════════════
# HAVERSINE DISTANCE
# ═══════════════════════════════════════════════════════════════

class TestHaversineDistance:
    """Test haversine distance calculations."""

    def test_same_point_returns_zero(self):
        """Distance between identical points should be 0."""
        assert haversine_distance(12.9716, 77.5946, 12.9716, 77.5946) == 0.0

    def test_known_distance_bangalore(self):
        """Test distance between two known Bangalore points (~1km)."""
        # MG Road: 12.9757, 77.6063 → Indiranagar: 12.9784, 77.6408
        dist = haversine_distance(12.9757, 77.6063, 12.9784, 77.6408)
        assert 3000 < dist < 5000  # ~3.8 km

    def test_opposite_sides_large_distance(self):
        """Distance between distant points should be large."""
        # Delhi: 28.6139, 77.2090 → Bangalore: 12.9716, 77.5946
        dist = haversine_distance(28.6139, 77.2090, 12.9716, 77.5946)
        assert dist > 1_500_000  # > 1500 km

    def test_small_distance_within_meters(self):
        """Nearby points should return small distances."""
        # Two points ~50m apart
        dist = haversine_distance(12.9716, 77.5946, 12.9716 + 0.0005, 77.5946)
        assert 30 < dist < 100


# ═══════════════════════════════════════════════════════════════
# GEOFENCE
# ═══════════════════════════════════════════════════════════════

class TestGeofence:
    """Test geofence radius checking."""

    def test_within_150m_returns_true(self):
        """User within 150m should pass geofence check."""
        # Same point → distance 0
        assert is_within_geofence(12.9716, 77.5946, 12.9716, 77.5946) is True

    def test_beyond_150m_returns_false(self):
        """User beyond 150m should fail geofence check."""
        # Points ~1km apart
        assert is_within_geofence(12.9716, 77.5946, 12.9757, 77.6063) is False

    def test_custom_radius_respected(self):
        """Custom radius should be respected."""
        # Points ~500m apart
        assert is_within_geofence(12.9716, 77.5946, 12.9757, 77.6063, radius_m=5000) is True


# ═══════════════════════════════════════════════════════════════
# PROXIMITY CLASSIFICATION
# ═══════════════════════════════════════════════════════════════

class TestProximityClassification:
    """Test proximity level classification."""

    def test_immediate_under_50m(self):
        assert classify_proximity(30) == "IMMEDIATE"

    def test_nearby_under_150m(self):
        assert classify_proximity(100) == "NEARBY"

    def test_approaching_under_500m(self):
        assert classify_proximity(300) == "APPROACHING"

    def test_far_over_500m(self):
        assert classify_proximity(600) == "FAR"

    def test_zero_distance_immediate(self):
        assert classify_proximity(0) == "IMMEDIATE"

    def test_boundary_at_50m_nearby(self):
        """At exactly 50m should be NEARBY (>=50 not <50)."""
        assert classify_proximity(50) == "NEARBY"

    def test_boundary_at_150m_approaching(self):
        assert classify_proximity(150) == "APPROACHING"

    def test_boundary_at_500m_far(self):
        assert classify_proximity(500) == "FAR"


# ═══════════════════════════════════════════════════════════════
# ARRIVAL DEDUPLICATION
# ═══════════════════════════════════════════════════════════════

class TestArrivalDedup:
    """Test arrival deduplication engine."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.client = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_is_duplicate_no_redis_returns_true(self):
        """Without Redis, should treat as duplicate (fail-closed)."""
        dedup = ArrivalDeduplicator(redis_client=None)
        result = await dedup.is_duplicate("u1", "o1", time.time(), "t1")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_duplicate_new_arrival_returns_false(self, mock_redis):
        """First arrival in time bucket should not be duplicate."""
        mock_redis.client.exists.return_value = False
        dedup = ArrivalDeduplicator(redis_client=mock_redis)
        result = await dedup.is_duplicate("u1", "o1", time.time(), "t1")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_duplicate_repeat_returns_true(self, mock_redis):
        """Repeated arrival in same time bucket should be duplicate."""
        mock_redis.client.exists.return_value = True
        dedup = ArrivalDeduplicator(redis_client=mock_redis)
        result = await dedup.is_duplicate("u1", "o1", time.time(), "t1")
        assert result is True

    @pytest.mark.asyncio
    async def test_is_duplicate_redis_error_returns_true(self, mock_redis):
        """Redis errors should treat as duplicate (fail-closed)."""
        mock_redis.client.exists.side_effect = RedisError("Down")
        dedup = ArrivalDeduplicator(redis_client=mock_redis)
        result = await dedup.is_duplicate("u1", "o1", time.time(), "t1")
        assert result is True

    @pytest.mark.asyncio
    async def test_check_idempotency_no_redis_raises(self):
        """Without Redis, idempotency check should raise RuntimeError."""
        dedup = ArrivalDeduplicator(redis_client=None)
        with pytest.raises(RuntimeError):
            await dedup.check_idempotency_key("key-1", "t1")

    @pytest.mark.asyncio
    async def test_check_idempotency_key_found(self, mock_redis):
        """Existing idempotency key should return cached response."""
        mock_redis.client.get.return_value = '{"status": "ok"}'
        dedup = ArrivalDeduplicator(redis_client=mock_redis)
        result = await dedup.check_idempotency_key("key-1", "t1")
        assert result is not None

    @pytest.mark.asyncio
    async def test_check_idempotency_key_not_found(self, mock_redis):
        """Non-existent idempotency key should return None."""
        mock_redis.client.get.return_value = None
        dedup = ArrivalDeduplicator(redis_client=mock_redis)
        result = await dedup.check_idempotency_key("key-1", "t1")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_idempotency_no_redis_raises(self):
        """Without Redis, storing idempotency should raise RuntimeError."""
        dedup = ArrivalDeduplicator(redis_client=None)
        with pytest.raises(RuntimeError):
            await dedup.store_idempotency_key("key-1", "{}", "t1")

    @pytest.mark.asyncio
    async def test_store_idempotency_succeeds(self, mock_redis):
        """Storing idempotency key should call Redis setex."""
        dedup = ArrivalDeduplicator(redis_client=mock_redis)
        await dedup.store_idempotency_key("key-1", '{"ok": true}', "t1")
        mock_redis.client.setex.assert_called_once()
