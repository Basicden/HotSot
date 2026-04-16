"""HotSot Shelf Service — Redis Client for Distributed Locking + TTL (Fail-CLOSED).

Fail-CLOSED semantics:
    When Redis is unavailable, operations are DENIED rather than silently
    allowed. This is especially critical for distributed locks — if Redis
    is down, we MUST NOT grant locks (which would allow concurrent access).

    - acquire_shelf_lock: Returns False when Redis is down (lock DENIED).
    - release_shelf_lock: Returns False when Redis is down (release FAILED — lock remains).
    - get_shelf_status:   Returns None with WARNING when Redis is down.
    - set_shelf_status:   Returns False when Redis is down (write DENIED).
    - get_shelf_ttl:      Returns -1 when Redis is down (unknown, NOT 0).
"""

import json
import logging
from typing import Optional, Dict, Any, List

import redis.asyncio as aioredis
from redis.exceptions import RedisError
import os

logger = logging.getLogger("shelf-service.redis")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/2")
redis_client: Optional[aioredis.Redis] = None


async def init_redis():
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True, max_connections=30)


async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()


async def acquire_shelf_lock(
    shelf_id: str,
    order_id: str,
    ttl: int = 600,
    tenant_id: Optional[str] = None,
) -> bool:
    """Acquire distributed lock on shelf using SETNX.

    Fail-CLOSED: Returns False when Redis is unavailable. When Redis is
    down, we DENY the lock — this prevents concurrent shelf assignments
    which could lead to double-booking or data corruption.

    Args:
        shelf_id: The shelf to lock.
        order_id: The order claiming the shelf (used as lock value for ownership).
        ttl: Lock time-to-live in seconds.
        tenant_id: Tenant for multi-tenancy key namespacing.

    Returns:
        True if lock was acquired, False if Redis is unavailable, lock
        already held, or a Redis error occurs.
    """
    if redis_client is None:
        logger.warning(
            "acquire_shelf_lock: Redis unavailable — lock DENIED for shelf=%s order=%s",
            shelf_id, order_id,
        )
        return False
    try:
        lock_key = f"shelf_lock:{tenant_id}:{shelf_id}" if tenant_id else f"shelf:{shelf_id}:lock"
        acquired = await redis_client.set(lock_key, order_id, nx=True, ex=ttl)
        if not acquired:
            logger.info("acquire_shelf_lock: lock already held for shelf=%s", shelf_id)
        return bool(acquired)
    except RedisError as exc:
        logger.warning(
            "acquire_shelf_lock: Redis error — lock DENIED for shelf=%s order=%s: %s",
            shelf_id, order_id, exc,
        )
        return False


async def release_shelf_lock(
    shelf_id: str,
    order_id: str,
    tenant_id: Optional[str] = None,
) -> bool:
    """Release shelf lock only if we own it (Lua-script compare-and-delete).

    Fail-CLOSED: Returns False when Redis is unavailable. If we cannot
    confirm the lock was released, we report failure — the caller should
    not assume the lock is freed.

    Args:
        shelf_id: The shelf to unlock.
        order_id: The order that holds the lock (must match to release).
        tenant_id: Tenant for multi-tenancy key namespacing.

    Returns:
        True if lock was released, False if Redis is unavailable, we
        didn't own the lock, or a Redis error occurs.
    """
    if redis_client is None:
        logger.warning(
            "release_shelf_lock: Redis unavailable — release FAILED for shelf=%s order=%s",
            shelf_id, order_id,
        )
        return False
    try:
        lock_key = f"shelf_lock:{tenant_id}:{shelf_id}" if tenant_id else f"shelf:{shelf_id}:lock"
        lua = '''
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        '''
        result = await redis_client.eval(lua, 1, lock_key, order_id)
        if result != 1:
            logger.warning(
                "release_shelf_lock: lock not owned or already released for shelf=%s order=%s",
                shelf_id, order_id,
            )
        return result == 1
    except RedisError as exc:
        logger.warning(
            "release_shelf_lock: Redis error — release FAILED for shelf=%s order=%s: %s",
            shelf_id, order_id, exc,
        )
        return False


async def get_shelf_status(shelf_id: str) -> Optional[Dict[str, Any]]:
    """Get shelf status from Redis.

    Fail-CLOSED: Returns None with WARNING when Redis is unavailable.
    Callers must treat None as "status unknown" and not assume the shelf
    is in any particular state.
    """
    if redis_client is None:
        logger.warning("get_shelf_status: Redis unavailable — returning None for shelf=%s", shelf_id)
        return None
    try:
        data = await redis_client.get(f"shelf:{shelf_id}:status")
        return json.loads(data) if data else None
    except RedisError as exc:
        logger.warning("get_shelf_status: Redis error — returning None for shelf=%s: %s", shelf_id, exc)
        return None


async def set_shelf_status(shelf_id: str, status: Dict[str, Any], ttl: int = 7200) -> bool:
    """Set shelf status in Redis.

    Fail-CLOSED: Returns False when Redis is unavailable. The status
    write is denied — callers must not assume the status was persisted.

    Returns:
        True if status was set, False if Redis is unavailable or error occurs.
    """
    if redis_client is None:
        logger.warning("set_shelf_status: Redis unavailable — status write DENIED for shelf=%s", shelf_id)
        return False
    try:
        await redis_client.setex(f"shelf:{shelf_id}:status", ttl, json.dumps(status, default=str))
        return True
    except RedisError as exc:
        logger.warning("set_shelf_status: Redis error — status write DENIED for shelf=%s: %s", shelf_id, exc)
        return False


async def get_shelf_ttl(shelf_id: str) -> int:
    """Get remaining TTL for a shelf lock.

    Fail-CLOSED: Returns -1 when Redis is unavailable. Callers MUST check
    for -1 and treat it as "TTL unknown" rather than assuming no TTL.
    Returning 0 would be misinterpreted as "lock expired" which could
    trigger incorrect expiry actions.

    Returns:
        Remaining TTL in seconds (>= 0) if available, -1 if Redis is
        unavailable, -2 if key does not exist.
    """
    if redis_client is None:
        logger.warning("get_shelf_ttl: Redis unavailable — returning -1 for shelf=%s", shelf_id)
        return -1
    try:
        return await redis_client.ttl(f"shelf:{shelf_id}:lock")
    except RedisError as exc:
        logger.warning("get_shelf_ttl: Redis error — returning -1 for shelf=%s: %s", shelf_id, exc)
        return -1


async def get_expired_shelves(kitchen_id: str) -> List[str]:
    """Find all expired shelves for a kitchen.

    Fail-CLOSED: Returns empty list with WARNING when Redis is unavailable.
    An empty list on Redis failure should not be interpreted as "no expired
    shelves" — callers should check Redis availability separately.
    """
    if redis_client is None:
        logger.warning("get_expired_shelves: Redis unavailable — returning empty list for kitchen=%s", kitchen_id)
        return []
    try:
        expired = []
        for i in range(1, 21):
            shelf_id = f"{kitchen_id}-A{i}"
            ttl = await get_shelf_ttl(shelf_id)
            status = await get_shelf_status(shelf_id)
            if status and status.get("status") == "OCCUPIED" and ttl <= 0:
                expired.append(shelf_id)
        return expired
    except RedisError as exc:
        logger.warning("get_expired_shelves: Redis error for kitchen=%s: %s", kitchen_id, exc)
        return []
