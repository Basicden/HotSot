"""HotSot Shelf Service — TTL Engine for shelf expiry management."""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from shared.utils.helpers import now_ts

logger = logging.getLogger("shelf-service.ttl")

SHELF_TTL_SECONDS = {"HOT": 600, "COLD": 900, "AMBIENT": 1200}
WARM_STATION_EXTENSION = 300  # +5 minutes


class TTLEngine:
    """Monitors and manages shelf TTLs for order expiry.

    Warning schedule:
    - 50% TTL: INFO warning (customer notification)
    - 75% TTL: WARNING (staff notification)
    - 90% TTL: CRITICAL (auto-escalation)
    - 100% TTL: EXPIRED (compensation triggered)
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def assign_shelf_ttl(self, shelf_id: str, order_id: str,
                                zone: str, kitchen_id: str,
                                tenant_id: str) -> Dict[str, Any]:
        """Set TTL tracking for a shelf assignment."""
        ttl = SHELF_TTL_SECONDS.get(zone, 600)
        key = f"shelf_ttl:{tenant_id}:{shelf_id}"

        data = {
            "shelf_id": shelf_id,
            "order_id": order_id,
            "zone": zone,
            "kitchen_id": kitchen_id,
            "ttl_seconds": ttl,
            "assigned_at": now_ts(),
            "expires_at": now_ts() + ttl,
            "warning_50_sent": False,
            "warning_75_sent": False,
            "warning_90_sent": False,
        }

        await self._redis.client.setex(key, ttl + 60, json.dumps(data))
        return data

    async def check_shelf_status(self, shelf_id: str, tenant_id: str) -> Optional[Dict]:
        """Check current shelf TTL status."""
        key = f"shelf_ttl:{tenant_id}:{shelf_id}"
        data = await self._redis.client.get(key)
        if not data:
            return None

        info = json.loads(data)
        elapsed = now_ts() - info["assigned_at"]
        remaining = max(0, info["ttl_seconds"] - elapsed)
        pct_used = (elapsed / info["ttl_seconds"]) * 100 if info["ttl_seconds"] > 0 else 100

        return {
            **info,
            "elapsed_seconds": int(elapsed),
            "ttl_remaining": int(remaining),
            "pct_used": round(pct_used, 1),
            "is_expired": remaining <= 0,
            "warning_level": self._get_warning_level(pct_used),
        }

    async def get_expired_shelves(self, kitchen_id: str, tenant_id: str) -> List[Dict]:
        """Find all expired shelves for a kitchen."""
        pattern = f"shelf_ttl:{tenant_id}:*"
        expired = []

        async for key in self._redis.client.scan_iter(match=pattern):
            data = await self._redis.client.get(key)
            if data:
                info = json.loads(data)
                if info.get("kitchen_id") == kitchen_id:
                    remaining = info["ttl_seconds"] - (now_ts() - info["assigned_at"])
                    if remaining <= 0:
                        expired.append(info)

        return expired

    async def extend_ttl(self, shelf_id: str, tenant_id: str,
                         additional_seconds: int = WARM_STATION_EXTENSION) -> bool:
        """Extend shelf TTL (warm station)."""
        key = f"shelf_ttl:{tenant_id}:{shelf_id}"
        data = await self._redis.client.get(key)
        if not data:
            return False

        info = json.loads(data)
        info["ttl_seconds"] += additional_seconds
        info["expires_at"] = info["assigned_at"] + info["ttl_seconds"]
        info["warning_50_sent"] = False
        info["warning_75_sent"] = False
        info["warning_90_sent"] = False

        await self._redis.client.setex(key, info["ttl_seconds"] + 60, json.dumps(info))
        return True

    @staticmethod
    def _get_warning_level(pct_used: float) -> str:
        if pct_used >= 90:
            return "CRITICAL"
        elif pct_used >= 75:
            return "WARNING"
        elif pct_used >= 50:
            return "INFO"
        return "OK"
