"""
HotSot Arrival Service — Deduplication Engine
==============================================
Prevents duplicate arrival events using a Redis SETNX-based approach.

The dedup key is computed as:
    SHA256(user_id + order_id + floor(timestamp / dedup_window_s))

This means the same user+order pair can only produce one arrival event
per time window (default 30 s).  After the window rolls forward a new
arrival is permitted — this is intentional: a user might leave and
return.

Redis keys are set with a TTL slightly longer than the window to ensure
cleanup even if the clock drifts.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as aioredis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ── Key prefix ─────────────────────────────────────────────────────────
_DEDUP_PREFIX = "hotsot:arrival:dedup"
_ARRIVAL_PREFIX = "hotsot:arrival:active"


# ── Data models ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DedupResult:
    """Outcome of a dedup check."""
    is_duplicate: bool
    dedup_key: str


@dataclass
class ActiveArrival:
    """Tracks an active (current) arrival for an order."""
    order_id: str
    user_id: str
    kitchen_id: str
    detected_at: float  # Unix timestamp
    strength: str       # "hard" | "soft"
    arrival_type: str   # "gps" | "qr"


# ── Dedup engine ───────────────────────────────────────────────────────

class ArrivalDedupEngine:
    """
    Redis-backed arrival deduplication.

    Uses SETNX (SET if Not eXists) to atomically claim a dedup key.
    If the key already exists the arrival is considered a duplicate.
    """

    def __init__(self, redis: aioredis.Redis | None = None) -> None:
        self._redis = redis
        self._settings = get_settings()

    # ── Redis lazy init ────────────────────────────────────────────────

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                self._settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    # ── Key generation ─────────────────────────────────────────────────

    @staticmethod
    def _make_dedup_key(
        user_id: str,
        order_id: str,
        timestamp: float,
        window_s: int,
    ) -> str:
        """
        Build the dedup key: SHA256(user_id + order_id + bucket).
        The bucket is ``floor(timestamp / window_s)`` so that all
        timestamps within the same window hash to the same key.
        """
        bucket = int(timestamp // window_s)
        raw = f"{user_id}:{order_id}:{bucket}"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return f"{_DEDUP_PREFIX}:{digest}"

    @staticmethod
    def _arrival_key(order_id: str) -> str:
        """Redis key for active arrival tracking."""
        return f"{_ARRIVAL_PREFIX}:{order_id}"

    # ── Dedup check ────────────────────────────────────────────────────

    async def check_and_record(
        self,
        user_id: str,
        order_id: str,
        timestamp: float | None = None,
    ) -> DedupResult:
        """
        Atomically check if this arrival is a duplicate and, if not,
        record it so subsequent calls within the same window are deduped.

        Uses ``SET key value NX EX ttl`` — a single atomic command.

        Returns
        -------
        DedupResult
            is_duplicate=True  → skip this arrival
            is_duplicate=False → first occurrence, proceed
        """
        if timestamp is None:
            timestamp = time.time()

        window = self._settings.dedup_window_s
        key = self._make_dedup_key(user_id, order_id, timestamp, window)
        ttl = self._settings.redis_dedup_ttl

        r = await self._get_redis()
        # SETNX with expiry — returns True if the key was set (first occurrence)
        was_set = await r.set(key, "1", nx=True, ex=ttl)

        is_duplicate = not was_set
        if is_duplicate:
            logger.debug(
                "Duplicate arrival detected: user=%s order=%s key=%s",
                user_id, order_id, key,
            )

        return DedupResult(is_duplicate=is_duplicate, dedup_key=key)

    # ── Active arrival tracking ────────────────────────────────────────

    async def set_active_arrival(self, arrival: ActiveArrival) -> None:
        """
        Store the current arrival state for an order so the status
        endpoint can look it up quickly.
        """
        r = await self._get_redis()
        key = self._arrival_key(arrival.order_id)
        ttl = self._settings.redis_arrival_ttl

        mapping = {
            "order_id": arrival.order_id,
            "user_id": arrival.user_id,
            "kitchen_id": arrival.kitchen_id,
            "detected_at": str(arrival.detected_at),
            "strength": arrival.strength,
            "arrival_type": arrival.arrival_type,
        }
        await r.hset(key, mapping=mapping)  # type: ignore[arg-type]
        await r.expire(key, ttl)

    async def get_active_arrival(self, order_id: str) -> Optional[dict]:
        """Retrieve the active arrival data for an order, or None."""
        r = await self._get_redis()
        key = self._arrival_key(order_id)
        data = await r.hgetall(key)
        return data if data else None

    async def get_waiting_for_kitchen(self, kitchen_id: str) -> list[dict]:
        """
        Scan for all active arrivals belonging to a specific kitchen.
        Uses SCAN (not KEYS) for production safety.
        """
        r = await self._get_redis()
        pattern = f"{_ARRIVAL_PREFIX}:*"
        results: list[dict] = []

        async for cursor in r.scan_iter(match=pattern, count=50):
            # cursor is the full key name
            data = await r.hgetall(cursor)
            if data and data.get("kitchen_id") == kitchen_id:
                results.append(data)

        return results

    async def remove_active_arrival(self, order_id: str) -> None:
        """Remove an active arrival (e.g. after handoff is complete)."""
        r = await self._get_redis()
        key = self._arrival_key(order_id)
        await r.delete(key)
