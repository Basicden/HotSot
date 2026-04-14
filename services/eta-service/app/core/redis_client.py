"""HotSot ETA Service — Redis Client."""

import json
from typing import Optional, Dict, Any
import redis.asyncio as aioredis
from app.core.config import config

redis_client: Optional[aioredis.Redis] = None


async def init_redis():
    global redis_client
    redis_client = aioredis.from_url(config.REDIS_URL, encoding="utf-8", decode_responses=True)


async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()


async def cache_eta(order_id: str, eta_data: Dict[str, Any], ttl: int = 120):
    if redis_client is None:
        return
    await redis_client.setex(f"eta:{order_id}", ttl, json.dumps(eta_data, default=str))


async def get_cached_eta(order_id: str) -> Optional[Dict[str, Any]]:
    if redis_client is None:
        return None
    data = await redis_client.get(f"eta:{order_id}")
    return json.loads(data) if data else None


async def get_kitchen_load(kitchen_id: str) -> int:
    if redis_client is None:
        return 0
    val = await redis_client.get(f"kitchen:{kitchen_id}:load")
    return int(val) if val else 0
