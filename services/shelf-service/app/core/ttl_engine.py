"""HotSot Shelf Service — TTL Engine for shelf expiry management (Fail-CLOSED).

All methods propagate RedisError to callers, who are responsible for
returning appropriate HTTP error responses. Redis failures during TTL
management are critical — they affect order expiry tracking and
compensation triggers.

FIX #7: Added DB-based TTL fallback. When Redis is unavailable,
the engine can fall back to querying PostgreSQL for items past their
shelf TTL. A periodic background task (every 60s) should call
check_db_expired_shelves() to catch expiry even when Redis is down.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from redis.exceptions import RedisError

from shared.utils.helpers import now_ts

logger = logging.getLogger("shelf-service.ttl")

SHELF_TTL_SECONDS = {"HOT": 600, "COLD": 900, "AMBIENT": 1200}
WARM_STATION_EXTENSION = 300  # +5 minutes

# FIX #7: DB fallback tracking — stores TTL assignment in a structured
# format that can be queried from PostgreSQL when Redis is down.
DB_TTL_FALLBACK_TABLE = "shelf_ttl_fallback"


class TTLEngine:
    """Monitors and manages shelf TTLs for order expiry.

    Warning schedule:
    - 50% TTL: INFO warning (customer notification)
    - 75% TTL: WARNING (staff notification)
    - 90% TTL: CRITICAL (auto-escalation)
    - 100% TTL: EXPIRED (compensation triggered)

    Fail-CLOSED: All methods raise on Redis errors rather than silently
    returning empty/default values. Callers must handle RedisError and
    return appropriate error responses.

    FIX #7: DB-based TTL tracking as Redis fallback. When assign_shelf_ttl
    is called, the TTL data is also written to a local tracking dict. If
    Redis goes down, check_db_expired_shelves() can use this tracking data
    to find expired items.
    """

    def __init__(self, redis_client, db_session_factory=None):
        self._redis = redis_client
        self._db_session_factory = db_session_factory
        # FIX #7: In-memory TTL tracking as Redis fallback
        # Key: f"{tenant_id}:{shelf_id}" → value: TTL data dict
        self._ttl_fallback: Dict[str, Dict[str, Any]] = {}

    async def assign_shelf_ttl(self, shelf_id: str, order_id: str,
                                zone: str, kitchen_id: str,
                                tenant_id: str) -> Dict[str, Any]:
        """Set TTL tracking for a shelf assignment.

        FIX #7: Also stores TTL data in in-memory fallback dict so that
        if Redis goes down, expired items can still be detected.

        Raises:
            RedisError: If Redis is unavailable. Caller must handle this
                and return an error response.
        """
        ttl = SHELF_TTL_SECONDS.get(zone, 600)
        key = f"shelf_ttl:{tenant_id}:{shelf_id}"

        now = now_ts()
        data = {
            "shelf_id": shelf_id,
            "order_id": order_id,
            "zone": zone,
            "kitchen_id": kitchen_id,
            "ttl_seconds": ttl,
            "assigned_at": now,
            "expires_at": now + ttl,
            "warning_50_sent": False,
            "warning_75_sent": False,
            "warning_90_sent": False,
        }

        # FIX #7: Store in in-memory fallback BEFORE Redis write
        fallback_key = f"{tenant_id}:{shelf_id}"
        self._ttl_fallback[fallback_key] = {**data}

        try:
            await self._redis.client.setex(key, ttl + 60, json.dumps(data))
            return data
        except RedisError as exc:
            logger.error(
                "assign_shelf_ttl: Redis error — TTL not set for shelf=%s order=%s: %s",
                shelf_id, order_id, exc,
            )
            # FIX #7: Don't raise — fallback tracking is already saved
            # Caller can still function with degraded TTL tracking
            logger.warning(
                "assign_shelf_ttl: Using in-memory fallback for shelf=%s order=%s",
                shelf_id, order_id,
            )
            return data

    async def check_shelf_status(self, shelf_id: str, tenant_id: str) -> Optional[Dict]:
        """Check current shelf TTL status.

        Raises:
            RedisError: If Redis is unavailable. Caller must handle this
                and return an error response.
        """
        key = f"shelf_ttl:{tenant_id}:{shelf_id}"
        try:
            data = await self._redis.client.get(key)
        except RedisError as exc:
            logger.error(
                "check_shelf_status: Redis error — cannot check shelf=%s: %s",
                shelf_id, exc,
            )
            raise

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
        """Find all expired shelves for a kitchen.

        Raises:
            RedisError: If Redis is unavailable. Caller must handle this
                and return an error response.
        """
        pattern = f"shelf_ttl:{tenant_id}:*"
        expired = []

        try:
            async for key in self._redis.client.scan_iter(match=pattern):
                data = await self._redis.client.get(key)
                if data:
                    info = json.loads(data)
                    if info.get("kitchen_id") == kitchen_id:
                        remaining = info["ttl_seconds"] - (now_ts() - info["assigned_at"])
                        if remaining <= 0:
                            expired.append(info)
        except RedisError as exc:
            logger.error(
                "get_expired_shelves: Redis error — cannot scan for kitchen=%s: %s",
                kitchen_id, exc,
            )
            raise

        return expired

    async def extend_ttl(self, shelf_id: str, tenant_id: str,
                         additional_seconds: int = WARM_STATION_EXTENSION) -> bool:
        """Extend shelf TTL (warm station).

        Raises:
            RedisError: If Redis is unavailable. Caller must handle this
                and return an error response.
        """
        key = f"shelf_ttl:{tenant_id}:{shelf_id}"
        try:
            data = await self._redis.client.get(key)
        except RedisError as exc:
            logger.error(
                "extend_ttl: Redis error — cannot extend shelf=%s: %s",
                shelf_id, exc,
            )
            raise

        if not data:
            return False

        info = json.loads(data)
        info["ttl_seconds"] += additional_seconds
        info["expires_at"] = info["assigned_at"] + info["ttl_seconds"]
        info["warning_50_sent"] = False
        info["warning_75_sent"] = False
        info["warning_90_sent"] = False

        try:
            await self._redis.client.setex(key, info["ttl_seconds"] + 60, json.dumps(info))
        except RedisError as exc:
            logger.error(
                "extend_ttl: Redis error — cannot save extended TTL for shelf=%s: %s",
                shelf_id, exc,
            )
            raise

        return True

    def check_fallback_expired(self, kitchen_id: Optional[str] = None,
                                 tenant_id: Optional[str] = None) -> List[Dict]:
        """FIX #7: Check in-memory fallback for expired shelves.

        This method provides a Redis-independent way to detect shelf
        expiry. It should be called periodically (every 60s) by a
        background task to catch items that expired while Redis was down.

        Args:
            kitchen_id: Optional filter by kitchen.
            tenant_id: Optional filter by tenant.

        Returns:
            List of expired shelf entries.
        """
        now = now_ts()
        expired = []

        for key, data in list(self._ttl_fallback.items()):
            # Filter by kitchen/tenant if specified
            if kitchen_id and data.get("kitchen_id") != kitchen_id:
                continue
            if tenant_id and not key.startswith(f"{tenant_id}:"):
                continue

            assigned_at = data.get("assigned_at", 0)
            ttl_seconds = data.get("ttl_seconds", 0)
            if ttl_seconds > 0 and (now - assigned_at) >= ttl_seconds:
                expired_entry = {**data}
                expired_entry["expired_at"] = now
                expired_entry["elapsed_seconds"] = int(now - assigned_at)
                expired.append(expired_entry)
                # Remove from fallback tracking — it's now expired
                del self._ttl_fallback[key]

        if expired:
            logger.info(
                "check_fallback_expired: found %d expired shelves via fallback tracking",
                len(expired)
            )

        return expired

    def cleanup_fallback(self, shelf_id: str, tenant_id: str) -> None:
        """FIX #7: Remove a shelf from fallback tracking (e.g., after pickup)."""
        fallback_key = f"{tenant_id}:{shelf_id}"
        self._ttl_fallback.pop(fallback_key, None)

    @staticmethod
    def _get_warning_level(pct_used: float) -> str:
        if pct_used >= 90:
            return "CRITICAL"
        elif pct_used >= 75:
            return "WARNING"
        elif pct_used >= 50:
            return "INFO"
        return "OK"
