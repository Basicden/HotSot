"""
HotSot Redis Client — Production-grade hot state layer.

Features:
    - Connection pooling with configurable limits
    - Distributed locking with fail-CLOSED semantics
    - Pub/Sub helpers for real-time events
    - Cache helpers with TTL
    - Health check
    - Fail-CLOSED: returns False/errors when Redis is down (NOT True)

CRITICAL DESIGN DECISION: Fail-CLOSED
    When Redis is unavailable, this client returns False for lock acquisition,
    None for cache reads, and raises exceptions for writes. This ensures that
    the system degrades safely — it will NOT grant access or assume cached
    data is valid when Redis is down.

Usage:
    from shared.utils.redis_client import RedisClient

    redis = RedisClient(service_name="order")
    await redis.connect()

    # Distributed lock
    acquired = await redis.acquire_distributed_lock("shelf:slot_1", "order_123", ttl=600)
    if acquired:
        try:
            # ... do work ...
        finally:
            await redis.release_distributed_lock("shelf:slot_1", "order_123")

    # Cache
    await redis.cache_set("eta:order_123", {"seconds": 300}, ttl=120)
    data = await redis.cache_get("eta:order_123")

    # Pub/Sub
    await redis.publish("order_events", {"type": "ORDER_PICKED", "order_id": "..."})
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

import redis.asyncio as aioredis
from redis.asyncio import ConnectionPool
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError

from shared.utils.config import get_settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# LUA SCRIPTS
# ═══════════════════════════════════════════════════════════════

# Atomic lock release: only release if we own the lock
# Returns 1 if released, 0 if not owner (lock was stolen/expired)
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Extend lock TTL: only extend if we still own the lock
_EXTEND_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


class RedisClient:
    """
    Production Redis client for hot state management.

    Key patterns (convention):
        - order:{order_id}:status       → current order status
        - kitchen:{kitchen_id}:load     → current kitchen load
        - shelf:{shelf_id}:lock         → distributed shelf lock
        - shelf:{shelf_id}:order        → order_id occupying shelf
        - eta:{order_id}                → cached ETA prediction
        - lock:{resource_key}           → distributed lock
        - ratelimit:{tenant_id}         → rate limit counter
        - cache:{key}                   → general-purpose cache
    """

    def __init__(self, service_name: str = "hotsot"):
        self._service_name = service_name
        self._client: Optional[aioredis.Redis] = None
        self._pool: Optional[ConnectionPool] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None

    async def connect(self) -> None:
        """
        Establish Redis connection with connection pooling.

        Raises:
            RedisConnectionError: If connection fails.
        """
        svc_settings = get_settings(self._service_name)

        try:
            self._pool = ConnectionPool.from_url(
                svc_settings.REDIS_URL,
                max_connections=svc_settings.REDIS_MAX_CONNECTIONS,
                socket_timeout=svc_settings.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=svc_settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                decode_responses=True,
                retry_on_timeout=True,
            )

            self._client = aioredis.Redis(connection_pool=self._pool)

            # Verify connection
            await self._client.ping()

            logger.info(
                f"Redis connected for service={self._service_name} "
                f"max_connections={svc_settings.REDIS_MAX_CONNECTIONS}"
            )
        except RedisError as e:
            logger.error(f"Redis connection failed for service={self._service_name}: {e}")
            # Set client to None so all operations fail-safe
            self._client = None
            raise

    async def disconnect(self) -> None:
        """Close Redis connection and release pool."""
        if self._pubsub:
            try:
                await self._pubsub.unsubscribe()
                await self._pubsub.close()
            except Exception as e:
                logger.warning(f"Error closing pubsub: {e}")
            self._pubsub = None

        if self._client:
            try:
                await self._client.close()
            except Exception as e:
                logger.warning(f"Error closing Redis client: {e}")
            self._client = None

        if self._pool:
            try:
                await self._pool.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting pool: {e}")
            self._pool = None

        logger.info(f"Redis disconnected for service={self._service_name}")

    @property
    def client(self) -> aioredis.Redis:
        """
        Get the Redis client instance.

        Raises:
            RuntimeError: If Redis is not connected.
        """
        if self._client is None:
            raise RuntimeError(
                f"Redis not connected for service={self._service_name}. "
                "Call connect() first."
            )
        return self._client

    def _is_connected(self) -> bool:
        """Check if client is available."""
        return self._client is not None

    # ═══════════════════════════════════════════════════════════
    # DISTRIBUTED LOCKING (fail-CLOSED)
    # ═══════════════════════════════════════════════════════════

    async def acquire_distributed_lock(
        self,
        key: str,
        value: Optional[str] = None,
        ttl: int = 30,
    ) -> bool:
        """
        Acquire a distributed lock using SET NX EX.

        FAIL-CLOSED: Returns False if Redis is down.
        This ensures that operations requiring locks are NOT executed
        when Redis is unavailable.

        Args:
            key: The lock key (e.g., "shelf:slot_1").
            value: Unique identifier for the lock owner. Auto-generated if None.
            ttl: Lock TTL in seconds. Lock auto-expires after this.

        Returns:
            True if lock acquired, False if already locked or Redis unavailable.
        """
        if not self._is_connected():
            logger.error(f"Redis unavailable — lock acquisition DENIED for key={key} (fail-CLOSED)")
            return False

        lock_value = value or str(uuid.uuid4())
        lock_key = f"lock:{key}"

        try:
            result = await self._client.set(
                lock_key,
                lock_value,
                nx=True,  # Only set if not exists
                ex=ttl,   # Auto-expire after TTL
            )
            acquired = result is True

            if acquired:
                logger.debug(f"Lock acquired: key={lock_key} value={lock_value} ttl={ttl}")
            else:
                logger.debug(f"Lock NOT acquired (already held): key={lock_key}")

            return acquired

        except RedisError as e:
            # FAIL-CLOSED: return False when Redis errors
            logger.error(
                f"Redis error during lock acquisition for key={key}: {e} "
                f"— returning False (fail-CLOSED)"
            )
            return False

    async def release_distributed_lock(
        self,
        key: str,
        value: str,
    ) -> bool:
        """
        Release a distributed lock using atomic Lua script.

        Only releases if the caller owns the lock (value matches).
        This prevents accidentally releasing someone else's lock.

        FAIL-CLOSED: Returns False if Redis is down.

        Args:
            key: The lock key (without "lock:" prefix).
            value: The lock owner identifier (must match the value used to acquire).

        Returns:
            True if lock was released, False if not owner or Redis unavailable.
        """
        if not self._is_connected():
            logger.error(f"Redis unavailable — lock release FAILED for key={key} (fail-CLOSED)")
            return False

        lock_key = f"lock:{key}"

        try:
            result = await self._client.eval(
                _RELEASE_LOCK_SCRIPT,
                1,  # number of keys
                lock_key,
                value,
            )
            released = result == 1

            if released:
                logger.debug(f"Lock released: key={lock_key}")
            else:
                logger.warning(
                    f"Lock NOT released (not owner or expired): key={lock_key}"
                )

            return released

        except RedisError as e:
            logger.error(
                f"Redis error during lock release for key={key}: {e} "
                f"— returning False (fail-CLOSED)"
            )
            return False

    async def extend_distributed_lock(
        self,
        key: str,
        value: str,
        additional_ttl: int = 30,
    ) -> bool:
        """
        Extend a distributed lock's TTL (lock renewal).

        Only extends if the caller still owns the lock.

        Args:
            key: The lock key.
            value: The lock owner identifier.
            additional_ttl: Additional seconds to add to TTL.

        Returns:
            True if TTL extended, False otherwise.
        """
        if not self._is_connected():
            logger.warning(f"Redis unavailable — lock extension DENIED for key={key} (fail-CLOSED)")
            return False

        lock_key = f"lock:{key}"

        try:
            result = await self._client.eval(
                _EXTEND_LOCK_SCRIPT,
                1,
                lock_key,
                value,
                str(additional_ttl),
            )
            return result == 1

        except RedisError as e:
            logger.error(f"Redis error during lock extension for key={key}: {e}")
            return False

    # ═══════════════════════════════════════════════════════════
    # CACHE HELPERS
    # ═══════════════════════════════════════════════════════════

    async def cache_set(
        self,
        key: str,
        value: Any,
        ttl: int = 120,
        prefix: str = "cache",
    ) -> bool:
        """
        Set a cache value with TTL.

        Args:
            key: Cache key.
            value: Value to cache (will be JSON-serialized).
            ttl: Time-to-live in seconds.
            prefix: Key prefix (default: "cache").

        Returns:
            True if set successfully, False on error.
        """
        if not self._is_connected():
            logger.warning(f"Redis unavailable — cache set DENIED for key={prefix}:{key}")
            return False

        cache_key = f"{prefix}:{key}"

        try:
            serialized = json.dumps(value, default=str)
            await self._client.set(cache_key, serialized, ex=ttl)
            logger.debug(f"Cache set: key={cache_key} ttl={ttl}")
            return True

        except (RedisError, TypeError) as e:
            logger.error(f"Cache set failed for key={cache_key}: {e}")
            return False

    async def cache_get(
        self,
        key: str,
        prefix: str = "cache",
    ) -> Optional[Any]:
        """
        Get a cached value.

        FAIL-CLOSED: Returns None if Redis is down (do NOT assume cache is valid).

        Args:
            key: Cache key.
            prefix: Key prefix.

        Returns:
            Deserialized value or None if not found / error.
        """
        if not self._is_connected():
            logger.warning(f"Redis unavailable — cache get DENIED for key={prefix}:{key}")
            return None

        cache_key = f"{prefix}:{key}"

        try:
            data = await self._client.get(cache_key)
            if data is None:
                return None
            return json.loads(data)

        except (RedisError, json.JSONDecodeError) as e:
            logger.error(f"Cache get failed for key={cache_key}: {e}")
            return None

    async def cache_delete(self, key: str, prefix: str = "cache") -> bool:
        """
        Delete a cached value.

        Args:
            key: Cache key.
            prefix: Key prefix.

        Returns:
            True if deleted, False on error.
        """
        if not self._is_connected():
            logger.warning(f"Redis unavailable — cache delete DENIED for key={prefix}:{key}")
            return False

        cache_key = f"{prefix}:{key}"

        try:
            await self._client.delete(cache_key)
            return True

        except RedisError as e:
            logger.error(f"Cache delete failed for key={cache_key}: {e}")
            return False

    # ═══════════════════════════════════════════════════════════
    # DOMAIN-SPECIFIC HELPERS
    # ═══════════════════════════════════════════════════════════

    async def set_order_status(
        self, order_id: str, status: str, tenant_id: str = "default"
    ) -> bool:
        """Set current order status in Redis for fast reads."""
        return await self.cache_set(
            f"{tenant_id}:order:{order_id}:status",
            status,
            ttl=600,
            prefix="",
        )

    async def get_order_status(
        self, order_id: str, tenant_id: str = "default"
    ) -> Optional[str]:
        """Get current order status from Redis."""
        data = await self.cache_get(
            f"{tenant_id}:order:{order_id}:status",
            prefix="",
        )
        return data if isinstance(data, str) else None

    async def set_kitchen_load(
        self, kitchen_id: str, load_data: dict, tenant_id: str = "default"
    ) -> bool:
        """Cache kitchen load data with short TTL."""
        return await self.cache_set(
            f"{tenant_id}:kitchen:{kitchen_id}:load",
            load_data,
            ttl=60,
            prefix="",
        )

    async def get_kitchen_load(
        self, kitchen_id: str, tenant_id: str = "default"
    ) -> Optional[dict]:
        """Get cached kitchen load data."""
        data = await self.cache_get(
            f"{tenant_id}:kitchen:{kitchen_id}:load",
            prefix="",
        )
        return data if isinstance(data, dict) else None

    async def cache_eta(
        self, order_id: str, eta_data: dict, tenant_id: str = "default", ttl: int = 120
    ) -> bool:
        """Cache ETA prediction for an order."""
        return await self.cache_set(
            f"{tenant_id}:eta:{order_id}",
            eta_data,
            ttl=ttl,
            prefix="",
        )

    async def get_cached_eta(
        self, order_id: str, tenant_id: str = "default"
    ) -> Optional[dict]:
        """Get cached ETA prediction."""
        data = await self.cache_get(
            f"{tenant_id}:eta:{order_id}",
            prefix="",
        )
        return data if isinstance(data, dict) else None

    # ═══════════════════════════════════════════════════════════
    # SHELF LOCKING (domain-specific distributed locks)
    # ═══════════════════════════════════════════════════════════

    async def acquire_shelf_lock(
        self,
        shelf_id: str,
        order_id: str,
        ttl: int = 600,
        tenant_id: str = "default",
    ) -> bool:
        """
        Acquire a shelf lock — domain wrapper around distributed lock.

        Args:
            shelf_id: The shelf slot identifier.
            order_id: The order claiming the shelf.
            ttl: Lock TTL in seconds (default: 600 = 10 minutes).
            tenant_id: Tenant for multi-tenancy.

        Returns:
            True if lock acquired, False otherwise.
        """
        return await self.acquire_distributed_lock(
            key=f"{tenant_id}:shelf:{shelf_id}",
            value=order_id,
            ttl=ttl,
        )

    async def release_shelf_lock(
        self,
        shelf_id: str,
        order_id: str,
        tenant_id: str = "default",
    ) -> bool:
        """Release a shelf lock."""
        return await self.release_distributed_lock(
            key=f"{tenant_id}:shelf:{shelf_id}",
            value=order_id,
        )

    async def get_shelf_occupant(
        self, shelf_id: str, tenant_id: str = "default"
    ) -> Optional[str]:
        """Get the order_id currently occupying a shelf.

        Fail-CLOSED: Returns None when Redis is unavailable. Callers must
        treat None as 'occupant unknown', not 'shelf is free'.
        """
        if not self._is_connected():
            logger.warning(f"Redis unavailable — get_shelf_occupant DENIED for shelf={shelf_id} tenant={tenant_id}")
            return None

        try:
            return await self._client.get(f"lock:{tenant_id}:shelf:{shelf_id}")
        except RedisError as e:
            logger.error(f"get_shelf_occupant failed for shelf={shelf_id}: {e}")
            return None

    # ═══════════════════════════════════════════════════════════
    # PUB/SUB HELPERS
    # ═══════════════════════════════════════════════════════════

    async def publish(self, channel: str, message: dict) -> bool:
        """
        Publish a message to a Redis channel.

        Args:
            channel: Channel name.
            message: Message payload (will be JSON-serialized).

        Returns:
            True if published, False on error.
        """
        if not self._is_connected():
            logger.warning(f"Redis unavailable — publish DENIED for channel={channel}")
            return False

        try:
            serialized = json.dumps(message, default=str)
            await self._client.publish(channel, serialized)
            logger.debug(f"Published to channel={channel}")
            return True

        except RedisError as e:
            logger.error(f"Publish failed for channel={channel}: {e}")
            return False

    async def subscribe(self, *channels: str) -> Optional[aioredis.client.PubSub]:
        """
        Subscribe to one or more Redis channels.

        Args:
            channels: Channel names to subscribe to.

        Returns:
            PubSub object for consuming messages, or None on error.
        """
        if not self._is_connected():
            logger.warning(f"Redis unavailable — subscribe DENIED for channels={channels}")
            return None

        try:
            self._pubsub = self._client.pubsub()
            await self._pubsub.subscribe(*channels)
            logger.info(f"Subscribed to channels: {channels}")
            return self._pubsub

        except RedisError as e:
            logger.error(f"Subscribe failed for channels={channels}: {e}")
            return None

    async def unsubscribe(self, *channels: str) -> bool:
        """
        Unsubscribe from Redis channels.

        Args:
            channels: Channel names to unsubscribe from.

        Returns:
            True if successful, False on error.
        """
        if not self._pubsub:
            return False

        try:
            await self._pubsub.unsubscribe(*channels)
            return True
        except RedisError as e:
            logger.error(f"Unsubscribe failed: {e}")
            return False

    # ═══════════════════════════════════════════════════════════
    # RATE LIMITING
    # ═══════════════════════════════════════════════════════════

    async def check_rate_limit(
        self,
        identifier: str,
        limit: int = 60,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        """
        Check rate limit using sliding window counter.

        FAIL-CLOSED: Returns (False, 0) if Redis is down —
        rate limit is NOT bypassed when Redis is unavailable.

        Args:
            identifier: Unique identifier (e.g., tenant_id, user_id, IP).
            limit: Maximum requests allowed in the window.
            window_seconds: Time window in seconds.

        Returns:
            Tuple of (is_allowed, remaining_count).
        """
        if not self._is_connected():
            # FAIL-CLOSED: deny requests when Redis is down
            logger.warning(
                f"Redis unavailable — rate limit DENIED for {identifier} (fail-CLOSED)"
            )
            return False, 0

        key = f"ratelimit:{identifier}"

        try:
            pipe = self._client.pipeline()
            now_ts = await self._client.time()
            now = int(now_ts[0])
            window_start = now - window_seconds

            # Remove old entries and add current request
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, window_seconds)

            results = await pipe.execute()
            current_count = results[1]

            is_allowed = current_count < limit
            remaining = max(0, limit - current_count - 1)

            if not is_allowed:
                logger.debug(
                    f"Rate limit exceeded: identifier={identifier} "
                    f"count={current_count} limit={limit}"
                )

            return is_allowed, remaining

        except RedisError as e:
            logger.error(
                f"Redis error during rate limit check for {identifier}: {e} "
                f"— denying (fail-CLOSED)"
            )
            return False, 0

    # ═══════════════════════════════════════════════════════════
    # HEALTH CHECK
    # ═══════════════════════════════════════════════════════════

    async def health_check(self) -> dict[str, Any]:
        """
        Check Redis connectivity and return health info.

        Returns:
            Dict with health status, latency, and connection info.
        """
        if not self._is_connected():
            return {
                "status": "unhealthy",
                "error": "Redis not connected",
                "service": self._service_name,
            }

        try:
            import time
            start = time.monotonic()
            await self._client.ping()
            latency_ms = (time.monotonic() - start) * 1000

            info = await self._client.info("server")

            return {
                "status": "healthy",
                "latency_ms": round(latency_ms, 2),
                "redis_version": info.get("redis_version", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
                "used_memory_human": info.get("used_memory_human", "unknown"),
                "service": self._service_name,
            }

        except RedisError as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "service": self._service_name,
            }


# ═══════════════════════════════════════════════════════════════
# SINGLETON MANAGEMENT
# ═══════════════════════════════════════════════════════════════

_clients: dict[str, RedisClient] = {}


def get_redis_client(service_name: str = "hotsot") -> RedisClient:
    """
    Get or create a RedisClient for a specific service.

    Args:
        service_name: The microservice identifier.

    Returns:
        RedisClient instance (not yet connected).
    """
    if service_name not in _clients:
        _clients[service_name] = RedisClient(service_name=service_name)
    return _clients[service_name]


# Legacy singleton (backward compat)
redis_client = RedisClient()
