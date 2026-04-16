"""HotSot ETA Service — Redis Client (Fail-CLOSED).

Fail-CLOSED semantics:
    When Redis is unavailable, operations return safe sentinel values and log
    warnings rather than silently succeeding or returning misleading defaults.

    - cache_eta:         Returns False when Redis is down (write DENIED).
    - get_cached_eta:    Returns None when Redis is down (cache MISS — caller
                         must fall back to database, not assume stale data is valid).
    - get_kitchen_load:  Returns -1 when Redis is down (load UNKNOWN — callers
                         must NOT interpret -1 as "no load"; instead they should
                         treat it as a signal to query the database directly).

Design Rationale:
    Returning 0 for kitchen load when Redis is down would mislead the ETA
    predictor into assuming the kitchen is idle, producing dangerously
    optimistic estimates.  Returning -1 forces callers to acknowledge that
    the real load is unknown and to degrade gracefully.
"""

import json
import logging
from typing import Optional, Dict, Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import config

logger = logging.getLogger("eta-service.redis")

redis_client: Optional[aioredis.Redis] = None


async def init_redis():
    """Initialize the async Redis connection pool."""
    global redis_client
    try:
        redis_client = aioredis.from_url(
            config.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=30,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            retry_on_timeout=True,
        )
        # Verify connection
        await redis_client.ping()
        logger.info("eta_redis_connected")
    except RedisError as exc:
        logger.error("eta_redis_connection_failed: %s", exc)
        redis_client = None


async def close_redis():
    """Close the Redis connection gracefully."""
    global redis_client
    if redis_client:
        try:
            await redis_client.close()
        except Exception as exc:
            logger.warning("eta_redis_close_error: %s", exc)
        redis_client = None


async def cache_eta(order_id: str, eta_data: Dict[str, Any], ttl: int = 120) -> bool:
    """Cache ETA prediction for an order.

    Fail-CLOSED: Returns False when Redis is unavailable.  The caller must
    not assume the ETA was cached — subsequent reads will fall through to
    the database, which is the correct degradation path.

    Args:
        order_id: The order identifier.
        eta_data: ETA prediction payload (will be JSON-serialized).
        ttl: Cache time-to-live in seconds (default: 120).

    Returns:
        True if cached successfully, False on Redis failure.
    """
    if redis_client is None:
        logger.warning(
            "cache_eta: Redis unavailable — cache write DENIED for order=%s", order_id
        )
        return False
    try:
        await redis_client.setex(
            f"eta:{order_id}", ttl, json.dumps(eta_data, default=str)
        )
        return True
    except RedisError as exc:
        logger.warning(
            "cache_eta: Redis error — cache write DENIED for order=%s: %s",
            order_id, exc,
        )
        return False


async def get_cached_eta(order_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve cached ETA prediction for an order.

    Fail-CLOSED: Returns None when Redis is unavailable.  This is the same
    return value as a cache miss, but the caller must be aware that a Redis
    outage means the cache is unreliable — not that the data doesn't exist.

    Args:
        order_id: The order identifier.

    Returns:
        Deserialized ETA data dict, or None if not found / Redis unavailable.
    """
    if redis_client is None:
        logger.warning(
            "get_cached_eta: Redis unavailable — cache MISS for order=%s (fail-CLOSED)",
            order_id,
        )
        return None
    try:
        data = await redis_client.get(f"eta:{order_id}")
        if data is None:
            return None
        return json.loads(data)
    except (RedisError, json.JSONDecodeError) as exc:
        logger.warning(
            "get_cached_eta: Redis error — cache MISS for order=%s: %s",
            order_id, exc,
        )
        return None


async def get_kitchen_load(kitchen_id: str) -> int:
    """Get current kitchen load from Redis.

    Fail-CLOSED: Returns -1 when Redis is unavailable.  Callers MUST check
    for -1 and treat it as "load unknown" rather than interpreting it as
    "no load".  Returning 0 would dangerously mislead the ETA predictor
    into assuming the kitchen is idle, producing optimistic estimates that
    could cause SLA breaches.

    Args:
        kitchen_id: The kitchen identifier.

    Returns:
        Load count (>= 0) if available, -1 if Redis is unavailable.
    """
    if redis_client is None:
        logger.warning(
            "get_kitchen_load: Redis unavailable — returning -1 (UNKNOWN) for kitchen=%s",
            kitchen_id,
        )
        return -1
    try:
        val = await redis_client.get(f"kitchen:{kitchen_id}:load")
        return int(val) if val else 0
    except (RedisError, ValueError) as exc:
        logger.warning(
            "get_kitchen_load: Redis error — returning -1 (UNKNOWN) for kitchen=%s: %s",
            kitchen_id, exc,
        )
        return -1
