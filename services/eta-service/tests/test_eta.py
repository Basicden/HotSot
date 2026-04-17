"""HotSot ETA Service — Unit Tests.

Tests cover:
1. ETA predictor: basic prediction, confidence intervals
2. Item complexity calculation
3. Confidence scoring under various conditions
4. Risk level determination
5. Peak hour detection
6. Redis caching behavior
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.core.predictor import (
    ETAPredictor,
    ETAPrediction,
    BASE_PREP_SECONDS,
    QUEUE_DELAY_PER_ORDER,
    LOAD_FACTOR_OVERLOADED,
    DEFAULT_BUFFER,
)


# ═══════════════════════════════════════════════════════════════
# BASIC ETA PREDICTION
# ═══════════════════════════════════════════════════════════════

class TestETAPredictorBasic:
    """Test basic ETA prediction calculations."""

    @pytest.mark.asyncio
    async def test_predict_default_returns_prediction_object(self):
        """Default prediction should return a valid ETAPrediction."""
        predictor = ETAPredictor(redis_client=None)
        result = await predictor.predict(
            order_id="order-1", kitchen_id="kitchen-1",
        )
        assert isinstance(result, ETAPrediction)
        assert result.order_id == "order-1"
        assert result.eta_seconds > 0
        assert result.confidence_score > 0
        assert result.risk_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    @pytest.mark.asyncio
    async def test_predict_base_prep_included(self):
        """Base prep time should be included in ETA."""
        predictor = ETAPredictor(redis_client=None)
        result = await predictor.predict(
            order_id="o1", kitchen_id="k1",
            queue_length=0, kitchen_load_pct=0.0, is_peak=False,
            base_prep_seconds=300,
        )
        assert result.eta_seconds >= 300

    @pytest.mark.asyncio
    async def test_predict_queue_delay_adds_time(self):
        """Each order in queue should add QUEUE_DELAY_PER_ORDER seconds."""
        predictor = ETAPredictor(redis_client=None)
        no_queue = await predictor.predict(
            order_id="o1", kitchen_id="k1", queue_length=0,
            kitchen_load_pct=0, is_peak=False, base_prep_seconds=300,
        )
        with_queue = await predictor.predict(
            order_id="o2", kitchen_id="k1", queue_length=5,
            kitchen_load_pct=0, is_peak=False, base_prep_seconds=300,
        )
        diff = with_queue.eta_seconds - no_queue.eta_seconds
        expected_diff = 5 * QUEUE_DELAY_PER_ORDER
        assert diff == expected_diff

    @pytest.mark.asyncio
    async def test_predict_overloaded_kitchen_load_factor(self):
        """Kitchen at 85%+ load should apply LOAD_FACTOR_OVERLOADED."""
        predictor = ETAPredictor(redis_client=None)
        normal = await predictor.predict(
            order_id="o1", kitchen_id="k1", queue_length=0,
            kitchen_load_pct=50, is_peak=False, base_prep_seconds=300,
        )
        overloaded = await predictor.predict(
            order_id="o2", kitchen_id="k1", queue_length=0,
            kitchen_load_pct=90, is_peak=False, base_prep_seconds=300,
        )
        assert overloaded.eta_seconds > normal.eta_seconds

    @pytest.mark.asyncio
    async def test_predict_peak_hours_adds_time(self):
        """Peak hours should add additional time."""
        predictor = ETAPredictor(redis_client=None)
        non_peak = await predictor.predict(
            order_id="o1", kitchen_id="k1", queue_length=0,
            kitchen_load_pct=0, is_peak=False, base_prep_seconds=300,
        )
        peak = await predictor.predict(
            order_id="o2", kitchen_id="k1", queue_length=0,
            kitchen_load_pct=0, is_peak=True, base_prep_seconds=300,
        )
        assert peak.eta_seconds > non_peak.eta_seconds


# ═══════════════════════════════════════════════════════════════
# ITEM COMPLEXITY
# ═══════════════════════════════════════════════════════════════

class TestItemComplexity:
    """Test _calculate_item_complexity()."""

    def test_empty_items_returns_zero(self):
        """No items should add no complexity."""
        assert ETAPredictor._calculate_item_complexity([]) == 0

    def test_simple_items_no_extra(self):
        """Simple items (not complex) should add no extra time."""
        items = [{"name": "chai"}, {"name": "samosa"}]
        assert ETAPredictor._calculate_item_complexity(items) == 0

    def test_complex_items_add_60_seconds_each(self):
        """Each complex item (biryani, thali, etc.) should add 60 seconds."""
        items = [{"name": "biryani"}, {"name": "dal"}]
        assert ETAPredictor._calculate_item_complexity(items) == 60

    def test_multiple_complex_items(self):
        """Multiple complex items should stack."""
        items = [{"name": "biryani"}, {"name": "thali"}, {"name": "tandoor chicken"}]
        assert ETAPredictor._calculate_item_complexity(items) == 180

    def test_complexity_capped_at_180(self):
        """Complexity extra should be capped at 180 seconds (3 min)."""
        items = [{"name": "biryani"}, {"name": "thali"}, {"name": "tandoor"}, {"name": "kebab"}, {"name": "pizza"}]
        assert ETAPredictor._calculate_item_complexity(items) == 180

    def test_case_insensitive_matching(self):
        """Complex keyword matching should be case-insensitive."""
        items = [{"name": "BIRYANI"}]
        assert ETAPredictor._calculate_item_complexity(items) == 60


# ═══════════════════════════════════════════════════════════════
# CONFIDENCE SCORING
# ═══════════════════════════════════════════════════════════════

class TestConfidenceScoring:
    """Test _calculate_confidence()."""

    def test_ideal_conditions_high_confidence(self):
        """Low load, no queue, off-peak should have high confidence."""
        conf = ETAPredictor._calculate_confidence(0, 0, False, [])
        assert conf >= 0.8

    def test_overloaded_kitchen_reduces_confidence(self):
        """Overloaded kitchen (>=85%) should reduce confidence."""
        normal = ETAPredictor._calculate_confidence(30, 0, False, [])
        overloaded = ETAPredictor._calculate_confidence(90, 0, False, [])
        assert overloaded < normal

    def test_long_queue_reduces_confidence(self):
        """Long queue (>30) should reduce confidence."""
        short_q = ETAPredictor._calculate_confidence(0, 5, False, [])
        long_q = ETAPredictor._calculate_confidence(0, 35, False, [])
        assert long_q < short_q

    def test_peak_reduces_confidence(self):
        """Peak hours should reduce confidence."""
        off_peak = ETAPredictor._calculate_confidence(0, 0, False, [])
        peak = ETAPredictor._calculate_confidence(0, 0, True, [])
        assert peak < off_peak

    def test_confidence_minimum_floor(self):
        """Confidence should never go below 0.3."""
        conf = ETAPredictor._calculate_confidence(100, 50, True, [1, 2, 3, 4, 5, 6])
        assert conf >= 0.3

    def test_confidence_maximum_ceiling(self):
        """Confidence should never exceed 1.0."""
        conf = ETAPredictor._calculate_confidence(0, 0, False, [])
        assert conf <= 1.0


# ═══════════════════════════════════════════════════════════════
# RISK LEVEL
# ═══════════════════════════════════════════════════════════════

class TestRiskLevel:
    """Test _determine_risk()."""

    def test_low_risk_ideal_conditions(self):
        """No load, no queue, off-peak → LOW risk."""
        assert ETAPredictor._determine_risk(0, 0, False) == "LOW"

    def test_medium_risk_moderate_load(self):
        """Moderate load (60%) → MEDIUM risk."""
        assert ETAPredictor._determine_risk(60, 0, False) == "MEDIUM"

    def test_high_risk_overloaded(self):
        """Overloaded (85%) → HIGH risk."""
        assert ETAPredictor._determine_risk(85, 0, False) == "HIGH"

    def test_critical_risk_overloaded_with_queue(self):
        """Overloaded + long queue → CRITICAL risk."""
        assert ETAPredictor._determine_risk(90, 35, True) == "CRITICAL"

    def test_peak_adds_to_risk(self):
        """Peak hours should escalate risk level."""
        without_peak = ETAPredictor._determine_risk(60, 0, False)
        with_peak = ETAPredictor._determine_risk(60, 0, True)
        risk_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        assert risk_order[with_peak] >= risk_order[without_peak]


# ═══════════════════════════════════════════════════════════════
# REDIS CACHING
# ═══════════════════════════════════════════════════════════════

class TestETACaching:
    """Test ETA caching with Redis."""

    @pytest.mark.asyncio
    async def test_predict_caches_in_redis(self):
        """Prediction should cache result in Redis when available."""
        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        predictor = ETAPredictor(redis_client=mock_redis)
        await predictor.predict(order_id="o1", kitchen_id="k1")
        mock_redis.client.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_predict_without_redis_no_error(self):
        """Prediction should work without Redis (just no caching)."""
        predictor = ETAPredictor(redis_client=None)
        result = await predictor.predict(order_id="o1", kitchen_id="k1")
        assert result.eta_seconds > 0

    @pytest.mark.asyncio
    async def test_get_cached_prediction_returns_cached(self):
        """get_cached_prediction should return cached data."""
        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        cached = {"eta_seconds": 400, "risk_level": "LOW", "confidence": 0.85}
        mock_redis.client.get.return_value = json.dumps(cached)
        predictor = ETAPredictor(redis_client=mock_redis)
        result = await predictor.get_cached_prediction("k1", "o1")
        assert result is not None
        assert result["eta_seconds"] == 400

    @pytest.mark.asyncio
    async def test_get_cached_prediction_cache_miss_returns_none(self):
        """Cache miss should return None."""
        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.get.return_value = None
        predictor = ETAPredictor(redis_client=mock_redis)
        result = await predictor.get_cached_prediction("k1", "o1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_cached_prediction_no_redis_returns_none(self):
        """No Redis should return None (cache miss)."""
        predictor = ETAPredictor(redis_client=None)
        result = await predictor.get_cached_prediction("k1", "o1")
        assert result is None
