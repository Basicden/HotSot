"""HotSot Shelf Service — TTL Enforcement Engine."""

import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field


@dataclass
class ShelfSlot:
    """Physical shelf slot model."""
    shelf_id: str
    kitchen_id: str
    temperature_zone: str = "HOT"  # HOT, COLD, AMBIENT
    capacity: int = 1
    ttl_seconds: int = 600
    status: str = "AVAILABLE"  # AVAILABLE, OCCUPIED, EXPIRED, MAINTENANCE
    current_order_id: Optional[str] = None
    assigned_at: Optional[float] = None

    @property
    def is_available(self) -> bool:
        return self.status == "AVAILABLE"

    @property
    def remaining_ttl(self) -> int:
        if not self.assigned_at:
            return self.ttl_seconds
        elapsed = time.time() - self.assigned_at
        return max(0, int(self.ttl_seconds - elapsed))

    @property
    def is_expired(self) -> bool:
        return self.remaining_ttl <= 0 and self.status == "OCCUPIED"

    def assign(self, order_id: str) -> bool:
        if not self.is_available:
            return False
        self.current_order_id = order_id
        self.status = "OCCUPIED"
        self.assigned_at = time.time()
        return True

    def release(self) -> Optional[str]:
        order_id = self.current_order_id
        self.current_order_id = None
        self.status = "AVAILABLE"
        self.assigned_at = None
        return order_id

    def mark_expired(self):
        self.status = "EXPIRED"


class TTLEngine:
    """
    Shelf TTL enforcement engine.
    India-specific: HOT=600s, COLD=900s, AMBIENT=1200s
    Warning at 70% TTL, Critical at 90%, Auto-expiry triggers compensation.
    """

    TTL_BY_ZONE = {"HOT": 600, "COLD": 900, "AMBIENT": 1200}
    WARNING_THRESHOLD = 0.70
    CRITICAL_THRESHOLD = 0.90

    def __init__(self):
        self.shelves: Dict[str, ShelfSlot] = {}

    def register_shelf(self, shelf_id: str, kitchen_id: str, zone: str = "HOT"):
        ttl = self.TTL_BY_ZONE.get(zone, 600)
        self.shelves[shelf_id] = ShelfSlot(shelf_id=shelf_id, kitchen_id=kitchen_id, temperature_zone=zone, ttl_seconds=ttl)

    def assign_order(self, shelf_id: str, order_id: str) -> bool:
        shelf = self.shelves.get(shelf_id)
        if not shelf:
            return False
        return shelf.assign(order_id)

    def release_shelf(self, shelf_id: str) -> Optional[str]:
        shelf = self.shelves.get(shelf_id)
        if not shelf:
            return None
        return shelf.release()

    def find_available(self, kitchen_id: str, zone: str = None) -> Optional[str]:
        for shelf in self.shelves.values():
            if shelf.kitchen_id != kitchen_id:
                continue
            if zone and shelf.temperature_zone != zone:
                continue
            if shelf.is_available:
                return shelf.shelf_id
        return None

    def check_expiry_warnings(self, kitchen_id: str) -> List[Dict[str, Any]]:
        warnings = []
        for shelf in self.shelves.values():
            if shelf.kitchen_id != kitchen_id or shelf.status != "OCCUPIED":
                continue
            consumed = 1.0 - (shelf.remaining_ttl / shelf.ttl_seconds)
            if consumed >= self.CRITICAL_THRESHOLD:
                warnings.append({"shelf_id": shelf.shelf_id, "order_id": shelf.current_order_id, "level": "CRITICAL", "remaining_seconds": shelf.remaining_ttl})
            elif consumed >= self.WARNING_THRESHOLD:
                warnings.append({"shelf_id": shelf.shelf_id, "order_id": shelf.current_order_id, "level": "WARNING", "remaining_seconds": shelf.remaining_ttl})
        return warnings

    def process_expired(self, kitchen_id: str) -> List[Dict[str, Any]]:
        expired = []
        for shelf in self.shelves.values():
            if shelf.kitchen_id != kitchen_id:
                continue
            if shelf.is_expired:
                expired.append({"shelf_id": shelf.shelf_id, "order_id": shelf.current_order_id, "ttl_exceeded_by": abs(shelf.remaining_ttl)})
                shelf.mark_expired()
        return expired


ttl_engine = TTLEngine()
