"""HotSot ML — Dataset Builder for ETA Model Training."""

import time
import random
import csv
from typing import List, Dict, Any
from dataclasses import dataclass
import numpy as np


@dataclass
class TrainingRecord:
    order_id: str
    kitchen_id: str
    kitchen_load: int
    queue_length: int
    item_complexity: int
    time_of_day: int
    day_of_week: int
    is_peak_hour: bool
    is_festival: bool
    is_monsoon: bool
    staff_count: int
    historical_delay_avg: float
    shelf_availability: float
    arrival_pressure: float
    actual_prep_time: float


class DatasetBuilder:
    """Build training dataset from Kafka events, synthetic data, or feature store."""

    def __init__(self):
        self.records: List[TrainingRecord] = []

    def add_production_record(self, order_events: List[Dict]) -> TrainingRecord:
        created = next((e for e in order_events if e["event_type"] == "ORDER_CREATED"), None)
        ready = next((e for e in order_events if e["event_type"] == "READY_FOR_PICKUP"), None)
        if not created or not ready:
            raise ValueError("Missing ORDER_CREATED or READY_FOR_PICKUP event")
        actual_prep = ready["timestamp"] - created["timestamp"]
        record = TrainingRecord(
            order_id=created["order_id"],
            kitchen_id=created.get("payload", {}).get("kitchen_id", "unknown"),
            kitchen_load=created.get("payload", {}).get("kitchen_load", 5),
            queue_length=created.get("payload", {}).get("queue_length", 3),
            item_complexity=created.get("payload", {}).get("complexity", 2),
            time_of_day=time.localtime(created["timestamp"]).tm_hour,
            day_of_week=time.localtime(created["timestamp"]).tm_wday,
            is_peak_hour=time.localtime(created["timestamp"]).tm_hour in [12, 13, 20, 21],
            is_festival=False,
            is_monsoon=random.random() > 0.85,
            staff_count=created.get("payload", {}).get("staff_count", 3),
            historical_delay_avg=created.get("payload", {}).get("avg_delay", 0.0),
            shelf_availability=0.7,
            arrival_pressure=0.3,
            actual_prep_time=actual_prep,
        )
        self.records.append(record)
        return record

    def generate_synthetic(self, n_samples: int = 5000) -> List[TrainingRecord]:
        np.random.seed(42)
        for i in range(n_samples):
            hour = np.random.choice(range(24), p=[
                0.02, 0.01, 0.01, 0.01, 0.01, 0.02, 0.03, 0.05,
                0.06, 0.07, 0.08, 0.09, 0.12, 0.11, 0.06, 0.04,
                0.03, 0.03, 0.03, 0.04, 0.05, 0.04, 0.03, 0.02])
            kitchen_load = max(0, int(np.random.exponential(5) + np.random.choice([0, 0, 0, 10, 20], p=[0.3, 0.25, 0.2, 0.15, 0.1])))
            queue_length = max(0, int(np.random.exponential(3)))
            is_peak = hour in [12, 13, 20, 21]
            is_festival = np.random.random() > 0.95
            is_monsoon = np.random.random() > 0.85
            actual = (300 + kitchen_load * 8 + queue_length * 25 + np.random.randint(1, 6) * 45 +
                      (150 if is_peak else 0) + (200 if is_festival else 0) + (100 if is_monsoon else 0) -
                      max(0, np.random.randint(2, 8) - 2) * 15 + np.random.normal(0, 30))
            self.records.append(TrainingRecord(
                order_id=f"synth_{i}", kitchen_id=f"kitchen_{np.random.randint(1, 50)}",
                kitchen_load=kitchen_load, queue_length=queue_length,
                item_complexity=np.random.randint(1, 6), time_of_day=hour,
                day_of_week=np.random.randint(0, 7), is_peak_hour=is_peak,
                is_festival=is_festival, is_monsoon=is_monsoon,
                staff_count=np.random.randint(2, 8), historical_delay_avg=np.random.exponential(30),
                shelf_availability=np.random.random(), arrival_pressure=np.random.random(),
                actual_prep_time=max(60, actual)))
        return self.records

    def to_numpy(self):
        X = np.array([[r.kitchen_load, r.queue_length, r.item_complexity, r.time_of_day,
                        int(r.is_peak_hour), int(r.is_festival), int(r.is_monsoon),
                        r.staff_count, r.historical_delay_avg, r.shelf_availability,
                        r.arrival_pressure] for r in self.records])
        y = np.array([r.actual_prep_time for r in self.records])
        return X, y

    def export_csv(self, path: str):
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "order_id", "kitchen_id", "kitchen_load", "queue_length", "item_complexity",
                "time_of_day", "day_of_week", "is_peak_hour", "is_festival", "is_monsoon",
                "staff_count", "historical_delay_avg", "shelf_availability", "arrival_pressure",
                "actual_prep_time"])
            writer.writeheader()
            for r in self.records:
                writer.writerow({k: getattr(r, k) for k in writer.fieldnames})
