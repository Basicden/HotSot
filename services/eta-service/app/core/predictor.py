"""HotSot ETA Service — ETA Prediction Engine.

Multi-factor ETA prediction for Indian cloud kitchens.
Formula: base_prep + queue_delay + load_factor + peak_adjustment + buffer
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from shared.utils.helpers import now_ts

logger = logging.getLogger("eta-service.predictor")

# Default baselines for Indian cloud kitchens
BASE_PREP_SECONDS = 300  # 5 min average
PEAK_PREP_SECONDS = 420  # 7 min during peak
QUEUE_DELAY_PER_ORDER = 45  # 45 seconds per order ahead
LOAD_FACTOR_OVERLOADED = 1.5  # 50% more time when overloaded
PEAK_HOURS = [(11, 14), (19, 22)]  # 11am-2pm, 7pm-10pm IST
DEFAULT_BUFFER = 45  # Buffer seconds


@dataclass
class ETAPrediction:
    order_id: str
    eta_seconds: int
    confidence_interval_low: int
    confidence_interval_high: int
    confidence_score: float
    risk_level: str
    delay_probability: float
    features_used: Dict[str, Any]


class ETAPredictor:
    """Multi-factor ETA predictor for cloud kitchen orders."""

    def __init__(self, redis_client=None):
        self._redis = redis_client

    async def predict(
        self,
        order_id: str,
        kitchen_id: str,
        items: List[Dict] = None,
        kitchen_load_pct: float = 0.0,
        queue_length: int = 0,
        time_of_day: Optional[int] = None,
        is_peak: Optional[bool] = None,
        base_prep_seconds: int = BASE_PREP_SECONDS,
    ) -> ETAPrediction:
        """Calculate ETA prediction for an order.

        Factors:
        1. Base prep time (kitchen-specific)
        2. Queue delay (orders ahead × delay per order)
        3. Load factor (overloaded kitchens take longer)
        4. Peak adjustment (peak hours add time)
        5. Buffer (safety margin)
        """
        if time_of_day is None:
            time_of_day = datetime.now(timezone.utc).hour
        if is_peak is None:
            is_peak = any(start <= time_of_day <= end for start, end in PEAK_HOURS)

        # Base prep time
        base = base_prep_seconds

        # Queue delay
        queue_delay = queue_length * QUEUE_DELAY_PER_ORDER

        # Load factor
        load_factor = LOAD_FACTOR_OVERLOADED if kitchen_load_pct >= 85 else 1.0

        # Peak adjustment
        peak_seconds = PEAK_PREP_SECONDS - BASE_PREP_SECONDS if is_peak else 0

        # Buffer
        buffer = DEFAULT_BUFFER

        # Complex items add time
        item_complexity = self._calculate_item_complexity(items or [])

        # Total ETA
        eta_seconds = int((base + item_complexity) * load_factor + queue_delay + peak_seconds + buffer)

        # Confidence scoring (0-1)
        confidence = self._calculate_confidence(
            kitchen_load_pct, queue_length, is_peak, items
        )

        # Confidence interval (±20% at low confidence, ±5% at high)
        margin = int(eta_seconds * (1 - confidence) * 0.5)
        ci_low = max(0, eta_seconds - margin)
        ci_high = eta_seconds + margin

        # Risk level
        risk_level = self._determine_risk(kitchen_load_pct, queue_length, is_peak)

        # Delay probability
        delay_prob = min(1.0, (kitchen_load_pct / 100) * 0.5 + (queue_length / 50) * 0.3 + (0.2 if is_peak else 0))

        features = {
            "base_prep": base,
            "queue_delay": queue_delay,
            "load_factor": load_factor,
            "peak_seconds": peak_seconds,
            "item_complexity": item_complexity,
            "buffer": buffer,
            "is_peak": is_peak,
        }

        prediction = ETAPrediction(
            order_id=order_id,
            eta_seconds=eta_seconds,
            confidence_interval_low=ci_low,
            confidence_interval_high=ci_high,
            confidence_score=round(confidence, 3),
            risk_level=risk_level,
            delay_probability=round(delay_prob, 3),
            features_used=features,
        )

        # Cache in Redis
        if self._redis:
            cache_key = f"eta:{kitchen_id}:{order_id}"
            await self._redis.client.setex(
                cache_key, 120,
                json.dumps({
                    "eta_seconds": eta_seconds,
                    "risk_level": risk_level,
                    "confidence": confidence,
                })
            )

        return prediction

    async def get_cached_prediction(self, kitchen_id: str, order_id: str) -> Optional[Dict]:
        """Get cached ETA prediction from Redis."""
        if not self._redis:
            return None
        cache_key = f"eta:{kitchen_id}:{order_id}"
        data = await self._redis.client.get(cache_key)
        return json.loads(data) if data else None

    @staticmethod
    def _calculate_item_complexity(items: List[Dict]) -> int:
        """Calculate additional prep time for complex items."""
        complex_keywords = ["biryani", "thali", "tandoor", "kebab", "pizza"]
        extra = 0
        for item in items:
            name = item.get("name", "").lower() if isinstance(item, dict) else str(item).lower()
            if any(kw in name for kw in complex_keywords):
                extra += 60  # +1 minute per complex item
        return min(extra, 180)  # Cap at 3 minutes

    @staticmethod
    def _calculate_confidence(load_pct: float, queue_length: int,
                               is_peak: bool, items: List[Dict]) -> float:
        """Calculate prediction confidence (0-1)."""
        confidence = 0.85  # Start with moderate-high confidence

        # Lower confidence when overloaded
        if load_pct >= 85:
            confidence -= 0.15
        elif load_pct >= 60:
            confidence -= 0.05

        # Lower confidence with long queues
        if queue_length > 30:
            confidence -= 0.1
        elif queue_length > 15:
            confidence -= 0.05

        # Lower confidence during peak
        if is_peak:
            confidence -= 0.05

        # Lower confidence with many items
        if len(items) > 5:
            confidence -= 0.05

        return max(0.3, min(1.0, confidence))

    @staticmethod
    def _determine_risk(load_pct: float, queue_length: int, is_peak: bool) -> str:
        """Determine risk level for ETA prediction."""
        score = 0
        if load_pct >= 85:
            score += 3
        elif load_pct >= 60:
            score += 1

        if queue_length > 30:
            score += 3
        elif queue_length > 15:
            score += 1

        if is_peak:
            score += 1

        if score >= 5:
            return "CRITICAL"
        elif score >= 3:
            return "HIGH"
        elif score >= 1:
            return "MEDIUM"
        return "LOW"
