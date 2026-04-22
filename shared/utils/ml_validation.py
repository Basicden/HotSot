"""
HotSot Shared Utilities — ML Validation Layer (Drift Detection + Fallback Rules).

Prevents the ML service from producing confident but wrong predictions during
festival rush and other drift scenarios. Provides:

    1. FeatureDistributionTracker — Tracks feature statistics over a sliding
       window and detects distribution drift via z-score analysis.
    2. PredictionValidator — Validates ETA predictions against business rules,
       enforcing confidence thresholds and triggering fallback when needed.
    3. RuleBasedETAFallback — Deterministic ETA prediction when ML is
       unreliable, based on kitchen load, queue depth, and other factors.
    4. MLDriftMonitor — Combines all three components into a single
       processing pipeline that updates distributions, detects drift, validates
       predictions, and falls back when necessary.

Usage:
    from shared.utils.ml_validation import MLDriftMonitor

    monitor = MLDriftMonitor()

    # For each prediction cycle:
    result = monitor.process_prediction(features, prediction)

    # Check system health:
    health = monitor.get_health()
"""

from __future__ import annotations

import logging
import math
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════

MIN_ETA_SECONDS = 300       # 5 minutes
MAX_ETA_SECONDS = 7200      # 2 hours
MIN_SAMPLES_FOR_DRIFT = 30  # Minimum observations before drift detection
DEFAULT_WINDOW_SIZE = 1000  # Sliding window size per feature
CONFIDENCE_FALLBACK_THRESHOLD = 0.3  # Below this → should_fallback=True

# Peak hours (lunch + dinner)
PEAK_HOURS = {12, 13, 19, 20, 21}


# ═══════════════════════════════════════════════════════════════
# FEATURE DISTRIBUTION TRACKER
# ═══════════════════════════════════════════════════════════════

class FeatureDistributionTracker:
    """
    Tracks mean, std, min, max of each feature over a sliding window.

    Uses a deque with maxlen for each feature to maintain a bounded
    memory footprint. Drift detection is disabled until at least 30
    observations are available for a given feature.

    Args:
        window_size: Maximum number of observations to retain per feature.
            Defaults to 1000.
    """

    def __init__(self, window_size: int = DEFAULT_WINDOW_SIZE) -> None:
        self._window_size = window_size
        self._data: Dict[str, deque] = {}

    def update(self, feature_name: str, value: float) -> None:
        """
        Add a new observation for a feature.

        Ignores NaN and Inf values to prevent distribution corruption.

        Args:
            feature_name: Name of the feature to track.
            value: Observed value for the feature.
        """
        if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
            return

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return

        if feature_name not in self._data:
            self._data[feature_name] = deque(maxlen=self._window_size)
        self._data[feature_name].append(numeric_value)

    def detect_drift(self, feature_name: str, value: float, z_threshold: float = 2.0) -> bool:
        """
        Check if a value is more than z_threshold standard deviations from the mean.

        Drift detection is disabled when fewer than MIN_SAMPLES_FOR_DRIFT
        observations are available (not enough data for statistical significance).

        Args:
            feature_name: Name of the feature to check.
            value: The value to test for drift.
            z_threshold: Number of standard deviations for the drift threshold.
                Defaults to 2.0.

        Returns:
            True if drift is detected, False otherwise.
        """
        if feature_name not in self._data:
            return False

        data = self._data[feature_name]
        if len(data) < MIN_SAMPLES_FOR_DRIFT:
            return False

        # Ignore non-numeric or invalid values
        if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
            return False

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return False

        dist = self.get_distribution(feature_name)
        std = dist["std"]

        # If std is 0 (constant feature), any different value is drift
        if std == 0.0:
            is_drift = numeric_value != dist["mean"]
        else:
            z_score = abs(numeric_value - dist["mean"]) / std
            is_drift = z_score > z_threshold

        if is_drift:
            logger.warning(
                f"Feature drift detected: feature={feature_name} "
                f"value={numeric_value:.4f} mean={dist['mean']:.4f} "
                f"std={std:.4f} z_score={abs(numeric_value - dist['mean']) / std if std != 0 else 'inf'} "
                f"threshold={z_threshold}"
            )

        return is_drift

    def get_distribution(self, feature_name: str) -> Dict[str, Any]:
        """
        Get the current distribution statistics for a feature.

        Args:
            feature_name: Name of the feature.

        Returns:
            Dictionary with keys: mean, std, min, max, count.
            Returns all zeros if feature has not been tracked.
        """
        if feature_name not in self._data:
            return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "count": 0}

        data = list(self._data[feature_name])
        count = len(data)

        if count == 0:
            return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "count": 0}

        mean = sum(data) / count
        min_val = min(data)
        max_val = max(data)

        if count < 2:
            std = 0.0
        else:
            variance = sum((x - mean) ** 2 for x in data) / (count - 1)
            std = math.sqrt(variance)

        return {
            "mean": mean,
            "std": std,
            "min": min_val,
            "max": max_val,
            "count": count,
        }

    def get_all_distributions(self) -> Dict[str, Dict[str, Any]]:
        """
        Get distribution statistics for all tracked features.

        Returns:
            Dictionary mapping feature names to their distribution stats.
        """
        return {name: self.get_distribution(name) for name in self._data}


# ═══════════════════════════════════════════════════════════════
# PREDICTION VALIDATOR
# ═══════════════════════════════════════════════════════════════

class PredictionValidator:
    """
    Validates ETA predictions against business rules.

    Enforces confidence thresholds, validates ETA bounds, checks for
    critical risk levels, and adjusts confidence based on operational
    conditions (kitchen load, queue depth, festival mode).

    Business rules:
        - ETA must be between 300s (5min) and 7200s (2hrs)
        - Confidence < 0.3 → should_fallback=True
        - Risk level CRITICAL → should_fallback=True
        - Kitchen load > 0.95 → reduce confidence by 20%
        - Queue depth > 30 → reduce confidence by 15%
        - Festival mode → reduce confidence by 10%
        - Missing features > 50% → confidence_level = "UNRELIABLE"
    """

    # Known numeric feature names for missing feature calculation
    KNOWN_FEATURES = {
        "kitchen_load", "queue_depth", "item_complexity", "time_of_day",
        "order_size", "prep_time_base", "is_weekend", "is_festival",
        "kitchen_id",
    }

    def __init__(self, drift_tracker: Optional[FeatureDistributionTracker] = None) -> None:
        """
        Initialize the validator with an optional drift tracker.

        Args:
            drift_tracker: FeatureDistributionTracker instance for drift
                detection integration. If None, drift checks are skipped.
        """
        self._drift_tracker = drift_tracker

    def validate(
        self,
        prediction: Dict[str, Any],
        features: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Validate an ETA prediction against business rules.

        Args:
            prediction: Prediction dict with at least 'eta_seconds' and
                'confidence' keys. May also include 'risk_level'.
            features: Feature dict used for the prediction. Used to check
                missing features and operational conditions.

        Returns:
            Validation result dict with keys:
                - is_valid: bool — Whether the prediction passes all rules
                - confidence_level: "HIGH" | "MEDIUM" | "LOW" | "UNRELIABLE"
                - should_fallback: bool — Whether to use fallback instead
                - fallback_eta: int — Rule-based ETA if fallback needed
                - drift_warnings: List[str] — Drift detection warnings
                - validation_errors: List[str] — Validation error messages
        """
        validation_errors: List[str] = []
        drift_warnings: List[str] = []

        eta_seconds = prediction.get("eta_seconds", 0)
        confidence = prediction.get("confidence", 0.0)
        risk_level = prediction.get("risk_level", "UNKNOWN")
        features = features or {}

        # --- ETA bounds check ---
        if not (MIN_ETA_SECONDS <= eta_seconds <= MAX_ETA_SECONDS):
            validation_errors.append(
                f"ETA {eta_seconds}s outside valid range "
                f"[{MIN_ETA_SECONDS}, {MAX_ETA_SECONDS}]"
            )

        # --- Confidence reduction based on operational conditions ---
        adjusted_confidence = confidence

        kitchen_load = self._safe_float(features.get("kitchen_load"))
        queue_depth = self._safe_int(features.get("queue_depth"))
        is_festival = features.get("is_festival", False)

        if kitchen_load > 0.95:
            adjusted_confidence *= 0.80  # Reduce by 20%

        if queue_depth > 30:
            adjusted_confidence *= 0.85  # Reduce by 15%

        if is_festival:
            adjusted_confidence *= 0.90  # Reduce by 10%

        # --- Missing features check ---
        missing_ratio = self._calculate_missing_ratio(features)
        if missing_ratio > 0.5:
            validation_errors.append(
                f"More than 50% features missing ({missing_ratio:.0%})"
            )

        # --- Drift detection ---
        if self._drift_tracker is not None:
            for feature_name, feature_value in features.items():
                if isinstance(feature_value, (int, float)) and feature_value is not None:
                    if self._drift_tracker.detect_drift(feature_name, feature_value):
                        drift_warnings.append(
                            f"Drift detected in feature: {feature_name}"
                        )

        # --- Determine confidence level ---
        if missing_ratio > 0.5:
            confidence_level = "UNRELIABLE"
        elif adjusted_confidence >= 0.8:
            confidence_level = "HIGH"
        elif adjusted_confidence >= 0.5:
            confidence_level = "MEDIUM"
        else:
            confidence_level = "LOW"

        # --- Determine should_fallback ---
        should_fallback = False

        if adjusted_confidence < CONFIDENCE_FALLBACK_THRESHOLD:
            should_fallback = True
            validation_errors.append(
                f"Confidence {adjusted_confidence:.2f} below fallback threshold "
                f"{CONFIDENCE_FALLBACK_THRESHOLD}"
            )

        if risk_level == "CRITICAL":
            should_fallback = True
            validation_errors.append(
                "Risk level CRITICAL — fallback to rule-based prediction"
            )

        if drift_warnings:
            should_fallback = True
            validation_errors.append(
                f"Feature drift detected in {len(drift_warnings)} feature(s)"
            )

        if missing_ratio > 0.5:
            should_fallback = True

        # --- Compute fallback ETA ---
        fallback_calculator = RuleBasedETAFallback()
        fallback_result = fallback_calculator.predict(features)
        fallback_eta = fallback_result["eta_seconds"]

        # --- Overall validity ---
        is_valid = len(validation_errors) == 0 and not should_fallback

        return {
            "is_valid": is_valid,
            "confidence_level": confidence_level,
            "should_fallback": should_fallback,
            "fallback_eta": fallback_eta,
            "drift_warnings": drift_warnings,
            "validation_errors": validation_errors,
            "adjusted_confidence": round(adjusted_confidence, 4),
        }

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Safely convert a value to float."""
        try:
            v = float(value)
            return v if not (math.isnan(v) or math.isinf(v)) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Safely convert a value to int."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _calculate_missing_ratio(features: Dict[str, Any]) -> float:
        """Calculate the ratio of missing (None) features."""
        if not features:
            return 1.0
        total = len(features)
        missing = sum(1 for v in features.values() if v is None)
        return missing / total


# ═══════════════════════════════════════════════════════════════
# RULE-BASED ETA FALLBACK
# ═══════════════════════════════════════════════════════════════

class RuleBasedETAFallback:
    """
    Deterministic ETA prediction when ML is unreliable.

    Uses simple, interpretable rules based on kitchen load, queue depth,
    item complexity, festival mode, and peak hours. Always returns a
    conservative estimate with low confidence (0.3).

    Rules:
        base_time = 900s (15 min default)
        kitchen_load_factor = 1 + kitchen_load (clamped to [0.1, 1.0])
        queue_factor = queue_depth * 30s
        complexity_factor = item_complexity * 1.5
        festival_factor = 1.3 if is_festival else 1.0
        peak_factor = 1.2 if time_of_day in [12,13,19,20,21] else 1.0
        final_eta = base_time * kitchen_load_factor * complexity_factor
                    * festival_factor * peak_factor + queue_factor
        Clamp to [300, 7200]
    """

    def predict(self, features: Dict[str, Any]) -> Dict[str, Any]:
        """
        Predict ETA using deterministic rules.

        Args:
            features: Feature dict with kitchen_load, queue_depth,
                item_complexity, is_festival, time_of_day, etc.

        Returns:
            Prediction dict with eta_seconds, eta_minutes, confidence,
            model_version, and applied_rules.
        """
        # --- Extract and sanitize features ---
        kitchen_load = self._safe_float(features.get("kitchen_load", 0.5), default=0.5)
        kitchen_load = max(0.1, min(1.0, kitchen_load))

        queue_depth = self._safe_int(features.get("queue_depth", 0), default=0)
        queue_depth = max(0, queue_depth)

        item_complexity = self._safe_float(features.get("item_complexity", 1.0), default=1.0)
        item_complexity = max(0.1, item_complexity)

        is_festival = bool(features.get("is_festival", False))
        time_of_day = self._safe_int(features.get("time_of_day", 12), default=12)
        time_of_day = max(0, min(23, time_of_day))

        # --- Compute factors ---
        base_time = 900  # 15 minutes
        kitchen_load_factor = 1 + kitchen_load
        queue_factor = queue_depth * 30
        complexity_factor = item_complexity * 1.5
        festival_factor = 1.3 if is_festival else 1.0
        peak_factor = 1.2 if time_of_day in PEAK_HOURS else 1.0

        # --- Compute final ETA ---
        final_eta = (
            base_time
            * kitchen_load_factor
            * complexity_factor
            * festival_factor
            * peak_factor
            + queue_factor
        )

        # --- Clamp to valid range ---
        final_eta = int(max(MIN_ETA_SECONDS, min(MAX_ETA_SECONDS, final_eta)))

        # --- Build applied rules dict for transparency ---
        applied_rules = {
            "base_time": base_time,
            "kitchen_load_factor": round(kitchen_load_factor, 2),
            "queue_factor": queue_factor,
            "complexity_factor": round(complexity_factor, 2),
            "festival_factor": festival_factor,
            "peak_factor": peak_factor,
        }

        return {
            "eta_seconds": final_eta,
            "eta_minutes": round(final_eta / 60, 1),
            "confidence": 0.3,
            "model_version": "rule_based_fallback_v1",
            "applied_rules": applied_rules,
        }

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Safely convert a value to float."""
        try:
            v = float(value)
            return v if not (math.isnan(v) or math.isinf(v)) else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Safely convert a value to int."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default


# ═══════════════════════════════════════════════════════════════
# ML DRIFT MONITOR
# ═══════════════════════════════════════════════════════════════

class MLDriftMonitor:
    """
    Combines FeatureDistributionTracker + PredictionValidator into a
    unified pipeline for processing ML predictions.

    Processing steps for each prediction:
        1. Update feature distributions with observed values
        2. Check for drift in each feature
        3. Validate the prediction against business rules
        4. If should_fallback, use RuleBasedETAFallback
        5. Return final prediction with validation metadata

    Args:
        window_size: Sliding window size for feature distribution tracking.
            Defaults to 1000.
    """

    def __init__(self, window_size: int = DEFAULT_WINDOW_SIZE) -> None:
        self._tracker = FeatureDistributionTracker(window_size=window_size)
        self._validator = PredictionValidator(drift_tracker=self._tracker)
        self._fallback = RuleBasedETAFallback()

        # Metrics
        self._total_predictions: int = 0
        self._fallback_count: int = 0
        self._drift_detected_count: int = 0

    def process_prediction(
        self,
        features: Dict[str, Any],
        prediction: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Process an ML prediction through the full validation pipeline.

        Args:
            features: Feature dict used for the prediction.
            prediction: Prediction dict from the ML model with keys like
                eta_seconds, confidence, risk_level, model_version.

        Returns:
            Final prediction dict with validation metadata. If fallback
            is triggered, the eta_seconds and related fields come from
            the rule-based fallback model.
        """
        self._total_predictions += 1

        # Step 1: Update feature distributions
        for feature_name, feature_value in features.items():
            if isinstance(feature_value, (int, float)) and feature_value is not None:
                self._tracker.update(feature_name, feature_value)

        # Step 2 & 3: Validate prediction (includes drift check)
        validation = self._validator.validate(prediction, features)

        # Track drift metrics
        if validation["drift_warnings"]:
            self._drift_detected_count += 1

        # Step 4: If should_fallback, use RuleBasedETAFallback
        if validation["should_fallback"]:
            self._fallback_count += 1
            fallback_result = self._fallback.predict(features)

            return {
                "eta_seconds": fallback_result["eta_seconds"],
                "eta_minutes": fallback_result["eta_minutes"],
                "confidence": fallback_result["confidence"],
                "model_version": fallback_result["model_version"],
                "risk_level": prediction.get("risk_level", "UNKNOWN"),
                "used_fallback": True,
                "original_prediction": {
                    "eta_seconds": prediction.get("eta_seconds"),
                    "confidence": prediction.get("confidence"),
                    "model_version": prediction.get("model_version"),
                },
                "validation": validation,
                "applied_rules": fallback_result.get("applied_rules", {}),
            }

        # Step 5: Return original prediction with validation metadata
        return {
            "eta_seconds": prediction.get("eta_seconds"),
            "eta_minutes": prediction.get("eta_minutes"),
            "confidence": validation.get("adjusted_confidence", prediction.get("confidence")),
            "model_version": prediction.get("model_version"),
            "risk_level": prediction.get("risk_level", "UNKNOWN"),
            "used_fallback": False,
            "original_prediction": None,
            "validation": validation,
        }

    def get_health(self) -> Dict[str, Any]:
        """
        Get the health status of the ML drift monitoring system.

        Returns:
            Health dict with:
                - status: "HEALTHY" | "DEGRADED" | "CRITICAL"
                - drift_features: List of feature names with detected drift
                - total_predictions: Total number of predictions processed
                - fallback_rate: Ratio of predictions that used fallback
        """
        fallback_rate = (
            self._fallback_count / self._total_predictions
            if self._total_predictions > 0
            else 0.0
        )

        # Identify features with active drift
        drift_features: List[str] = []
        all_distributions = self._tracker.get_all_distributions()
        for feature_name, dist in all_distributions.items():
            if dist["count"] >= MIN_SAMPLES_FOR_DRIFT:
                # Check if the latest value shows drift
                data = self._tracker._data[feature_name]
                if data:
                    latest_value = data[-1]
                    if self._tracker.detect_drift(feature_name, latest_value):
                        drift_features.append(feature_name)

        # Determine overall status
        if fallback_rate > 0.5:
            status = "CRITICAL"
        elif fallback_rate > 0.2 or len(drift_features) > 3:
            status = "DEGRADED"
        else:
            status = "HEALTHY"

        return {
            "status": status,
            "drift_features": drift_features,
            "total_predictions": self._total_predictions,
            "fallback_rate": round(fallback_rate, 4),
            "fallback_count": self._fallback_count,
            "drift_detected_count": self._drift_detected_count,
            "tracked_features": list(all_distributions.keys()),
        }
