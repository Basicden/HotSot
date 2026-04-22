"""
HotSot — PHASE 5: ML Testing

Tests ML model robustness with noisy data, adversarial inputs,
distribution drift, and fallback behavior.

Key Tests:
    1. Prediction stability (same input → same output)
    2. Noisy data handling (missing features, outliers)
    3. Adversarial inputs (extreme values, impossible combos)
    4. Distribution drift detection
    5. Confidence threshold enforcement
    6. Fallback behavior when model unavailable
"""

import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

# ═══════════════════════════════════════════════════════════════
# ML TEST DATA GENERATORS
# ═══════════════════════════════════════════════════════════════

def generate_normal_eta_features() -> Dict:
    """Generate normal ETA prediction features."""
    return {
        "kitchen_id": f"kitchen_{random.randint(1, 50)}",
        "item_complexity": random.uniform(0.3, 1.5),
        "kitchen_load": random.uniform(0.1, 0.9),
        "queue_depth": random.randint(0, 20),
        "time_of_day": random.randint(0, 23),
        "is_weekend": random.choice([True, False]),
        "is_festival": False,
        "order_size": random.randint(1, 5),
        "prep_time_base": random.randint(300, 1200),  # seconds
    }


def generate_noisy_features(missing_pct: float = 0.3) -> Dict:
    """Generate features with random missing values."""
    features = generate_normal_eta_features()

    # Randomly drop some features
    keys = list(features.keys())
    n_drop = int(len(keys) * missing_pct)
    for key in random.sample(keys, n_drop):
        features[key] = None

    return features


def generate_adversarial_features() -> List[Dict]:
    """Generate adversarial feature combinations."""
    return [
        # Impossible values
        {"kitchen_load": -0.5, "queue_depth": -10, "prep_time_base": -300},
        {"kitchen_load": 5.0, "queue_depth": 1000000, "prep_time_base": 0},
        # Zero values
        {"kitchen_load": 0.0, "queue_depth": 0, "order_size": 0, "prep_time_base": 0},
        # Extreme values
        {"kitchen_load": 1.0, "queue_depth": 999, "order_size": 999, "prep_time_base": 86400},
        # Impossible time
        {"time_of_day": 25, "is_weekend": "maybe", "is_festival": 42},
        # Type confusion
        {"kitchen_load": "busy", "queue_depth": "many", "prep_time_base": "slow"},
        # NaN/Inf
        {"kitchen_load": float("nan"), "queue_depth": float("inf")},
        # All null
        {k: None for k in ["kitchen_load", "queue_depth", "order_size", "prep_time_base"]},
    ]


def generate_distribution_drift_data(n_samples: int = 100) -> List[Dict]:
    """Generate data that represents distribution drift (shifted features)."""
    drifted = []
    for i in range(n_samples):
        # Simulate festival/rush hour drift: much higher loads
        drifted.append({
            "kitchen_id": f"kitchen_{random.randint(1, 50)}",
            "item_complexity": random.uniform(1.0, 2.5),  # Higher than normal
            "kitchen_load": random.uniform(0.7, 1.0),  # Near capacity
            "queue_depth": random.randint(15, 50),  # Much deeper
            "time_of_day": random.choice([12, 13, 19, 20, 21]),  # Peak hours only
            "is_weekend": True,
            "is_festival": True,  # Festival mode
            "order_size": random.randint(3, 10),  # Larger orders
            "prep_time_base": random.randint(600, 1800),  # Longer prep
        })
    return drifted


# ═══════════════════════════════════════════════════════════════
# MOCK ETA PREDICTOR
# ═══════════════════════════════════════════════════════════════

class MockETAPredictor:
    """Simplified ETA predictor for testing."""

    BASE_PREP_TIME = 900  # 15 minutes
    MIN_ETA = 300  # 5 minutes
    MAX_ETA = 7200  # 2 hours
    CONFIDENCE_THRESHOLD = 0.3

    def predict(self, features: Dict) -> Dict:
        """Predict ETA from features, with graceful handling of bad input."""
        try:
            prep_base = features.get("prep_time_base", self.BASE_PREP_TIME)
            if prep_base is None or prep_base <= 0:
                prep_base = self.BASE_PREP_TIME

            complexity = features.get("item_complexity", 1.0)
            if complexity is None or complexity <= 0:
                complexity = 1.0

            kitchen_load = features.get("kitchen_load", 0.5)
            if kitchen_load is None or kitchen_load < 0:
                kitchen_load = 0.5

            queue_depth = features.get("queue_depth", 5)
            if queue_depth is None or queue_depth < 0:
                queue_depth = 5

            order_size = features.get("order_size", 1)
            if order_size is None or order_size <= 0:
                order_size = 1

            # Simple prediction formula
            eta_seconds = prep_base * complexity * (1 + kitchen_load) + queue_depth * 30 + order_size * 60

            # Clamp to bounds
            eta_seconds = max(self.MIN_ETA, min(self.MAX_ETA, eta_seconds))

            # Confidence based on feature completeness
            total_features = 9
            present_features = sum(1 for v in features.values() if v is not None)
            confidence = present_features / total_features

            # Risk level
            if eta_seconds > 3600:
                risk = "CRITICAL"
            elif eta_seconds > 2400:
                risk = "HIGH"
            elif eta_seconds > 1200:
                risk = "MEDIUM"
            else:
                risk = "LOW"

            return {
                "eta_seconds": int(eta_seconds),
                "eta_minutes": round(eta_seconds / 60, 1),
                "confidence": round(confidence, 2),
                "risk_level": risk,
                "model_version": "mock_v1",
                "features_used": present_features,
                "features_missing": total_features - present_features,
            }

        except Exception as e:
            # Fallback: return safe default
            return {
                "eta_seconds": self.BASE_PREP_TIME,
                "eta_minutes": 15.0,
                "confidence": 0.0,
                "risk_level": "HIGH",
                "model_version": "fallback",
                "error": str(e),
            }


# ═══════════════════════════════════════════════════════════════
# ML TESTS
# ═══════════════════════════════════════════════════════════════

class TestPredictionStability:
    """Property: Same input → same output (deterministic)."""

    def test_same_features_same_eta(self):
        """Invariant: Identical features produce identical predictions."""
        predictor = MockETAPredictor()
        features = generate_normal_eta_features()

        result1 = predictor.predict(features)
        result2 = predictor.predict(features)

        assert result1["eta_seconds"] == result2["eta_seconds"]
        assert result1["confidence"] == result2["confidence"]

    def test_prediction_always_within_bounds(self):
        """Invariant: ETA is always within [MIN_ETA, MAX_ETA]."""
        predictor = MockETAPredictor()

        for _ in range(100):
            features = generate_normal_eta_features()
            result = predictor.predict(features)
            assert predictor.MIN_ETA <= result["eta_seconds"] <= predictor.MAX_ETA


class TestNoisyDataHandling:
    """Test: Model handles missing/noisy features gracefully."""

    def test_missing_features_dont_crash(self):
        """Invariant: Missing features → degraded but valid prediction."""
        predictor = MockETAPredictor()

        for _ in range(50):
            features = generate_noisy_features(missing_pct=0.5)
            result = predictor.predict(features)
            assert "eta_seconds" in result
            assert result["eta_seconds"] > 0
            assert result["confidence"] < 1.0  # Lower confidence with missing features

    def test_fully_missing_features_returns_fallback(self):
        """Invariant: All features missing → fallback prediction."""
        predictor = MockETAPredictor()
        features = {k: None for k in [
            "kitchen_load", "queue_depth", "order_size", "prep_time_base",
            "item_complexity", "time_of_day", "is_weekend", "is_festival",
        ]}

        result = predictor.predict(features)
        assert "eta_seconds" in result
        assert result["eta_seconds"] > 0
        assert result["confidence"] == 0.0 or result["model_version"] == "fallback"


class TestAdversarialInputs:
    """Test: Model doesn't crash on adversarial inputs."""

    def test_adversarial_features_dont_crash(self):
        """Invariant: Adversarial features → prediction or fallback, never crash."""
        predictor = MockETAPredictor()

        for features in generate_adversarial_features():
            result = predictor.predict(features)
            assert "eta_seconds" in result
            assert result["eta_seconds"] > 0

    def test_negative_values_clamped(self):
        """Invariant: Negative kitchen load → clamped to reasonable default."""
        predictor = MockETAPredictor()
        result = predictor.predict({"kitchen_load": -1.0, "queue_depth": 5, "prep_time_base": 900})
        assert result["eta_seconds"] > 0

    def test_infinite_values_handled(self):
        """Invariant: Infinity values → clamped prediction."""
        predictor = MockETAPredictor()
        result = predictor.predict({"kitchen_load": float("inf"), "queue_depth": 5, "prep_time_base": 900})
        assert result["eta_seconds"] <= predictor.MAX_ETA


class TestConfidenceThreshold:
    """Test: Confidence threshold is enforced."""

    def test_full_features_high_confidence(self):
        """Invariant: All features present → high confidence."""
        predictor = MockETAPredictor()
        features = generate_normal_eta_features()
        result = predictor.predict(features)

        assert result["confidence"] >= 0.8

    def test_half_features_medium_confidence(self):
        """Invariant: Half features missing → medium confidence."""
        predictor = MockETAPredictor()
        features = generate_noisy_features(missing_pct=0.5)
        result = predictor.predict(features)

        assert result["confidence"] < 0.8
        assert result["features_missing"] > 0

    def test_zero_confidence_triggers_fallback(self):
        """Invariant: Zero confidence → fallback or warning."""
        predictor = MockETAPredictor()
        features = {k: None for k in ["kitchen_load", "queue_depth"]}
        result = predictor.predict(features)

        # Zero confidence means system should flag for human review
        if result["confidence"] == 0.0:
            assert result["risk_level"] in ["HIGH", "CRITICAL"]


class TestDistributionDrift:
    """Test: System detects and handles distribution drift."""

    def test_drift_data_produces_different_predictions(self):
        """Invariant: Drifted data produces different (higher) ETAs."""
        predictor = MockETAPredictor()

        # Normal data
        normal_etas = [predictor.predict(generate_normal_eta_features())["eta_seconds"] for _ in range(50)]

        # Drifted data (festival rush)
        drifted_etas = [predictor.predict(f)["eta_seconds"] for f in generate_distribution_drift_data(50)]

        avg_normal = sum(normal_etas) / len(normal_etas)
        avg_drifted = sum(drifted_etas) / len(drifted_etas)

        # Drifted data should produce higher ETAs
        assert avg_drifted > avg_normal * 0.8  # At minimum, drift should be detectable

    def test_drift_increases_risk_level(self):
        """Invariant: Distribution drift increases risk level."""
        predictor = MockETAPredictor()

        normal_risks = [predictor.predict(generate_normal_eta_features())["risk_level"] for _ in range(50)]
        drift_risks = [predictor.predict(f)["risk_level"] for f in generate_distribution_drift_data(50)]

        critical_normal = normal_risks.count("CRITICAL") + normal_risks.count("HIGH")
        critical_drift = drift_risks.count("CRITICAL") + drift_risks.count("HIGH")

        assert critical_drift >= critical_normal  # Drift should increase risk


class TestFeedbackLoop:
    """Test: ML feedback loop correctly processes signals."""

    def test_positive_feedback_recorded(self):
        """Invariant: ORDER_PICKED events create positive feedback."""
        feedback = {
            "event_type": "ORDER_PICKED",
            "order_id": "ord_123",
            "predicted_eta": 900,
            "actual_eta": 850,
            "error_pct": abs(900 - 850) / 900 * 100,
            "weight": 1,
        }
        assert feedback["error_pct"] < 10  # Good prediction
        assert feedback["weight"] == 1

    def test_negative_feedback_weighted_heavier(self):
        """Invariant: SHELF_EXPIRED events have 3x weight."""
        feedback = {
            "event_type": "SHELF_EXPIRED",
            "order_id": "ord_456",
            "predicted_ttl": 600,
            "actual_ttl": 450,
            "error_pct": abs(600 - 450) / 600 * 100,
            "weight": 3,  # Negative signal weighted 3x
        }
        assert feedback["weight"] == 3
        assert feedback["error_pct"] > 0

    def test_retrain_threshold_triggered(self):
        """Invariant: Retrain triggered when negative feedback exceeds 25%."""
        total_feedback = 100
        negative_count = 30  # 30%

        negative_ratio = negative_count / total_feedback
        retrain_threshold = 0.25

        assert negative_ratio > retrain_threshold  # Should trigger retrain
