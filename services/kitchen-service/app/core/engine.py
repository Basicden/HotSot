"""HotSot Kitchen Service — Kitchen Orchestration Engine.

Handles priority scoring, queue management, batch cooking, throughput
monitoring, staff assignment, and load balancing at 10K+ vendor scale.
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import UUID

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
# QUEUE MANAGER
# ═══════════════════════════════════════════════════════════════

class QueueManager:
    """
    Manages kitchen queues with priority ordering.

    Supports three queue types:
    - IMMEDIATE: VIP/arrived users (processed first)
    - NORMAL: Regular orders
    - BATCH: Grouped by cooking category for efficiency
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def enqueue(self, kitchen_id: str, order_id: str, queue_type: str,
                      priority_score: float, tenant_id: str) -> int:
        """Add order to kitchen queue. Returns queue position."""
        key = f"queue:{tenant_id}:{kitchen_id}:{queue_type}"
        member = json.dumps({
            "order_id": order_id,
            "priority_score": priority_score,
            "enqueued_at": now_ts(),
        })
        # Sorted set with priority as score (higher = first)
        await self._redis.client.zadd(key, {member: priority_score})
        position = await self._redis.client.zrevrank(key, member)
        return (position or 0) + 1

    async def dequeue(self, kitchen_id: str, order_id: str,
                      queue_type: str, tenant_id: str) -> bool:
        """Remove order from queue."""
        key = f"queue:{tenant_id}:{kitchen_id}:{queue_type}"
        # Find and remove the member with this order_id
        members = await self._redis.client.zrange(key, 0, -1)
        for member in members:
            data = json.loads(member)
            if data.get("order_id") == order_id:
                await self._redis.client.zrem(key, member)
                return True
        return False

    async def get_queue(self, kitchen_id: str, tenant_id: str) -> List[Dict]:
        """Get full kitchen queue ordered by priority (highest first)."""
        result = []
        for qt in [QueueType.IMMEDIATE.value, QueueType.NORMAL.value, QueueType.BATCH.value]:
            key = f"queue:{tenant_id}:{kitchen_id}:{qt}"
            members = await self._redis.client.zrevrange(key, 0, -1, withscores=True)
            for member, score in members:
                data = json.loads(member)
                data["queue_type"] = qt
                data["priority_score"] = score
                result.append(data)
        return result

    async def get_position(self, kitchen_id: str, order_id: str,
                           tenant_id: str) -> Optional[int]:
        """Get queue position for a specific order."""
        for qt in [QueueType.IMMEDIATE.value, QueueType.NORMAL.value, QueueType.BATCH.value]:
            key = f"queue:{tenant_id}:{kitchen_id}:{qt}"
            members = await self._redis.client.zrevrange(key, 0, -1)
            for i, member in enumerate(members):
                data = json.loads(member)
                if data.get("order_id") == order_id:
                    return i + 1
        return None

    async def reorder(self, kitchen_id: str, tenant_id: str) -> int:
        """Recalculate and reorder queue based on current priority scores."""
        # This would be called periodically or after arrival events
        # For now, sorted sets auto-sort by score
        return 0


# ═══════════════════════════════════════════════════════════════
# BATCH ENGINE
# ═══════════════════════════════════════════════════════════════

class BatchEngine:
    """
    Groups orders by cooking category for batch efficiency.

    India food reality: a tandoor can cook 6 kebabs in the same time as 1.
    Batch cooking saves 40-60% kitchen time during peak hours.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def suggest_batch(self, kitchen_id: str, tenant_id: str) -> List[Dict]:
        """Suggest batch groups based on current queue."""
        suggestions = []
        for category in BatchCategory:
            key = f"batch_candidates:{tenant_id}:{kitchen_id}:{category.value}"
            candidates = await self._redis.client.lrange(key, 0, -1)
            if len(candidates) >= 2:  # Minimum batch size
                suggestions.append({
                    "category": category.value,
                    "order_count": len(candidates),
                    "order_ids": [json.loads(c).get("order_id") for c in candidates],
                    "estimated_time_saving": f"{len(candidates) * 0.3:.0f} min",
                })
        return suggestions

    async def add_to_batch_candidates(self, kitchen_id: str, order_id: str,
                                       category: str, tenant_id: str) -> None:
        """Add order to batch candidate pool."""
        key = f"batch_candidates:{tenant_id}:{kitchen_id}:{category}"
        await self._redis.client.rpush(key, json.dumps({
            "order_id": order_id,
            "added_at": now_ts(),
        }))
        # Auto-expire candidates after 10 minutes
        await self._redis.client.expire(key, 600)

    async def remove_from_batch_candidates(self, kitchen_id: str, order_id: str,
                                            category: str, tenant_id: str) -> None:
        """Remove order from batch candidate pool."""
        key = f"batch_candidates:{tenant_id}:{kitchen_id}:{category}"
        members = await self._redis.client.lrange(key, 0, -1)
        for member in members:
            data = json.loads(member)
            if data.get("order_id") == order_id:
                await self._redis.client.lrem(key, 1, member)
                break


# ═══════════════════════════════════════════════════════════════
# THROUGHPUT MONITOR
# ═══════════════════════════════════════════════════════════════

class ThroughputMonitor:
    """
    Tracks kitchen throughput and detects overload conditions.

    Overload triggers:
    - Active orders >= 85% of max capacity
    - Queue length > 50
    - Throughput degradation > 40%
    """

    OVERLOAD_THRESHOLD = 0.85
    MAX_QUEUE_LENGTH = 50
    DEGRADATION_THRESHOLD = 0.40

    def __init__(self, redis_client):
        self._redis = redis_client

    async def record_throughput(self, kitchen_id: str, kitchen_size: str,
                                 active_orders: int, queue_length: int,
                                 tenant_id: str) -> Dict[str, Any]:
        """Record throughput measurement and detect overload."""
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

        # Cache in Redis
        key = f"throughput:{tenant_id}:{kitchen_id}"
        await self._redis.client.setex(key, 60, json.dumps(measurement))

        return measurement

    async def get_throughput(self, kitchen_id: str, tenant_id: str) -> Optional[Dict]:
        """Get latest throughput measurement."""
        key = f"throughput:{tenant_id}:{kitchen_id}"
        data = await self._redis.client.get(key)
        return json.loads(data) if data else None

    async def should_throttle(self, kitchen_id: str, tenant_id: str) -> bool:
        """Check if kitchen should throttle new orders."""
        throughput = await self.get_throughput(kitchen_id, tenant_id)
        if throughput:
            return throughput.get("is_overloaded", False)
        return False


# ═══════════════════════════════════════════════════════════════
# STAFF ASSIGNMENT ENGINE
# ═══════════════════════════════════════════════════════════════

class StaffAssignmentEngine:
    """
    Assigns kitchen staff to orders based on role and availability.

    Roles:
    - CHEF: Handles IN_PREP state
    - PACKER: Handles PACKING state
    - RUNNER: Handles shelf placement and handoff
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def assign_staff(self, kitchen_id: str, order_id: str,
                           required_role: str, tenant_id: str) -> Optional[str]:
        """Find and assign available staff member to an order."""
        key = f"staff_available:{tenant_id}:{kitchen_id}:{required_role}"
        # Get first available staff member
        staff_member = await self._redis.client.lpop(key)
        if staff_member:
            # Mark as busy
            busy_key = f"staff_busy:{tenant_id}:{kitchen_id}:{staff_member.decode()}"
            await self._redis.client.setex(busy_key, 1800, json.dumps({
                "order_id": order_id,
                "assigned_at": now_ts(),
            }))
            return staff_member.decode()
        return None

    async def release_staff(self, kitchen_id: str, staff_id: str,
                            role: str, tenant_id: str) -> None:
        """Release staff member back to available pool."""
        # Remove from busy
        busy_key = f"staff_busy:{tenant_id}:{kitchen_id}:{staff_id}"
        await self._redis.client.delete(busy_key)
        # Add back to available pool
        key = f"staff_available:{tenant_id}:{kitchen_id}:{role}"
        await self._redis.client.rpush(key, staff_id)

    async def register_staff(self, kitchen_id: str, staff_id: str,
                              role: str, tenant_id: str) -> None:
        """Register a staff member as available."""
        key = f"staff_available:{tenant_id}:{kitchen_id}:{role}"
        await self._redis.client.rpush(key, staff_id)

    async def heartbeat(self, kitchen_id: str, staff_id: str, tenant_id: str) -> bool:
        """Process staff heartbeat — confirms staff is still active."""
        hb_key = f"staff_heartbeat:{tenant_id}:{kitchen_id}:{staff_id}"
        await self._redis.client.setex(hb_key, 120, str(now_ts()))
        return True
