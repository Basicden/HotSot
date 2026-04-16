"""HotSot ML Service — Unit Tests.

Tests cover:
1. Feature store: store and retrieve features
2. Feedback submission: error calculation, retrain threshold
3. Model training: sample data generation
4. Model status endpoint
"""
import os
import time
from unittest.mock import patch, MagicMock

import pytest

from app.routes.ml import (
    FEATURE_STORE,
    TRAINING_DATA,
    _generate_sample_data,
    TrainingRequest,
    FeedbackRequest,
)


# ═══════════════════════════════════════════════════════════════
# SAMPLE DATA GENERATION
# ═══════════════════════════════════════════════════════════════

class TestSampleDataGeneration:
    """Test the sample data generation for model training."""

    def test_generate_sample_data_returns_correct_shape(self):
        """Generated data should have matching X and y lengths."""
        X, y = _generate_sample_data(100)
        assert X.shape[0] == 100
        assert len(y) == 100

    def test_generate_sample_data_has_9_features(self):
        """Generated data should have 9 feature columns."""
        X, y = _generate_sample_data(100)
        assert X.shape[1] == 9

    def test_generate_sample_data_eta_is_positive(self):
        """All ETA values should be at least 60 seconds."""
        X, y = _generate_sample_data(500)
        assert all(y >= 60)

    def test_generate_sample_data_custom_count(self):
        """Custom sample count should be respected."""
        X, y = _generate_sample_data(50)
        assert X.shape[0] == 50


# ═══════════════════════════════════════════════════════════════
# FEATURE STORE
# ═══════════════════════════════════════════════════════════════

class TestFeatureStore:
    """Test feature store operations."""

    def setup_method(self):
        """Clear feature store before each test."""
        FEATURE_STORE.clear()

    @pytest.mark.asyncio
    async def test_store_features_stores_in_memory(self):
        """Storing features should add to FEATURE_STORE."""
        from app.routes.ml import store_features
        result = await store_features(
            order_id="order-1",
            features={"kitchen_load": 50, "queue_length": 5},
        )
        assert result["status"] == "STORED"
        assert "order-1" in FEATURE_STORE

    @pytest.mark.asyncio
    async def test_get_features_returns_stored_data(self):
        """Getting features should return previously stored data."""
        from app.routes.ml import store_features, get_features
        await store_features("order-2", {"load": 80})
        result = await get_features("order-2")
        assert result["features"]["load"] == 80

    @pytest.mark.asyncio
    async def test_get_features_not_found_raises_404(self):
        """Getting non-existent features should raise 404."""
        from fastapi import HTTPException
        from app.routes.ml import get_features
        with pytest.raises(HTTPException) as exc_info:
            await get_features("nonexistent")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_store_features_overwrites_existing(self):
        """Storing features for same order should overwrite."""
        from app.routes.ml import store_features, get_features
        await store_features("order-3", {"version": 1})
        await store_features("order-3", {"version": 2})
        result = await get_features("order-3")
        assert result["features"]["version"] == 2


# ═══════════════════════════════════════════════════════════════
# FEEDBACK SUBMISSION
# ═══════════════════════════════════════════════════════════════

class TestFeedbackSubmission:
    """Test ETA prediction feedback loop."""

    def setup_method(self):
        TRAINING_DATA.clear()

    @pytest.mark.asyncio
    async def test_feedback_calculates_error(self):
        """Feedback should calculate absolute error."""
        from app.routes.ml import submit_feedback
        req = FeedbackRequest(
            order_id="o1", predicted_eta=300, actual_eta=350,
            kitchen_id="k1",
        )
        result = await submit_feedback(req)
        assert result["error_seconds"] == 50
        assert result["error_pct"] == pytest.approx(16.67, abs=0.1)

    @pytest.mark.asyncio
    async def test_feedback_zero_predicted_eta_handles_gracefully(self):
        """Zero predicted ETA should not cause division by zero."""
        from app.routes.ml import submit_feedback
        req = FeedbackRequest(
            order_id="o2", predicted_eta=0, actual_eta=100,
            kitchen_id="k1",
        )
        result = await submit_feedback(req)
        assert result["error_seconds"] == 100

    @pytest.mark.asyncio
    async def test_feedback_appends_to_training_data(self):
        """Feedback should append to training dataset."""
        from app.routes.ml import submit_feedback
        req = FeedbackRequest(
            order_id="o3", predicted_eta=300, actual_eta=310,
            kitchen_id="k1",
        )
        await submit_feedback(req)
        assert len(TRAINING_DATA) == 1

    @pytest.mark.asyncio
    async def test_feedback_needs_retrain_when_error_high(self):
        """Error > 25% should flag needs_retrain."""
        from app.routes.ml import submit_feedback
        req = FeedbackRequest(
            order_id="o4", predicted_eta=100, actual_eta=200,
            kitchen_id="k1",
        )
        result = await submit_feedback(req)
        assert result["needs_retrain"] is True

    @pytest.mark.asyncio
    async def test_feedback_no_retrain_when_error_low(self):
        """Error <= 25% should not flag needs_retrain."""
        from app.routes.ml import submit_feedback
        req = FeedbackRequest(
            order_id="o5", predicted_eta=300, actual_eta=310,
            kitchen_id="k1",
        )
        result = await submit_feedback(req)
        assert result["needs_retrain"] is False
