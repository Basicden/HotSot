"""HotSot Shelf Service — Redis Client for Distributed Locking + TTL."""

import json
import time
from typing import Optional, Dict, Any, List
import redis.asyncio as aioredis
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/2")
redis_client: Optional[aioredis.Redis] = None


async def init_redis():
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True, max_connections=30)


async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()


async def acquire_shelf_lock(shelf_id: str, order_id: str, ttl: int = 600) -> bool:
    """Acquire distributed lock on shelf using SETNX."""
    if redis_client is None:
        return True
    return await redis_client.set(f"shelf:{shelf_id}:lock", order_id, nx=True, ex=ttl)


async def release_shelf_lock(shelf_id: str, order_id: str) -> bool:
    """Release shelf lock only if we own it."""
    if redis_client is None:
        return True
    lua = '''
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    '''
    result = await redis_client.eval(lua, 1, f"shelf:{shelf_id}:lock", order_id)
    return result == 1


async def get_shelf_status(shelf_id: str) -> Optional[Dict[str, Any]]:
    if redis_client is None:
        return None
    data = await redis_client.get(f"shelf:{shelf_id}:status")
    return json.loads(data) if data else None


async def set_shelf_status(shelf_id: str, status: Dict[str, Any], ttl: int = 7200):
    if redis_client is None:
        return
    await redis_client.setex(f"shelf:{shelf_id}:status", ttl, json.dumps(status, default=str))


async def get_shelf_ttl(shelf_id: str) -> int:
    if redis_client is None:
        return 0
    return await redis_client.ttl(f"shelf:{shelf_id}:lock")


async def get_expired_shelves(kitchen_id: str) -> List[str]:
    expired = []
    for i in range(1, 21):
        shelf_id = f"{kitchen_id}-A{i}"
        ttl = await get_shelf_ttl(shelf_id)
        status = await get_shelf_status(shelf_id)
        if status and status.get("status") == "OCCUPIED" and ttl <= 0:
            expired.append(shelf_id)
    return expired
