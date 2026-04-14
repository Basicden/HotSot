"""HotSot ETA Service — ML Predictor (LightGBM + Rule-based Fallback)."""

import os
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from app.core.config import config


@dataclass
class ETAResult:
    """ETA prediction result with confidence intervals."""
    eta_seconds: int
    confidence: float
    confidence_interval: List[int]
    risk_level: str
    delay_probability: float
    model_version: str
    features_used: List[str]


_model = None
_model_version = "v1_rule_based"


def load_model():
    """Load the ML model for inference."""
    global _model, _model_version
    model_path = config.MODEL_PATH
    if os.path.exists(model_path):
        try:
            import lightgbm as lgb
            _model = lgb.Booster(model_file=model_path)
            _model_version = "v1_lgbm"
            print(f"[ETA] Loaded LightGBM model from {model_path}")
        except Exception as e:
            print(f"[ETA] Failed to load model: {e}. Using rule-based fallback.")
            _model = None
    else:
        print(f"[ETA] Model not found at {model_path}. Using rule-based fallback.")
        _model = None


def predict_eta(
    kitchen_load: int = 0, queue_length: int = 0, item_complexity: int = 1,
    time_of_day: int = 12, is_peak_hour: bool = False, is_festival: bool = False,
    is_monsoon: bool = False, staff_count: int = 3, historical_delay: float = 0.0,
    arrival_pressure: float = 0.0,
) -> ETAResult:
    """Predict ETA using ML model or rule-based fallback."""
    if _model is not None:
        return _predict_ml(kitchen_load, queue_length, item_complexity,
                          time_of_day, is_peak_hour, is_festival,
                          is_monsoon, staff_count, historical_delay)
    return _predict_rule_based(kitchen_load, queue_length, item_complexity,
                              time_of_day, is_peak_hour, is_festival,
                              is_monsoon, staff_count, historical_delay, arrival_pressure)


def _predict_ml(kitchen_load, queue_length, item_complexity,
                time_of_day, is_peak_hour, is_festival,
                is_monsoon, staff_count, historical_delay) -> ETAResult:
    """ML-based prediction using LightGBM."""
    features = [kitchen_load, queue_length, item_complexity, time_of_day,
                int(is_peak_hour), int(is_festival), int(is_monsoon),
                staff_count, historical_delay, kitchen_load * item_complexity]
    try:
        eta = int(_model.predict([features])[0])
    except Exception:
        return _predict_rule_based(kitchen_load, queue_length, item_complexity,
                                  time_of_day, is_peak_hour, is_festival,
                                  is_monsoon, staff_count, historical_delay, 0.0)

    confidence = _calculate_confidence(kitchen_load, queue_length)
    risk = _assess_risk(eta, confidence)
    delay_prob = min(historical_delay / max(eta, 1), 1.0)
    buffer = int(eta * 0.1)
    return ETAResult(eta_seconds=max(60, eta), confidence=confidence,
                     confidence_interval=[max(30, eta - buffer), eta + buffer],
                     risk_level=risk, delay_probability=delay_prob,
                     model_version=_model_version,
                     features_used=["kitchen_load", "queue_length", "complexity", "time_of_day",
                                    "peak_hour", "festival", "monsoon", "staff_count", "historical_delay"])


def _predict_rule_based(kitchen_load, queue_length, item_complexity,
                        time_of_day, is_peak_hour, is_festival,
                        is_monsoon, staff_count, historical_delay,
                        arrival_pressure) -> ETAResult:
    """Rule-based ETA (India-optimized fallback)."""
    base = config.BASE_PREP_SECONDS
    queue_factor = queue_length * 25
    load_factor = kitchen_load * 8
    complexity_factor = item_complexity * 45
    peak_mult = config.PEAK_HOUR_MULTIPLIER if is_peak_hour else 1.0
    festival_mult = config.FESTIVAL_MULTIPLIER if is_festival else 1.0
    monsoon_mult = config.MONSOON_MULTIPLIER if is_monsoon else 1.0
    staff_efficiency = max(0, (staff_count - 2) * 15)
    arrival_boost = arrival_pressure * 20
    raw_eta = (base + queue_factor + load_factor + complexity_factor) * peak_mult * festival_mult * monsoon_mult
    eta = max(60, int(raw_eta - staff_efficiency - arrival_boost + historical_delay * 0.3))
    confidence = _calculate_confidence(kitchen_load, queue_length)
    risk = _assess_risk(eta, confidence)
    delay_prob = min(0.5, (kitchen_load * 0.02 + queue_length * 0.05))
    buffer = int(eta * 0.12)
    return ETAResult(eta_seconds=eta, confidence=confidence,
                     confidence_interval=[max(30, eta - buffer), eta + buffer],
                     risk_level=risk, delay_probability=min(1.0, delay_prob),
                     model_version="v1_rule_based",
                     features_used=["kitchen_load", "queue_length", "complexity", "peak_hour",
                                    "festival", "monsoon", "staff_count", "arrival_pressure"])


def _calculate_confidence(kitchen_load: int, queue_length: int) -> float:
    load_penalty = kitchen_load * 0.02
    queue_penalty = queue_length * 0.03
    confidence = 0.95 - load_penalty - queue_penalty
    return max(config.MIN_CONFIDENCE, min(config.MAX_CONFIDENCE, confidence))


def _assess_risk(eta_seconds: int, confidence: float) -> str:
    if confidence >= 0.85 and eta_seconds < 600:
        return "LOW"
    elif confidence >= 0.60:
        return "MEDIUM"
    return "HIGH"
