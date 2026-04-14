"""HotSot ML — Feature Engineering for ETA Model."""

import time
from typing import Dict, Any, List
from dataclasses import dataclass

FEATURE_DEFINITIONS = {
    "kitchen_load": {"type": "int", "range": [0, 50], "description": "Current orders in kitchen", "source": "redis:kitchen:{id}:load"},
    "queue_length": {"type": "int", "range": [0, 30], "description": "Orders waiting in queue", "source": "kitchen_engine:queue_depth"},
    "item_complexity": {"type": "int", "range": [1, 5], "description": "Prep complexity (1=simple, 5=complex thali)", "source": "menu:item:complexity"},
    "time_of_day": {"type": "int", "range": [0, 23], "description": "Hour of day", "source": "derived"},
    "is_peak_hour": {"type": "bool", "description": "Peak hour (12-2pm or 8-10pm IST)", "source": "derived"},
    "is_festival": {"type": "bool", "description": "Festival day flag (Diwali, Eid, etc.)", "source": "calendar:festival_flag"},
    "is_monsoon": {"type": "bool", "description": "Monsoon conditions active", "source": "weather:monsoon_flag"},
    "staff_count": {"type": "int", "range": [1, 10], "description": "Staff on shift", "source": "kitchen:staff:count"},
    "historical_delay_avg": {"type": "float", "range": [0, 300], "description": "Rolling 15-min avg delay", "source": "redis:kitchen:{id}:avg_delay"},
    "shelf_availability": {"type": "float", "range": [0, 1], "description": "Fraction of shelf slots available", "source": "shelf:availability_ratio"},
    "arrival_pressure": {"type": "float", "range": [0, 1], "description": "Users approaching for pickup (0-1)", "source": "arrival:detection_score"},
}


@dataclass
class FeatureVector:
    """Compiled feature vector for ETA prediction."""
    kitchen_load: int = 0
    queue_length: int = 0
    item_complexity: int = 1
    time_of_day: int = 12
    is_peak_hour: bool = False
    is_festival: bool = False
    is_monsoon: bool = False
    staff_count: int = 3
    historical_delay_avg: float = 0.0
    shelf_availability: float = 0.8
    arrival_pressure: float = 0.0

    @property
    def load_complexity_interaction(self) -> float:
        return self.kitchen_load * self.item_complexity

    @property
    def peak_festival_combined(self) -> float:
        m = 1.0
        if self.is_peak_hour: m *= 1.5
        if self.is_festival: m *= 1.8
        return m

    def to_array(self) -> List[float]:
        return [
            self.kitchen_load, self.queue_length, self.item_complexity,
            self.time_of_day, float(self.is_peak_hour), float(self.is_festival),
            float(self.is_monsoon), self.staff_count, self.historical_delay_avg,
            self.shelf_availability, self.arrival_pressure,
            self.load_complexity_interaction, self.peak_festival_combined,
        ]


def build_feature_vector(kitchen_id: str, order_data: Dict[str, Any]) -> FeatureVector:
    """Build feature vector from real-time data sources."""
    now = time.localtime()
    hour = now.tm_hour
    return FeatureVector(
        kitchen_load=order_data.get("kitchen_load", 0),
        queue_length=order_data.get("queue_length", 0),
        item_complexity=order_data.get("item_complexity", 1),
        time_of_day=hour,
        is_peak_hour=hour in [12, 13, 20, 21],
        is_festival=order_data.get("is_festival", False),
        is_monsoon=order_data.get("is_monsoon", False),
        staff_count=order_data.get("staff_count", 3),
        historical_delay_avg=order_data.get("historical_delay_avg", 0.0),
        shelf_availability=order_data.get("shelf_availability", 0.8),
        arrival_pressure=order_data.get("arrival_pressure", 0.0),
    )
