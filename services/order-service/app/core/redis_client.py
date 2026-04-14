"""HotSot Order Service — Redis Client (Hot State Layer)."""

import json
from typing import Dict, Any, Optional
import redis.asyncio as aioredis

from app.core.config import config

redis_client: Optional[aioredis.Redis] = None


async def init_redis():
    """Initialize Redis connection."""
    global redis_client
    redis_client = aioredis.from_url(
        config.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=50,
    )


async def close_redis():
    """Close Redis connection."""
    global redis_client
    if redis_client:
        await redis_client.close()


async def set_order_state(order_id: str, state: Dict[str, Any], ttl: int = 3600):
    """Set order state in Redis for fast reads."""
    if redis_client is None:
        return
    key = f"order:{order_id}:state"
    await redis_client.setex(key, ttl, json.dumps(state, default=str))


async def get_order_state(order_id: str) -> Optional[Dict[str, Any]]:
    """Get order state from Redis cache."""
    if redis_client is None:
        return None
    key = f"order:{order_id}:state"
    data = await redis_client.get(key)
    if data:
        return json.loads(data)
    return None


async def increment_kitchen_load(kitchen_id: str) -> int:
    """Increment kitchen load counter in Redis."""
    if redis_client is None:
        return 0
    key = f"kitchen:{kitchen_id}:load"
    return await redis_client.incr(key)


async def decrement_kitchen_load(kitchen_id: str) -> int:
    """Decrement kitchen load counter in Redis."""
    if redis_client is None:
        return 0
    key = f"kitchen:{kitchen_id}:load"
    return await redis_client.decr(key)


async def get_kitchen_load(kitchen_id: str) -> int:
    """Get current kitchen load from Redis."""
    if redis_client is None:
        return 0
    key = f"kitchen:{kitchen_id}:load"
    val = await redis_client.get(key)
    return int(val) if val else 0


async def acquire_lock(key: str, value: str, ttl: int = 30) -> bool:
    """Acquire a distributed lock using Redis SETNX."""
    if redis_client is None:
        return True  # Fallback: allow operation if Redis unavailable
    return await redis_client.set(key, value, nx=True, ex=ttl)


async def release_lock(key: str, value: str) -> bool:
    """Release a distributed lock (check value before deleting)."""
    if redis_client is None:
        return True
    lua_script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    result = await redis_client.eval(lua_script, 1, key, value)
    return result == 1
