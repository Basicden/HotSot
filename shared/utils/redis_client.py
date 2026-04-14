"""
HotSot Redis Client — Hot state layer for real-time data.
"""

import json
import logging
from datetime import datetime
from uuid import UUID

import redis.asyncio as aioredis

from shared.utils.config import settings

logger = logging.getLogger(__name__)


class RedisClient:
    """
    Redis client for hot state management.
    
    Key patterns:
    - order:{order_id}:status    → current order status
    - kitchen:{kitchen_id}:load  → current kitchen load
    - shelf:{shelf_id}:lock      → distributed shelf lock
    - shelf:{shelf_id}:order     → order_id occupying shelf
    - eta:{order_id}             → cached ETA prediction
    """

    def __init__(self):
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info("Redis connected")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.close()

    @property
    def client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._client

    # ─── Order State ───

    async def set_order_status(self, order_id: UUID, status: str) -> None:
        await self.client.set(f"order:{order_id}:status", status)

    async def get_order_status(self, order_id: UUID) -> str | None:
        return await self.client.get(f"order:{order_id}:status")

    # ─── Kitchen Load ───

    async def set_kitchen_load(self, kitchen_id: UUID, load_data: dict) -> None:
        await self.client.set(
            f"kitchen:{kitchen_id}:load", json.dumps(load_data), ex=60
        )

    async def get_kitchen_load(self, kitchen_id: UUID) -> dict | None:
        data = await self.client.get(f"kitchen:{kitchen_id}:load")
        return json.loads(data) if data else None

    # ─── Shelf Locking (Distributed Lock via SETNX) ───

    async def acquire_shelf_lock(
        self, shelf_id: str, order_id: UUID, ttl: int = 600
    ) -> bool:
        """Try to acquire a shelf lock. Returns True if successful."""
        result = await self.client.set(
            f"shelf:{shelf_id}:lock",
            str(order_id),
            nx=True,  # Only set if not exists
            ex=ttl,   # Auto-expire after TTL
        )
        return result is True

    async def release_shelf_lock(self, shelf_id: str, order_id: UUID) -> bool:
        """Release a shelf lock (only if we own it)."""
        current = await self.client.get(f"shelf:{shelf_id}:lock")
        if current == str(order_id):
            await self.client.delete(f"shelf:{shelf_id}:lock")
            return True
        return False

    async def get_shelf_occupant(self, shelf_id: str) -> str | None:
        """Get the order_id currently occupying a shelf."""
        return await self.client.get(f"shelf:{shelf_id}:lock")

    # ─── ETA Cache ───

    async def cache_eta(self, order_id: UUID, eta_data: dict, ttl: int = 120) -> None:
        await self.client.set(f"eta:{order_id}", json.dumps(eta_data), ex=ttl)

    async def get_cached_eta(self, order_id: UUID) -> dict | None:
        data = await self.client.get(f"eta:{order_id}")
        return json.loads(data) if data else None


# Singleton
redis_client = RedisClient()
