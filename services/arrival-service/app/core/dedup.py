"""HotSot Arrival Service — Deduplication Engine (Fail-CLOSED).

Fail-CLOSED semantics:
    When Redis is unavailable, deduplication defaults to DENYING operations
    rather than allowing potentially duplicate requests through.

    - is_duplicate:           Returns True when Redis is down (treat as
                              duplicate → block the request). This prevents
                              duplicate arrivals from being processed.
    - check_idempotency_key:  Raises RuntimeError when Redis is down.
                              Losing idempotency tracking is dangerous.
    - store_idempotency_key:  Raises RuntimeError when Redis is down.
                              Silently losing idempotency data could allow
                              duplicate processing on retries.
"""

import hashlib
import logging
from typing import Optional

from redis.exceptions import RedisError

logger = logging.getLogger("arrival-service.dedup")


class ArrivalDeduplicator:
    """Deduplicate arrival signals using Redis with time-window hashing.

    Strategy: hash(user_id + order_id + floor(timestamp/30s))
    This means the same arrival event within 30 seconds is treated as a duplicate.

    Fail-CLOSED: When Redis is unavailable, arrivals are treated as duplicates
    to prevent duplicate processing. This is the safe default — it may cause
    some legitimate re-tries to be blocked, but it prevents duplicate entries.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client

    async def is_duplicate(self, user_id: str, order_id: str,
                           timestamp: float, tenant_id: str) -> bool:
        """Check if this arrival signal is a duplicate.

        Fail-CLOSED: Returns True when Redis is unavailable, blocking
        potentially duplicate requests. This is the safe default — blocking
        a legitimate retry is better than processing a duplicate arrival.

        Returns:
            True if duplicate or Redis unavailable, False if not a duplicate.
        """
        if not self._redis:
            logger.warning(
                "is_duplicate: Redis unavailable — treating as DUPLICATE (fail-closed) "
                "user=%s order=%s",
                user_id, order_id,
            )
            return True

        # 30-second time bucket
        time_bucket = int(timestamp) // 30
        raw = f"{user_id}:{order_id}:{time_bucket}"
        dedup_key = hashlib.sha256(raw.encode()).hexdigest()[:32]

        redis_key = f"arrival_dedup:{tenant_id}:{dedup_key}"

        try:
            # Check if key exists
            exists = await self._redis.client.exists(redis_key)
            if exists:
                logger.info("arrival_dedup_hit user=%s order=%s", user_id, order_id)
                return True

            # Mark as seen (TTL = 60 seconds to cover 2 time buckets)
            await self._redis.client.setex(redis_key, 60, "1")
            return False
        except RedisError as exc:
            logger.warning(
                "is_duplicate: Redis error — treating as DUPLICATE (fail-closed) "
                "user=%s order=%s: %s",
                user_id, order_id, exc,
            )
            return True

    async def check_idempotency_key(self, key: str, tenant_id: str) -> Optional[str]:
        """Check if an idempotency key was already processed.

        Fail-CLOSED: Raises RuntimeError when Redis is unavailable.
        Losing idempotency tracking could lead to duplicate processing,
        so we fail hard rather than returning None (which would allow
        the request to proceed as if never seen before).
        """
        if not self._redis:
            logger.error(
                "check_idempotency_key: Redis unavailable — cannot verify idempotency key=%s",
                key,
            )
            raise RuntimeError("Redis unavailable — cannot verify idempotency key")

        redis_key = f"arrival_idem:{tenant_id}:{key}"
        try:
            result = await self._redis.client.get(redis_key)
            return result
        except RedisError as exc:
            logger.error(
                "check_idempotency_key: Redis error — cannot verify idempotency key=%s: %s",
                key, exc,
            )
            raise RuntimeError(f"Redis error — cannot verify idempotency key: {exc}") from exc

    async def store_idempotency_key(self, key: str, response: str,
                                      tenant_id: str, ttl: int = 86400) -> None:
        """Store processed idempotency key with response.

        Fail-CLOSED: Raises RuntimeError when Redis is unavailable.
        Silently losing idempotency data means future retries of the
        same request would not be recognized as duplicates, leading to
        duplicate processing.
        """
        if not self._redis:
            logger.error(
                "store_idempotency_key: Redis unavailable — CANNOT store idempotency key=%s. "
                "Future retries of this request may be processed as duplicates!",
                key,
            )
            raise RuntimeError("Redis unavailable — cannot store idempotency key")

        redis_key = f"arrival_idem:{tenant_id}:{key}"
        try:
            await self._redis.client.setex(redis_key, ttl, response)
        except RedisError as exc:
            logger.error(
                "store_idempotency_key: Redis error — CANNOT store idempotency key=%s: %s. "
                "Future retries of this request may be processed as duplicates!",
                key, exc,
            )
            raise RuntimeError(f"Redis error — cannot store idempotency key: {exc}") from exc
