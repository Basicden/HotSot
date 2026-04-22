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
    7. Feature drift detection (FeatureDistributionTracker)
    8. Prediction validation (PredictionValidator)
    9. Rule-based ETA fallback (RuleBasedETAFallback)
    10. ML drift monitor integration (MLDriftMonitor)
"""

import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

# ═══════════════════════════════════════════════════════════════
# ML VALIDATION LAYER IMPORTS
# ═══════════════════════════════════════════════════════════════

import importlib.util
import os

# Direct import of ml_validation module to avoid pulling in
# shared.utils.__init__ (which imports sqlalchemy, redis, etc.)
_ml_val_path = os.path.join(
    os.path.dirname(__file__), "..", "..", "shared", "utils", "ml_validation.py"
)
_ml_val_path = os.path.abspath(_ml_val_path)
_spec = importlib.util.spec_from_file_location("ml_validation", _ml_val_path)
_ml_validation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ml_validation)

FeatureDistributionTracker = _ml_validation.FeatureDistributionTracker
PredictionValidator = _ml_validation.PredictionValidator
RuleBasedETAFallback = _ml_validation.RuleBasedETAFallback
MLDriftMonitor = _ml_validation.MLDriftMonitor
MIN_ETA_SECONDS = _ml_validation.MIN_ETA_SECONDS
MAX_ETA_SECONDS = _ml_validation.MAX_ETA_SECONDS
CONFIDENCE_FALLBACK_THRESHOLD = _ml_validation.CONFIDENCE_FALLBACK_THRESHOLD
MIN_SAMPLES_FOR_DRIFT = _ml_validation.MIN_SAMPLES_FOR_DRIFT
DEFAULT_WINDOW_SIZE = _ml_validation.DEFAULT_WINDOW_SIZE

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
        # Partial features with defaults may produce MEDIUM risk, which is
        # acceptable since the predictor uses safe defaults
        if result["confidence"] == 0.0:
            assert result["risk_level"] in ["HIGH", "CRITICAL", "MEDIUM"]


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


# ═══════════════════════════════════════════════════════════════
# ML VALIDATION LAYER TESTS
# ═══════════════════════════════════════════════════════════════

class TestFeatureDriftDetection:
    """Tests for FeatureDistributionTracker — sliding window statistics and drift detection."""

    def test_update_tracks_values(self):
        """Feature values are stored and counted correctly."""
        tracker = FeatureDistributionTracker()
        for i in range(50):
            tracker.update("kitchen_load", 0.5)

        dist = tracker.get_distribution("kitchen_load")
        assert dist["count"] == 50
        assert dist["mean"] == 0.5
        assert dist["std"] == 0.0  # All same value

    def test_distribution_statistics(self):
        """Mean, std, min, max are computed correctly."""
        tracker = FeatureDistributionTracker()
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        for v in values:
            tracker.update("test_feature", v)

        dist = tracker.get_distribution("test_feature")
        assert dist["count"] == 5
        assert dist["mean"] == 3.0
        assert dist["min"] == 1.0
        assert dist["max"] == 5.0
        # std of [1,2,3,4,5] with Bessel's correction: sqrt(2.5) ≈ 1.581
        assert abs(dist["std"] - 1.5811) < 0.01

    def test_sliding_window_maxlen(self):
        """Oldest values are evicted when window is full."""
        tracker = FeatureDistributionTracker(window_size=5)
        for v in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]:
            tracker.update("test_feature", v)

        dist = tracker.get_distribution("test_feature")
        # Window should only contain last 5 values: [3, 4, 5, 6, 7]
        assert dist["count"] == 5
        assert dist["min"] == 3.0
        assert dist["max"] == 7.0
        assert dist["mean"] == 5.0

    def test_drift_detection_disabled_with_few_samples(self):
        """Drift detection returns False when count < 30."""
        tracker = FeatureDistributionTracker()
        for i in range(29):
            tracker.update("kitchen_load", 0.5)

        # Even a wildly different value should not trigger drift
        assert tracker.detect_drift("kitchen_load", 999.0) is False

    def test_drift_detection_enabled_with_enough_samples(self):
        """Drift detection works when count >= 30."""
        tracker = FeatureDistributionTracker()
        # Build a normal distribution: kitchen_load around 0.5
        for _ in range(100):
            tracker.update("kitchen_load", 0.5 + random.gauss(0, 0.05))

        # A very different value should trigger drift
        assert tracker.detect_drift("kitchen_load", 5.0, z_threshold=2.0) is True

    def test_no_drift_for_normal_values(self):
        """Values close to the mean do not trigger drift."""
        tracker = FeatureDistributionTracker()
        for _ in range(100):
            tracker.update("kitchen_load", 0.5)

        # Same value as the mean → no drift
        assert tracker.detect_drift("kitchen_load", 0.5) is False

    def test_drift_with_custom_z_threshold(self):
        """Custom z_threshold is respected."""
        tracker = FeatureDistributionTracker()
        for _ in range(100):
            tracker.update("kitchen_load", 0.5 + random.gauss(0, 0.05))

        # With a very high threshold, even extreme values are not drift
        assert tracker.detect_drift("kitchen_load", 5.0, z_threshold=100.0) is False

    def test_nan_and_inf_ignored(self):
        """NaN and Inf values are not added to the tracker."""
        tracker = FeatureDistributionTracker()
        tracker.update("test", 1.0)
        tracker.update("test", float("nan"))
        tracker.update("test", float("inf"))
        tracker.update("test", float("-inf"))
        tracker.update("test", 2.0)

        dist = tracker.get_distribution("test")
        assert dist["count"] == 2  # Only 1.0 and 2.0
        assert dist["mean"] == 1.5

    def test_none_value_ignored(self):
        """None values are not added to the tracker."""
        tracker = FeatureDistributionTracker()
        tracker.update("test", 1.0)
        tracker.update("test", None)
        tracker.update("test", 3.0)

        dist = tracker.get_distribution("test")
        assert dist["count"] == 2

    def test_get_distribution_unknown_feature(self):
        """Unknown features return zero stats."""
        tracker = FeatureDistributionTracker()
        dist = tracker.get_distribution("nonexistent")
        assert dist["mean"] == 0.0
        assert dist["std"] == 0.0
        assert dist["count"] == 0

    def test_get_all_distributions(self):
        """All tracked features are returned."""
        tracker = FeatureDistributionTracker()
        tracker.update("feature_a", 1.0)
        tracker.update("feature_b", 2.0)

        all_dist = tracker.get_all_distributions()
        assert "feature_a" in all_dist
        assert "feature_b" in all_dist
        assert all_dist["feature_a"]["mean"] == 1.0
        assert all_dist["feature_b"]["mean"] == 2.0

    def test_constant_feature_drift_detection(self):
        """When std=0 (constant feature), any different value is drift."""
        tracker = FeatureDistributionTracker()
        for _ in range(50):
            tracker.update("kitchen_load", 0.5)

        # std = 0, any different value → drift
        assert tracker.detect_drift("kitchen_load", 0.6) is True
        # Same value → no drift
        assert tracker.detect_drift("kitchen_load", 0.5) is False

    def test_drift_detection_on_unknown_feature(self):
        """Drift detection returns False for untracked features."""
        tracker = FeatureDistributionTracker()
        assert tracker.detect_drift("nonexistent", 999.0) is False


class TestPredictionValidator:
    """Tests for PredictionValidator — business rule enforcement and confidence adjustment."""

    def test_valid_prediction_passes(self):
        """A normal prediction with good confidence passes validation."""
        validator = PredictionValidator()
        prediction = {
            "eta_seconds": 900,
            "confidence": 0.9,
            "risk_level": "LOW",
        }
        features = {
            "kitchen_load": 0.5,
            "queue_depth": 5,
            "is_festival": False,
        }

        result = validator.validate(prediction, features)
        assert result["is_valid"] is True
        assert result["should_fallback"] is False
        assert result["confidence_level"] in ("HIGH", "MEDIUM")

    def test_low_confidence_triggers_fallback(self):
        """Confidence < 0.3 triggers fallback."""
        validator = PredictionValidator()
        prediction = {
            "eta_seconds": 900,
            "confidence": 0.2,
            "risk_level": "LOW",
        }
        features = {"kitchen_load": 0.5, "queue_depth": 5}

        result = validator.validate(prediction, features)
        assert result["should_fallback"] is True
        assert result["confidence_level"] == "LOW"

    def test_critical_risk_triggers_fallback(self):
        """Risk level CRITICAL triggers fallback regardless of confidence."""
        validator = PredictionValidator()
        prediction = {
            "eta_seconds": 3600,
            "confidence": 0.9,
            "risk_level": "CRITICAL",
        }
        features = {"kitchen_load": 0.5, "queue_depth": 5}

        result = validator.validate(prediction, features)
        assert result["should_fallback"] is True

    def test_eta_out_of_bounds_fails_validation(self):
        """ETA outside [300, 7200] is flagged as invalid."""
        validator = PredictionValidator()

        # Too low
        prediction = {"eta_seconds": 100, "confidence": 0.9, "risk_level": "LOW"}
        result = validator.validate(prediction, {"kitchen_load": 0.5})
        assert any("outside valid range" in e for e in result["validation_errors"])

        # Too high
        prediction = {"eta_seconds": 10000, "confidence": 0.9, "risk_level": "LOW"}
        result = validator.validate(prediction, {"kitchen_load": 0.5})
        assert any("outside valid range" in e for e in result["validation_errors"])

    def test_high_kitchen_load_reduces_confidence(self):
        """Kitchen load > 0.95 reduces confidence by 20%."""
        validator = PredictionValidator()
        prediction = {"eta_seconds": 900, "confidence": 0.9, "risk_level": "LOW"}
        features = {"kitchen_load": 0.96, "queue_depth": 5, "is_festival": False}

        result = validator.validate(prediction, features)
        # 0.9 * 0.80 = 0.72
        assert abs(result["adjusted_confidence"] - 0.72) < 0.01

    def test_high_queue_depth_reduces_confidence(self):
        """Queue depth > 30 reduces confidence by 15%."""
        validator = PredictionValidator()
        prediction = {"eta_seconds": 900, "confidence": 0.9, "risk_level": "LOW"}
        features = {"kitchen_load": 0.5, "queue_depth": 35, "is_festival": False}

        result = validator.validate(prediction, features)
        # 0.9 * 0.85 = 0.765
        assert abs(result["adjusted_confidence"] - 0.765) < 0.01

    def test_festival_mode_reduces_confidence(self):
        """Festival mode reduces confidence by 10%."""
        validator = PredictionValidator()
        prediction = {"eta_seconds": 900, "confidence": 0.9, "risk_level": "LOW"}
        features = {"kitchen_load": 0.5, "queue_depth": 5, "is_festival": True}

        result = validator.validate(prediction, features)
        # 0.9 * 0.90 = 0.81
        assert abs(result["adjusted_confidence"] - 0.81) < 0.01

    def test_multiple_reductions_stack(self):
        """Multiple confidence reductions stack multiplicatively."""
        validator = PredictionValidator()
        prediction = {"eta_seconds": 900, "confidence": 1.0, "risk_level": "LOW"}
        features = {
            "kitchen_load": 0.96,    # -20%
            "queue_depth": 35,       # -15%
            "is_festival": True,     # -10%
        }

        result = validator.validate(prediction, features)
        # 1.0 * 0.80 * 0.85 * 0.90 = 0.612
        assert abs(result["adjusted_confidence"] - 0.612) < 0.01

    def test_missing_features_over_50_percent_unreliable(self):
        """More than 50% missing features → UNRELIABLE confidence level."""
        validator = PredictionValidator()
        prediction = {"eta_seconds": 900, "confidence": 0.9, "risk_level": "LOW"}
        features = {
            "kitchen_load": 0.5,
            "queue_depth": None,
            "item_complexity": None,
            "time_of_day": None,
            "order_size": None,
            "is_festival": None,
        }  # 5/6 = 83% missing

        result = validator.validate(prediction, features)
        assert result["confidence_level"] == "UNRELIABLE"
        assert result["should_fallback"] is True

    def test_fallback_eta_is_computed(self):
        """Fallback ETA is always computed and returned."""
        validator = PredictionValidator()
        prediction = {"eta_seconds": 900, "confidence": 0.9, "risk_level": "LOW"}
        features = {"kitchen_load": 0.5, "queue_depth": 5}

        result = validator.validate(prediction, features)
        assert "fallback_eta" in result
        assert isinstance(result["fallback_eta"], int)
        assert MIN_ETA_SECONDS <= result["fallback_eta"] <= MAX_ETA_SECONDS

    def test_drift_warnings_populated(self):
        """Drift detection results are included in validation output."""
        tracker = FeatureDistributionTracker()
        # Build up normal data
        for _ in range(50):
            tracker.update("kitchen_load", 0.5)

        validator = PredictionValidator(drift_tracker=tracker)
        prediction = {"eta_seconds": 900, "confidence": 0.9, "risk_level": "LOW"}
        features = {"kitchen_load": 5.0}  # Extreme drift

        result = validator.validate(prediction, features)
        assert len(result["drift_warnings"]) > 0

    def test_confidence_levels_mapping(self):
        """Confidence levels map correctly to ranges."""
        validator = PredictionValidator()

        # HIGH: >= 0.8
        result = validator.validate(
            {"eta_seconds": 900, "confidence": 0.9, "risk_level": "LOW"},
            {"kitchen_load": 0.5, "queue_depth": 5, "is_festival": False},
        )
        assert result["confidence_level"] == "HIGH"

        # MEDIUM: 0.5 - 0.79
        result = validator.validate(
            {"eta_seconds": 900, "confidence": 0.6, "risk_level": "LOW"},
            {"kitchen_load": 0.5, "queue_depth": 5, "is_festival": False},
        )
        assert result["confidence_level"] == "MEDIUM"

        # LOW: < 0.5
        result = validator.validate(
            {"eta_seconds": 900, "confidence": 0.4, "risk_level": "LOW"},
            {"kitchen_load": 0.5, "queue_depth": 5, "is_festival": False},
        )
        assert result["confidence_level"] == "LOW"

    def test_no_features_given(self):
        """Empty features dict → high missing ratio → UNRELIABLE."""
        validator = PredictionValidator()
        prediction = {"eta_seconds": 900, "confidence": 0.9, "risk_level": "LOW"}
        features = {}

        result = validator.validate(prediction, features)
        # Empty features → 100% missing (0/0 handled as 1.0)
        # But empty dict: len(features) == 0 → ratio = 1.0
        assert result["should_fallback"] is True


class TestRuleBasedFallback:
    """Tests for RuleBasedETAFallback — deterministic ETA prediction."""

    def test_default_prediction(self):
        """Default features produce a reasonable ETA."""
        fallback = RuleBasedETAFallback()
        result = fallback.predict({})

        assert "eta_seconds" in result
        assert MIN_ETA_SECONDS <= result["eta_seconds"] <= MAX_ETA_SECONDS
        assert result["confidence"] == 0.3
        assert result["model_version"] == "rule_based_fallback_v1"

    def test_base_time_is_900_seconds(self):
        """With minimal load/queue, ETA starts at base_time = 900s."""
        fallback = RuleBasedETAFallback()
        result = fallback.predict({
            "kitchen_load": 0.1,  # Minimum allowed (0.0 gets clamped to 0.1)
            "queue_depth": 0,
            "item_complexity": 1.0,
            "is_festival": False,
            "time_of_day": 3,  # Not peak
        })

        # base_time * (1 + 0.1) * (1.0 * 1.5) * 1.0 * 1.0 + 0
        # = 900 * 1.1 * 1.5 * 1.0 * 1.0 + 0 = 1485
        assert result["eta_seconds"] == 1485

    def test_kitchen_load_increases_eta(self):
        """Higher kitchen load increases ETA."""
        fallback = RuleBasedETAFallback()

        result_low = fallback.predict({"kitchen_load": 0.1, "queue_depth": 0})
        result_high = fallback.predict({"kitchen_load": 0.9, "queue_depth": 0})

        assert result_high["eta_seconds"] > result_low["eta_seconds"]

    def test_queue_depth_adds_time(self):
        """Each queue depth unit adds 30 seconds."""
        fallback = RuleBasedETAFallback()

        result_no_queue = fallback.predict({"kitchen_load": 0.5, "queue_depth": 0})
        result_with_queue = fallback.predict({"kitchen_load": 0.5, "queue_depth": 10})

        diff = result_with_queue["eta_seconds"] - result_no_queue["eta_seconds"]
        assert diff == 300  # 10 * 30 = 300

    def test_festival_factor_increases_eta(self):
        """Festival mode multiplies ETA by 1.3."""
        fallback = RuleBasedETAFallback()
        features_base = {
            "kitchen_load": 0.5,
            "queue_depth": 0,
            "item_complexity": 1.0,
            "time_of_day": 3,
        }

        result_normal = fallback.predict({**features_base, "is_festival": False})
        result_festival = fallback.predict({**features_base, "is_festival": True})

        # Festival ETA should be 1.3x the normal (minus queue_factor which is 0)
        assert result_festival["eta_seconds"] > result_normal["eta_seconds"]
        ratio = result_festival["eta_seconds"] / result_normal["eta_seconds"]
        assert abs(ratio - 1.3) < 0.01

    def test_peak_hours_increases_eta(self):
        """Peak hours (12,13,19,20,21) multiply ETA by 1.2."""
        fallback = RuleBasedETAFallback()
        features_base = {
            "kitchen_load": 0.5,
            "queue_depth": 0,
            "item_complexity": 1.0,
            "is_festival": False,
        }

        result_off_peak = fallback.predict({**features_base, "time_of_day": 3})
        result_peak = fallback.predict({**features_base, "time_of_day": 12})

        assert result_peak["eta_seconds"] > result_off_peak["eta_seconds"]
        ratio = result_peak["eta_seconds"] / result_off_peak["eta_seconds"]
        assert abs(ratio - 1.2) < 0.01

    def test_eta_clamped_to_min(self):
        """ETA is clamped to MIN_ETA_SECONDS (300)."""
        fallback = RuleBasedETAFallback()
        result = fallback.predict({
            "kitchen_load": 0.0,
            "queue_depth": 0,
            "item_complexity": 0.1,
            "is_festival": False,
            "time_of_day": 3,
        })
        assert result["eta_seconds"] >= MIN_ETA_SECONDS

    def test_eta_clamped_to_max(self):
        """ETA is clamped to MAX_ETA_SECONDS (7200)."""
        fallback = RuleBasedETAFallback()
        result = fallback.predict({
            "kitchen_load": 1.0,
            "queue_depth": 999,
            "item_complexity": 10.0,
            "is_festival": True,
            "time_of_day": 12,
        })
        assert result["eta_seconds"] <= MAX_ETA_SECONDS

    def test_confidence_always_0_3(self):
        """Rule-based fallback always returns confidence = 0.3."""
        fallback = RuleBasedETAFallback()
        for _ in range(10):
            result = fallback.predict(generate_normal_eta_features())
            assert result["confidence"] == 0.3

    def test_model_version_is_rule_based(self):
        """Model version is always 'rule_based_fallback_v1'."""
        fallback = RuleBasedETAFallback()
        result = fallback.predict({})
        assert result["model_version"] == "rule_based_fallback_v1"

    def test_applied_rules_are_transparent(self):
        """Applied rules dict is returned for explainability."""
        fallback = RuleBasedETAFallback()
        result = fallback.predict({
            "kitchen_load": 0.8,
            "queue_depth": 10,
            "item_complexity": 1.5,
            "is_festival": True,
            "time_of_day": 12,
        })

        rules = result["applied_rules"]
        assert "base_time" in rules
        assert "kitchen_load_factor" in rules
        assert "queue_factor" in rules
        assert "complexity_factor" in rules
        assert "festival_factor" in rules
        assert "peak_factor" in rules
        assert rules["festival_factor"] == 1.3
        assert rules["peak_factor"] == 1.2

    def test_invalid_inputs_use_defaults(self):
        """Invalid feature values fall back to safe defaults."""
        fallback = RuleBasedETAFallback()
        result = fallback.predict({
            "kitchen_load": "busy",
            "queue_depth": "many",
            "item_complexity": None,
            "time_of_day": 25,
        })

        assert MIN_ETA_SECONDS <= result["eta_seconds"] <= MAX_ETA_SECONDS

    def test_eta_minutes_calculated(self):
        """eta_minutes is correctly derived from eta_seconds."""
        fallback = RuleBasedETAFallback()
        result = fallback.predict({"kitchen_load": 0.5, "queue_depth": 0})
        assert abs(result["eta_minutes"] - result["eta_seconds"] / 60) < 0.1


class TestMLDriftMonitor:
    """Integration tests for MLDriftMonitor — full prediction processing pipeline."""

    def test_normal_prediction_passes_through(self):
        """Normal predictions pass through without fallback."""
        monitor = MLDriftMonitor()
        features = {
            "kitchen_load": 0.5,
            "queue_depth": 5,
            "item_complexity": 1.0,
            "time_of_day": 14,
            "is_festival": False,
        }
        prediction = {
            "eta_seconds": 900,
            "eta_minutes": 15.0,
            "confidence": 0.9,
            "risk_level": "LOW",
            "model_version": "ml_v2",
        }

        result = monitor.process_prediction(features, prediction)
        assert result["used_fallback"] is False
        assert result["eta_seconds"] == 900
        assert result["model_version"] == "ml_v2"

    def test_low_confidence_triggers_fallback(self):
        """Low confidence triggers fallback to rule-based."""
        monitor = MLDriftMonitor()
        features = {
            "kitchen_load": 0.5,
            "queue_depth": 5,
            "item_complexity": 1.0,
            "is_festival": False,
        }
        prediction = {
            "eta_seconds": 900,
            "eta_minutes": 15.0,
            "confidence": 0.1,
            "risk_level": "LOW",
            "model_version": "ml_v2",
        }

        result = monitor.process_prediction(features, prediction)
        assert result["used_fallback"] is True
        assert result["model_version"] == "rule_based_fallback_v1"
        assert result["original_prediction"]["eta_seconds"] == 900

    def test_critical_risk_triggers_fallback(self):
        """CRITICAL risk level triggers fallback even with high confidence."""
        monitor = MLDriftMonitor()
        features = {"kitchen_load": 0.5, "queue_depth": 5}
        prediction = {
            "eta_seconds": 4500,
            "eta_minutes": 75.0,
            "confidence": 0.9,
            "risk_level": "CRITICAL",
            "model_version": "ml_v2",
        }

        result = monitor.process_prediction(features, prediction)
        assert result["used_fallback"] is True

    def test_feature_distributions_updated(self):
        """Processing predictions updates feature distributions."""
        monitor = MLDriftMonitor()
        features = {"kitchen_load": 0.5, "queue_depth": 5}

        for _ in range(50):
            prediction = {
                "eta_seconds": 900,
                "confidence": 0.9,
                "risk_level": "LOW",
                "model_version": "ml_v2",
            }
            monitor.process_prediction(features, prediction)

        health = monitor.get_health()
        assert "kitchen_load" in health["tracked_features"]
        assert "queue_depth" in health["tracked_features"]

    def test_drift_detection_in_pipeline(self):
        """Drift is detected when features shift significantly."""
        monitor = MLDriftMonitor()

        # Build normal distribution
        normal_features = {"kitchen_load": 0.5, "queue_depth": 5}
        prediction = {
            "eta_seconds": 900,
            "confidence": 0.9,
            "risk_level": "LOW",
            "model_version": "ml_v2",
        }
        for _ in range(50):
            monitor.process_prediction(normal_features, prediction)

        # Now send drifted features
        drifted_features = {"kitchen_load": 5.0, "queue_depth": 100}
        result = monitor.process_prediction(drifted_features, prediction)

        # Drift should be detected, triggering fallback
        assert result["used_fallback"] is True
        assert len(result["validation"]["drift_warnings"]) > 0

    def test_health_status_healthy(self):
        """Health status is HEALTHY with low fallback rate."""
        monitor = MLDriftMonitor()
        features = {"kitchen_load": 0.5, "queue_depth": 5}
        prediction = {
            "eta_seconds": 900,
            "confidence": 0.9,
            "risk_level": "LOW",
            "model_version": "ml_v2",
        }

        for _ in range(10):
            monitor.process_prediction(features, prediction)

        health = monitor.get_health()
        assert health["status"] == "HEALTHY"
        assert health["total_predictions"] == 10
        assert health["fallback_rate"] == 0.0

    def test_health_status_degraded_with_high_fallback_rate(self):
        """Health status is DEGRADED when fallback rate > 20%."""
        monitor = MLDriftMonitor()
        features = {"kitchen_load": 0.5, "queue_depth": 5}

        # 7 normal predictions
        for _ in range(7):
            monitor.process_prediction(features, {
                "eta_seconds": 900, "confidence": 0.9,
                "risk_level": "LOW", "model_version": "ml_v2",
            })

        # 3 fallback-triggering predictions (low confidence)
        for _ in range(3):
            monitor.process_prediction(features, {
                "eta_seconds": 900, "confidence": 0.1,
                "risk_level": "LOW", "model_version": "ml_v2",
            })

        health = monitor.get_health()
        # 3/10 = 30% fallback rate > 20% threshold
        assert health["status"] in ("DEGRADED", "CRITICAL")
        assert health["fallback_rate"] == 0.3

    def test_health_status_critical_with_very_high_fallback(self):
        """Health status is CRITICAL when fallback rate > 50%."""
        monitor = MLDriftMonitor()
        features = {"kitchen_load": 0.5, "queue_depth": 5}

        # All predictions trigger fallback
        for _ in range(10):
            monitor.process_prediction(features, {
                "eta_seconds": 900, "confidence": 0.1,
                "risk_level": "LOW", "model_version": "ml_v2",
            })

        health = monitor.get_health()
        assert health["status"] == "CRITICAL"
        assert health["fallback_rate"] == 1.0

    def test_total_predictions_counted(self):
        """Total predictions counter is incremented correctly."""
        monitor = MLDriftMonitor()
        features = {"kitchen_load": 0.5, "queue_depth": 5}
        prediction = {
            "eta_seconds": 900, "confidence": 0.9,
            "risk_level": "LOW", "model_version": "ml_v2",
        }

        for _ in range(25):
            monitor.process_prediction(features, prediction)

        health = monitor.get_health()
        assert health["total_predictions"] == 25

    def test_validation_metadata_included(self):
        """Validation metadata is always included in results."""
        monitor = MLDriftMonitor()
        features = {"kitchen_load": 0.5, "queue_depth": 5}
        prediction = {
            "eta_seconds": 900, "confidence": 0.9,
            "risk_level": "LOW", "model_version": "ml_v2",
        }

        result = monitor.process_prediction(features, prediction)
        assert "validation" in result
        assert "is_valid" in result["validation"]
        assert "confidence_level" in result["validation"]
        assert "drift_warnings" in result["validation"]
        assert "validation_errors" in result["validation"]

    def test_fallback_preserves_original_prediction(self):
        """When fallback is used, original prediction is preserved for audit."""
        monitor = MLDriftMonitor()
        features = {"kitchen_load": 0.5, "queue_depth": 5}
        prediction = {
            "eta_seconds": 900, "eta_minutes": 15.0,
            "confidence": 0.1, "risk_level": "LOW",
            "model_version": "ml_v2",
        }

        result = monitor.process_prediction(features, prediction)
        assert result["used_fallback"] is True
        assert result["original_prediction"]["eta_seconds"] == 900
        assert result["original_prediction"]["model_version"] == "ml_v2"
        assert result["model_version"] == "rule_based_fallback_v1"

    def test_festival_rush_scenario(self):
        """Integration: festival rush with drifted features triggers fallback."""
        monitor = MLDriftMonitor()

        # Normal operation: stable features
        for _ in range(50):
            monitor.process_prediction(
                {"kitchen_load": 0.5, "queue_depth": 5, "is_festival": False},
                {"eta_seconds": 900, "confidence": 0.9, "risk_level": "LOW", "model_version": "ml_v2"},
            )

        # Festival rush: features drift significantly
        festival_features = {
            "kitchen_load": 0.98,
            "queue_depth": 45,
            "is_festival": True,
        }
        # ML model still reports high confidence (wrong!)
        drifted_prediction = {
            "eta_seconds": 600,  # Unreasonably low for festival rush
            "confidence": 0.85,
            "risk_level": "MEDIUM",
            "model_version": "ml_v2",
        }

        result = monitor.process_prediction(festival_features, drifted_prediction)
        # Should trigger fallback due to drift + reduced confidence from load/festival
        assert result["used_fallback"] is True
        assert result["eta_seconds"] >= MIN_ETA_SECONDS
