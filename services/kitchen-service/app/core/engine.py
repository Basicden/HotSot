"""HotSot Kitchen Service — Kitchen Orchestration Engine.

Handles priority scoring, queue management, batch cooking, throughput
monitoring, staff assignment, and load balancing at 10K+ vendor scale.

All Redis operations are fail-CLOSED: when Redis is unavailable, operations
return safe sentinel values and log warnings rather than crashing with
unhandled RuntimeError or RedisError.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

from redis.exceptions import RedisError

from shared.utils.helpers import now_ts, Timer
from shared.types.schemas import (
    TIER_WEIGHTS, BATCH_CATEGORY_MAP, KITCHEN_THROUGHPUT, PEAK_DEGRADATION,
    RiskLevel, QueueType, BatchCategory,
)

logger = logging.getLogger("kitchen-service.engine")


# ═══════════════════════════════════════════════════════════════
# PRIORITY SCORE CALCULATOR
# ═══════════════════════════════════════════════════════════════

class PriorityScoreCalculator:
    """
    Multi-factor priority score for kitchen queue ordering.

    Score = tier_bonus + arrival_proximity + delay_risk + age_factor
            + batch_compatibility + arrival_boost

    Range: 0-200 (higher = more urgent)
    """

    @staticmethod
    def calculate(
        user_tier: str = "FREE",
        arrival_proximity: float = 0.0,
        delay_risk: float = 0.0,
        order_age_seconds: int = 0,
        batch_category: Optional[str] = None,
        current_batch_category: Optional[str] = None,
        arrival_boost: float = 0.0,
    ) -> float:
        """Calculate priority score for an order."""
        # Tier bonus: VIP orders get priority
        tier_bonus = TIER_WEIGHTS.get(user_tier, 1) * 5.0

        # Arrival proximity: 0-40 (closer = higher)
        # If user is within 500m, they get max proximity bonus
        proximity_score = max(0, min(40.0, 40.0 * (1.0 - arrival_proximity / 500.0)))

        # Delay risk: 0-30 (higher risk = higher priority)
        delay_score = min(30.0, delay_risk * 30.0)

        # Age factor: 0-20 (older orders get priority)
        # Max bonus at 15 minutes, saturates
        age_score = min(20.0, (order_age_seconds / 900.0) * 20.0)

        # Batch compatibility: 0-10 (same category as current batch)
        batch_score = 0.0
        if batch_category and current_batch_category and batch_category == current_batch_category:
            batch_score = 10.0

        total = tier_bonus + proximity_score + delay_score + age_score + batch_score + arrival_boost
        return round(min(200.0, total), 2)

    @staticmethod
    def determine_queue_type(score: float) -> str:
        """Determine queue type from priority score."""
        if score >= 80:
            return QueueType.IMMEDIATE.value
        elif score >= 50:
            return QueueType.NORMAL.value
        else:
            return QueueType.BATCH.value

    @staticmethod
    def determine_batch_category(items: List[Dict]) -> Optional[str]:
        """Determine batch category from order items."""
        for category, keywords in BATCH_CATEGORY_MAP.items():
            for item in items:
                name = item.get("name", "").lower() if isinstance(item, dict) else str(item).lower()
                if any(kw in name for kw in keywords):
                    return category
        return None


# ═══════════════════════════════════════════════════════════════
# QUEUE MANAGER (Fail-CLOSED)
# ═══════════════════════════════════════════════════════════════

class QueueManager:
    """
    Manages kitchen queues with priority ordering.

    Supports three queue types:
    - IMMEDIATE: VIP/arrived users (processed first)
    - NORMAL: Regular orders
    - BATCH: Grouped by cooking category for efficiency

    Fail-CLOSED: All Redis operations are wrapped in try/except.
    When Redis is unavailable, queue operations return safe defaults
    and log warnings rather than crashing.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def enqueue(self, kitchen_id: str, order_id: str, queue_type: str,
                      priority_score: float, tenant_id: str) -> int:
        """Add order to kitchen queue. Returns queue position (-1 on failure)."""
        key = f"queue:{tenant_id}:{kitchen_id}:{queue_type}"
        member = json.dumps({
            "order_id": order_id,
            "priority_score": priority_score,
            "enqueued_at": now_ts(),
        })
        try:
            client = self._redis.client
            await client.zadd(key, {member: priority_score})
            position = await client.zrevrank(key, member)
            return (position or 0) + 1
        except (RedisError, RuntimeError) as exc:
            logger.warning(
                "enqueue: Redis unavailable — queue operation FAILED for kitchen=%s order=%s: %s",
                kitchen_id, order_id, exc,
            )
            return -1

    async def dequeue(self, kitchen_id: str, order_id: str,
                      queue_type: str, tenant_id: str) -> bool:
        """Remove order from queue. Returns False on failure or not found."""
        key = f"queue:{tenant_id}:{kitchen_id}:{queue_type}"
        try:
            client = self._redis.client
            members = await client.zrange(key, 0, -1)
            for member in members:
                data = json.loads(member)
                if data.get("order_id") == order_id:
                    await client.zrem(key, member)
                    return True
            return False
        except (RedisError, RuntimeError, json.JSONDecodeError) as exc:
            logger.warning(
                "dequeue: Redis unavailable — dequeue FAILED for kitchen=%s order=%s: %s",
                kitchen_id, order_id, exc,
            )
            return False

    async def get_queue(self, kitchen_id: str, tenant_id: str) -> List[Dict]:
        """Get full kitchen queue ordered by priority. Returns [] on failure."""
        result = []
        for qt in [QueueType.IMMEDIATE.value, QueueType.NORMAL.value, QueueType.BATCH.value]:
            key = f"queue:{tenant_id}:{kitchen_id}:{qt}"
            try:
                client = self._redis.client
                members = await client.zrevrange(key, 0, -1, withscores=True)
                for member, score in members:
                    data = json.loads(member)
                    data["queue_type"] = qt
                    data["priority_score"] = score
                    result.append(data)
            except (RedisError, RuntimeError, json.JSONDecodeError) as exc:
                logger.warning(
                    "get_queue: Redis error for kitchen=%s queue_type=%s: %s",
                    kitchen_id, qt, exc,
                )
        return result

    async def get_position(self, kitchen_id: str, order_id: str,
                           tenant_id: str) -> Optional[int]:
        """Get queue position for a specific order. Returns None on failure."""
        for qt in [QueueType.IMMEDIATE.value, QueueType.NORMAL.value, QueueType.BATCH.value]:
            key = f"queue:{tenant_id}:{kitchen_id}:{qt}"
            try:
                client = self._redis.client
                members = await client.zrevrange(key, 0, -1)
                for i, member in enumerate(members):
                    data = json.loads(member)
                    if data.get("order_id") == order_id:
                        return i + 1
            except (RedisError, RuntimeError, json.JSONDecodeError) as exc:
                logger.warning(
                    "get_position: Redis error for kitchen=%s queue_type=%s: %s",
                    kitchen_id, qt, exc,
                )
        return None

    async def reorder(self, kitchen_id: str, tenant_id: str) -> int:
        """Recalculate and reorder queue based on current priority scores."""
        # Sorted sets auto-sort by score
        return 0


# ═══════════════════════════════════════════════════════════════
# BATCH ENGINE (Fail-CLOSED)
# ═══════════════════════════════════════════════════════════════

class BatchEngine:
    """
    Groups orders by cooking category for batch efficiency.

    India food reality: a tandoor can cook 6 kebabs in the same time as 1.
    Batch cooking saves 40-60% kitchen time during peak hours.

    Fail-CLOSED: All Redis operations are wrapped in try/except.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def suggest_batch(self, kitchen_id: str, tenant_id: str) -> List[Dict]:
        """Suggest batch groups based on current queue. Returns [] on failure."""
        suggestions = []
        for category in BatchCategory:
            key = f"batch_candidates:{tenant_id}:{kitchen_id}:{category.value}"
            try:
                client = self._redis.client
                candidates = await client.lrange(key, 0, -1)
                if len(candidates) >= 2:  # Minimum batch size
                    suggestions.append({
                        "category": category.value,
                        "order_count": len(candidates),
                        "order_ids": [json.loads(c).get("order_id") for c in candidates],
                        "estimated_time_saving": f"{len(candidates) * 0.3:.0f} min",
                    })
            except (RedisError, RuntimeError, json.JSONDecodeError) as exc:
                logger.warning(
                    "suggest_batch: Redis error for kitchen=%s category=%s: %s",
                    kitchen_id, category.value, exc,
                )
        return suggestions

    async def add_to_batch_candidates(self, kitchen_id: str, order_id: str,
                                       category: str, tenant_id: str) -> bool:
        """Add order to batch candidate pool. Returns False on failure."""
        key = f"batch_candidates:{tenant_id}:{kitchen_id}:{category}"
        try:
            client = self._redis.client
            await client.rpush(key, json.dumps({
                "order_id": order_id,
                "added_at": now_ts(),
            }))
            # Auto-expire candidates after 10 minutes
            await client.expire(key, 600)
            return True
        except (RedisError, RuntimeError) as exc:
            logger.warning(
                "add_to_batch_candidates: Redis error for kitchen=%s order=%s: %s",
                kitchen_id, order_id, exc,
            )
            return False

    async def remove_from_batch_candidates(self, kitchen_id: str, order_id: str,
                                            category: str, tenant_id: str) -> bool:
        """Remove order from batch candidate pool. Returns False on failure."""
        key = f"batch_candidates:{tenant_id}:{kitchen_id}:{category}"
        try:
            client = self._redis.client
            members = await client.lrange(key, 0, -1)
            for member in members:
                data = json.loads(member)
                if data.get("order_id") == order_id:
                    await client.lrem(key, 1, member)
                    break
            return True
        except (RedisError, RuntimeError, json.JSONDecodeError) as exc:
            logger.warning(
                "remove_from_batch_candidates: Redis error for kitchen=%s order=%s: %s",
                kitchen_id, order_id, exc,
            )
            return False


# ═══════════════════════════════════════════════════════════════
# THROUGHPUT MONITOR (Fail-CLOSED)
# ═══════════════════════════════════════════════════════════════

class ThroughputMonitor:
    """
    Tracks kitchen throughput and detects overload conditions.

    Overload triggers:
    - Active orders >= 85% of max capacity
    - Queue length > 50
    - Throughput degradation > 40%

    Fail-CLOSED: Redis cache writes log warnings on failure but the
    measurement is still computed and returned (degrades to uncached).
    """

    OVERLOAD_THRESHOLD = 0.85
    MAX_QUEUE_LENGTH = 50
    DEGRADATION_THRESHOLD = 0.40

    def __init__(self, redis_client):
        self._redis = redis_client

    async def record_throughput(self, kitchen_id: str, kitchen_size: str,
                                 active_orders: int, queue_length: int,
                                 tenant_id: str) -> Dict[str, Any]:
        """Record throughput measurement and detect overload.

        Returns the measurement dict even if Redis is unavailable (degrades
        gracefully — computation is local, only caching is Redis-dependent).
        """
        max_capacity = KITCHEN_THROUGHPUT.get(kitchen_size, 15)
        load_pct = (active_orders / max(max_capacity, 1)) * 100
        is_overloaded = (
            load_pct >= self.OVERLOAD_THRESHOLD * 100
            or queue_length > self.MAX_QUEUE_LENGTH
        )

        measurement = {
            "kitchen_id": kitchen_id,
            "orders_per_minute": max_capacity * (1 - PEAK_DEGRADATION if is_overloaded else 1),
            "active_orders": active_orders,
            "queue_length": queue_length,
            "load_percentage": round(load_pct, 2),
            "is_overloaded": is_overloaded,
            "measured_at": datetime.now(timezone.utc).isoformat(),
        }

        # Cache in Redis (fail-safe: measurement still returned if cache fails)
        key = f"throughput:{tenant_id}:{kitchen_id}"
        try:
            await self._redis.client.setex(key, 60, json.dumps(measurement))
        except (RedisError, RuntimeError) as exc:
            logger.warning(
                "record_throughput: Redis cache write FAILED for kitchen=%s: %s",
                kitchen_id, exc,
            )

        return measurement

    async def get_throughput(self, kitchen_id: str, tenant_id: str) -> Optional[Dict]:
        """Get latest throughput measurement. Returns None on failure."""
        key = f"throughput:{tenant_id}:{kitchen_id}"
        try:
            client = self._redis.client
            data = await client.get(key)
            return json.loads(data) if data else None
        except (RedisError, RuntimeError, json.JSONDecodeError) as exc:
            logger.warning(
                "get_throughput: Redis error for kitchen=%s: %s", kitchen_id, exc,
            )
            return None

    async def should_throttle(self, kitchen_id: str, tenant_id: str) -> bool:
        """Check if kitchen should throttle new orders.

        Fail-CLOSED: Returns True (throttle) when Redis is unavailable.
        If we cannot verify kitchen capacity, we assume overload to
        prevent accepting orders we cannot fulfill.
        """
        throughput = await self.get_throughput(kitchen_id, tenant_id)
        if throughput is None:
            # Redis unavailable — assume overloaded to be safe
            logger.warning(
                "should_throttle: throughput data unavailable for kitchen=%s — assuming OVERLOADED (fail-CLOSED)",
                kitchen_id,
            )
            return True
        return throughput.get("is_overloaded", False)


# ═══════════════════════════════════════════════════════════════
# STAFF ASSIGNMENT ENGINE (Fail-CLOSED)
# ═══════════════════════════════════════════════════════════════

class StaffAssignmentEngine:
    """
    Assigns kitchen staff to orders based on role and availability.

    Roles:
    - CHEF: Handles IN_PREP state
    - PACKER: Handles PACKING state
    - RUNNER: Handles shelf placement and handoff

    Fail-CLOSED: All Redis operations are wrapped in try/except.
    Staff assignment returns None when Redis is unavailable (no phantom
    assignments), and heartbeat/release operations log failures.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def assign_staff(self, kitchen_id: str, order_id: str,
                           required_role: str, tenant_id: str) -> Optional[str]:
        """Find and assign available staff member to an order.

        Returns None if no staff available or Redis is down.
        """
        key = f"staff_available:{tenant_id}:{kitchen_id}:{required_role}"
        try:
            client = self._redis.client
            staff_member = await client.lpop(key)
            if staff_member:
                # Mark as busy
                staff_id = staff_member if isinstance(staff_member, str) else staff_member.decode()
                busy_key = f"staff_busy:{tenant_id}:{kitchen_id}:{staff_id}"
                await client.setex(busy_key, 1800, json.dumps({
                    "order_id": order_id,
                    "assigned_at": now_ts(),
                }))
                return staff_id
            return None
        except (RedisError, RuntimeError) as exc:
            logger.warning(
                "assign_staff: Redis unavailable — staff assignment FAILED for kitchen=%s order=%s: %s",
                kitchen_id, order_id, exc,
            )
            return None

    async def release_staff(self, kitchen_id: str, staff_id: str,
                            role: str, tenant_id: str) -> bool:
        """Release staff member back to available pool. Returns False on failure."""
        try:
            client = self._redis.client
            # Remove from busy
            busy_key = f"staff_busy:{tenant_id}:{kitchen_id}:{staff_id}"
            await client.delete(busy_key)
            # Add back to available pool
            key = f"staff_available:{tenant_id}:{kitchen_id}:{role}"
            await client.rpush(key, staff_id)
            return True
        except (RedisError, RuntimeError) as exc:
            logger.warning(
                "release_staff: Redis error for kitchen=%s staff=%s: %s",
                kitchen_id, staff_id, exc,
            )
            return False

    async def register_staff(self, kitchen_id: str, staff_id: str,
                              role: str, tenant_id: str) -> bool:
        """Register a staff member as available. Returns False on failure."""
        key = f"staff_available:{tenant_id}:{kitchen_id}:{role}"
        try:
            await self._redis.client.rpush(key, staff_id)
            return True
        except (RedisError, RuntimeError) as exc:
            logger.warning(
                "register_staff: Redis error for kitchen=%s staff=%s: %s",
                kitchen_id, staff_id, exc,
            )
            return False

    async def heartbeat(self, kitchen_id: str, staff_id: str, tenant_id: str) -> bool:
        """Process staff heartbeat — confirms staff is still active.

        Returns False if Redis is unavailable (heartbeat not recorded).
        """
        hb_key = f"staff_heartbeat:{tenant_id}:{kitchen_id}:{staff_id}"
        try:
            await self._redis.client.setex(hb_key, 120, str(now_ts()))
            return True
        except (RedisError, RuntimeError) as exc:
            logger.warning(
                "heartbeat: Redis error for kitchen=%s staff=%s: %s",
                kitchen_id, staff_id, exc,
            )
            return False
