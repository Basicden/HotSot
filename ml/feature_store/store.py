"""HotSot ML — Feature Store (Redis-backed)."""

import os
import json
from typing import Dict, Any, Optional
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/5")


class FeatureStore:
    """Online feature store backed by Redis for kitchen/user features."""

    def __init__(self):
        self.client = redis.from_url(REDIS_URL, decode_responses=True)

    def set_kitchen_features(self, kitchen_id: str, features: Dict[str, Any]):
        key = f"features:kitchen:{kitchen_id}"
        self.client.setex(key, 300, json.dumps(features, default=str))

    def get_kitchen_features(self, kitchen_id: str) -> Optional[Dict[str, Any]]:
        data = self.client.get(f"features:kitchen:{kitchen_id}")
        return json.loads(data) if data else None

    def set_user_features(self, user_id: str, features: Dict[str, Any]):
        self.client.setex(f"features:user:{user_id}", 3600, json.dumps(features, default=str))

    def get_user_features(self, user_id: str) -> Optional[Dict[str, Any]]:
        data = self.client.get(f"features:user:{user_id}")
        return json.loads(data) if data else None

    def build_feature_vector(self, kitchen_id: str, user_id: str = None) -> Dict[str, Any]:
        import time
        kitchen = self.get_kitchen_features(kitchen_id) or {}
        user = self.get_user_features(user_id) if user_id else {}
        hour = time.localtime().tm_hour
        return {
            "kitchen_load": kitchen.get("load", 0), "queue_length": kitchen.get("queue_length", 0),
            "staff_count": kitchen.get("staff_count", 3), "historical_delay_avg": kitchen.get("avg_delay_15min", 0.0),
            "shelf_availability": kitchen.get("shelf_availability", 0.8), "time_of_day": hour,
            "is_peak_hour": hour in [12, 13, 20, 21], "is_festival": kitchen.get("is_festival", False),
            "is_monsoon": kitchen.get("is_monsoon", False), "user_tier": user.get("tier", 0),
            "arrival_pressure": kitchen.get("arrival_pressure", 0.0),
        }
