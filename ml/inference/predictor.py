"""HotSot ML — Real-time Predictor Service."""

import os
from typing import Dict, Any
from eta_model import ETAModel

MODEL_PATH = os.getenv("MODEL_PATH", "/app/models/eta_model.txt")
predictor = ETAModel(model_path=MODEL_PATH)


def predict_eta(features: Dict[str, Any]) -> Dict[str, Any]:
    """Predict ETA from feature dictionary."""
    feature_vector = [
        features.get("kitchen_load", 0), features.get("queue_length", 0),
        features.get("item_complexity", 1), features.get("time_of_day", 12),
        float(features.get("is_peak_hour", False)), float(features.get("is_festival", False)),
        float(features.get("is_monsoon", False)), features.get("staff_count", 3),
        features.get("historical_delay_avg", 0.0), features.get("shelf_availability", 0.8),
        features.get("arrival_pressure", 0.0),
    ]
    result = predictor.predict(feature_vector)
    return {
        "eta_seconds": result.eta_seconds, "confidence": round(result.confidence, 2),
        "confidence_interval": [result.confidence_low, result.confidence_high],
        "risk_level": result.risk_level, "delay_probability": round(result.delay_probability, 2),
        "model_version": result.model_version,
    }
