"""HotSot Kitchen Service — Redis Client."""

import json
from typing import Optional, Dict, Any
import redis.asyncio as aioredis

redis_client: Optional[aioredis.Redis] = None


async def init_redis():
    global redis_client
    redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)


async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()


async def get_kitchen_load(kitchen_id: str) -> int:
    if redis_client is None:
        return 0
    val = await redis_client.get(f"kitchen:{kitchen_id}:load")
    return int(val) if val else 0


async def set_kitchen_load(kitchen_id: str, load: int):
    if redis_client is None:
        return
    await redis_client.set(f"kitchen:{kitchen_id}:load", str(load))
