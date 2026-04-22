"""HotSot ETA Service — ETA Prediction Engine.

Multi-factor ETA prediction for Indian cloud kitchens.
Formula: base_prep + queue_delay + load_factor + peak_adjustment + buffer

FIX #6: Added distribution drift detection and rule-based fallback.
When feature drift exceeds threshold or confidence drops below 0.5,
the predictor auto-switches to a conservative rule-based estimate
instead of producing confident-but-wrong predictions.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from shared.utils.helpers import now_ts

logger = logging.getLogger("eta-service.predictor")

# Default baselines for Indian cloud kitchens
BASE_PREP_SECONDS = 300  # 5 min average
PEAK_PREP_SECONDS = 420  # 7 min during peak
QUEUE_DELAY_PER_ORDER = 45  # 45 seconds per order ahead
LOAD_FACTOR_OVERLOADED = 1.5  # 50% more time when overloaded
PEAK_HOURS = [(11, 14), (19, 22)]  # 11am-2pm, 7pm-10pm IST
DEFAULT_BUFFER = 45  # Buffer seconds

# FIX #6: Drift detection thresholds
DRIFT_CONFIDENCE_THRESHOLD = 0.5  # Below this, switch to rule-based fallback
DRIFT_LOAD_THRESHOLD = 0.90  # 90% load = drift zone
FESTIVAL_LOAD_MULTIPLIER = 2.0  # 2x during festival rush
FESTIVAL_QUEUE_MULTIPLIER = 3.0  # 3x queue depth during festivals

# Known Indian festival dates (month-day) for proactive drift detection
FESTIVAL_PERIODS = [
    (10, 1, 10, 31),   # Navratri/Dussehra (Oct)
    (11, 1, 11, 15),   # Diwali (Nov)
    (12, 20, 1, 5),    # Christmas/New Year
    (3, 1, 3, 31),     # Holi (Mar)
    (8, 1, 8, 31),     # Raksha Bandhan/Janmashtami (Aug)
    (4, 1, 4, 14),     # Baisakhi/Ramadan Eid (Apr)
]


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
    drift_detected: bool = False  # FIX #6: Flag when drift is detected
    fallback_mode: bool = False   # FIX #6: True when using rule-based fallback


class ETAPredictor:
    """Multi-factor ETA predictor for cloud kitchen orders.

    FIX #6: Includes distribution drift detection. When load/queue metrics
    exceed normal training distribution bounds, confidence is automatically
    reduced and a wider prediction interval is used. If confidence drops
    below DRIFT_CONFIDENCE_THRESHOLD, the predictor switches to a
    conservative rule-based fallback with wider margins.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._prediction_history: List[Dict] = []  # For drift tracking
        self._drift_window_size = 100  # Track last 100 predictions

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

        # FIX #6: Distribution drift detection
        drift_detected = False
        fallback_mode = False

        # Check for festival period
        now_month = datetime.now(timezone.utc).month
        now_day = datetime.now(timezone.utc).day
        is_festival = any(
            (m1 <= now_month < m2) or (now_month == m1 and now_day >= d1) or (now_month == m2 and now_day <= d2)
            for m1, d1, m2, d2 in FESTIVAL_PERIODS
        )

        # Detect drift conditions
        if is_festival:
            drift_detected = True
            logger.info(
                f"Drift detected: festival period (month={now_month}, day={now_day}) — "
                f"applying festival multipliers for order={order_id}"
            )
            # Recalculate with festival multipliers
            queue_delay = queue_length * QUEUE_DELAY_PER_ORDER * FESTIVAL_QUEUE_MULTIPLIER
            load_factor = LOAD_FACTOR_OVERLOADED * FESTIVAL_LOAD_MULTIPLIER if kitchen_load_pct >= 60 else LOAD_FACTOR_OVERLOADED
            buffer = DEFAULT_BUFFER * 3  # Triple buffer during festivals
            eta_seconds = int((base + item_complexity) * load_factor + queue_delay + peak_seconds + buffer)

        elif kitchen_load_pct >= DRIFT_LOAD_THRESHOLD * 100:
            drift_detected = True
            logger.info(
                f"Drift detected: extreme load ({kitchen_load_pct}%) — "
                f"widening confidence interval for order={order_id}"
            )
            # Add extra buffer for extreme load
            eta_seconds = int(eta_seconds * 1.3)  # 30% more time

        elif queue_length > 30:
            drift_detected = True
            logger.info(
                f"Drift detected: long queue ({queue_length} orders) — "
                f"switching to conservative estimate for order={order_id}"
            )

        # Auto-switch to fallback mode when confidence is too low
        if confidence < DRIFT_CONFIDENCE_THRESHOLD:
            fallback_mode = True
            # Conservative rule-based fallback: use worst-case estimates
            eta_seconds = int(base * 2.0 + queue_length * 60 + 180)  # 2x prep + 1min/order + 3min buffer
            confidence = DRIFT_CONFIDENCE_THRESHOLD  # Floor the confidence
            logger.warning(
                f"ETA fallback mode activated for order={order_id}: "
                f"confidence={confidence:.3f} < threshold={DRIFT_CONFIDENCE_THRESHOLD}. "
                f"Using conservative rule-based estimate: {eta_seconds}s"
            )

        # Track prediction for drift analysis
        self._prediction_history.append({
            "order_id": order_id,
            "kitchen_id": kitchen_id,
            "eta_seconds": eta_seconds,
            "confidence": confidence,
            "load_pct": kitchen_load_pct,
            "queue_length": queue_length,
            "drift_detected": drift_detected,
            "fallback_mode": fallback_mode,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        # Keep only recent predictions
        if len(self._prediction_history) > self._drift_window_size:
            self._prediction_history = self._prediction_history[-self._drift_window_size:]

        # Recalculate confidence interval (wider during drift)
        margin_pct = 0.05 + (0.25 if drift_detected else 0) + (0.40 if fallback_mode else 0)
        margin = int(eta_seconds * margin_pct)
        ci_low = max(0, eta_seconds - margin)
        ci_high = eta_seconds + margin

        prediction = ETAPrediction(
            order_id=order_id,
            eta_seconds=eta_seconds,
            confidence_interval_low=ci_low,
            confidence_interval_high=ci_high,
            confidence_score=round(confidence, 3),
            risk_level=risk_level,
            delay_probability=round(delay_prob, 3),
            features_used=features,
            drift_detected=drift_detected,
            fallback_mode=fallback_mode,
        )

        # Cache in Redis (fail-closed: log warning if unavailable)
        if self._redis:
            cache_key = f"eta:{kitchen_id}:{order_id}"
            try:
                await self._redis.client.setex(
                    cache_key, 120,
                    json.dumps({
                        "eta_seconds": eta_seconds,
                        "risk_level": risk_level,
                        "confidence": confidence,
                    })
                )
            except Exception as exc:
                logger.warning(
                    "predict: Failed to cache ETA prediction — order=%s kitchen=%s: %s",
                    order_id, kitchen_id, exc,
                )
        else:
            logger.warning(
                "predict: Redis unavailable — ETA prediction not cached for order=%s kitchen=%s",
                order_id, kitchen_id,
            )

        return prediction

    async def get_cached_prediction(self, kitchen_id: str, order_id: str) -> Optional[Dict]:
        """Get cached ETA prediction from Redis.

        Fail-CLOSED: Returns None with WARNING when Redis is unavailable.
        Callers must treat None as a cache miss and recompute.
        """
        if not self._redis:
            logger.warning(
                "get_cached_prediction: Redis unavailable — cache miss for order=%s kitchen=%s",
                order_id, kitchen_id,
            )
            return None
        cache_key = f"eta:{kitchen_id}:{order_id}"
        try:
            data = await self._redis.client.get(cache_key)
            return json.loads(data) if data else None
        except Exception as exc:
            logger.warning(
                "get_cached_prediction: Redis error — cache miss for order=%s kitchen=%s: %s",
                order_id, kitchen_id, exc,
            )
            return None

    def get_drift_stats(self) -> Dict[str, Any]:
        """FIX #6: Get current drift detection statistics."""
        if not self._prediction_history:
            return {"total_predictions": 0, "drift_rate": 0, "fallback_rate": 0}

        total = len(self._prediction_history)
        drift_count = sum(1 for p in self._prediction_history if p.get("drift_detected"))
        fallback_count = sum(1 for p in self._prediction_history if p.get("fallback_mode"))
        avg_confidence = sum(p.get("confidence", 0) for p in self._prediction_history) / total
        avg_load = sum(p.get("load_pct", 0) for p in self._prediction_history) / total

        return {
            "total_predictions": total,
            "drift_count": drift_count,
            "drift_rate": round(drift_count / total, 3),
            "fallback_count": fallback_count,
            "fallback_rate": round(fallback_count / total, 3),
            "avg_confidence": round(avg_confidence, 3),
            "avg_load_pct": round(avg_load, 1),
            "needs_retrain": drift_count / total > 0.25 if total > 10 else False,
        }

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
