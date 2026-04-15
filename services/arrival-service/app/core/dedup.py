"""HotSot Arrival Service — Deduplication Engine."""

import hashlib
import logging
from typing import Optional

logger = logging.getLogger("arrival-service.dedup")


class ArrivalDeduplicator:
    """Deduplicate arrival signals using Redis with time-window hashing.

    Strategy: hash(user_id + order_id + floor(timestamp/30s))
    This means the same arrival event within 30 seconds is treated as a duplicate.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client

    async def is_duplicate(self, user_id: str, order_id: str,
                           timestamp: float, tenant_id: str) -> bool:
        """Check if this arrival signal is a duplicate."""
        if not self._redis:
            return False

        # 30-second time bucket
        time_bucket = int(timestamp) // 30
        raw = f"{user_id}:{order_id}:{time_bucket}"
        dedup_key = hashlib.sha256(raw.encode()).hexdigest()[:32]

        redis_key = f"arrival_dedup:{tenant_id}:{dedup_key}"

        # Check if key exists
        exists = await self._redis.client.exists(redis_key)
        if exists:
            logger.info("arrival_dedup_hit", user_id=user_id, order_id=order_id)
            return True

        # Mark as seen (TTL = 60 seconds to cover 2 time buckets)
        await self._redis.client.setex(redis_key, 60, "1")
        return False

    async def check_idempotency_key(self, key: str, tenant_id: str) -> Optional[str]:
        """Check if an idempotency key was already processed."""
        if not self._redis:
            return None

        redis_key = f"arrival_idem:{tenant_id}:{key}"
        result = await self._redis.client.get(redis_key)
        return result

    async def store_idempotency_key(self, key: str, response: str,
                                      tenant_id: str, ttl: int = 86400) -> None:
        """Store processed idempotency key with response."""
        if not self._redis:
            return

        redis_key = f"arrival_idem:{tenant_id}:{key}"
        await self._redis.client.setex(redis_key, ttl, response)
