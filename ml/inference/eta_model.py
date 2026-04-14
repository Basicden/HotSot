"""HotSot ML — ETA Model Inference Wrapper."""

import os
from typing import Dict, Any, Optional, List
from dataclasses import dataclass


@dataclass
class PredictionResult:
    eta_seconds: int
    confidence: float
    confidence_low: int
    confidence_high: int
    risk_level: str
    delay_probability: float
    model_version: str


class ETAModel:
    """Production ETA model wrapper supporting LightGBM + rule-based fallback."""

    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.model_version = "none"
        if model_path and os.path.exists(model_path):
            self._load_model(model_path)

    def _load_model(self, path: str):
        try:
            if path.endswith(".txt"):
                import lightgbm as lgb
                self.model = lgb.Booster(model_file=path)
                self.model_version = "lgbm_" + os.path.basename(path)
            elif path.endswith(".pkl"):
                import pickle
                with open(path, "rb") as f:
                    self.model = pickle.load(f)
                self.model_version = "sklearn_" + os.path.basename(path)
        except Exception:
            self.model = None

    def predict(self, features: list) -> PredictionResult:
        if self.model is not None:
            try:
                import numpy as np
                X = np.array([features])
                eta = float(self.model.predict(X)[0])
                return self._build_result(eta, features)
            except Exception:
                pass
        return self._rule_based_predict(features)

    def _rule_based_predict(self, features: list) -> PredictionResult:
        base = 300
        eta = base + features[0] * 8 + features[1] * 25 + features[2] * 45
        if len(features) > 4 and features[4]: eta *= 1.5
        if len(features) > 5 and features[5]: eta *= 1.8
        return self._build_result(eta, features)

    def _build_result(self, eta: float, features: list) -> PredictionResult:
        eta = max(60, int(eta))
        kitchen_load = features[0] if features else 0
        queue_length = features[1] if len(features) > 1 else 0
        confidence = max(0.4, min(0.99, 0.95 - kitchen_load * 0.02 - queue_length * 0.03))
        buffer = int(eta * 0.1)
        if confidence >= 0.85 and eta < 600: risk = "LOW"
        elif confidence >= 0.60: risk = "MEDIUM"
        else: risk = "HIGH"
        return PredictionResult(eta_seconds=eta, confidence=confidence, confidence_low=max(30, eta - buffer),
                                confidence_high=eta + buffer, risk_level=risk,
                                delay_probability=min(1.0, kitchen_load * 0.02 + queue_length * 0.05),
                                model_version=self.model_version or "rule_based")
