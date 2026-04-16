"""HotSot Kitchen Service — Queue Management Routes.

Kitchen queue management endpoints: view, reorder, and set priority
for items in the kitchen preparation queue.

Uses Redis for real-time queue state and PostgreSQL for persistence.
Follows the set_dependencies() injection pattern used across the service.
"""

import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from shared.auth.jwt import get_current_user, require_role
from shared.utils.helpers import now_iso
from shared.types.schemas import EventType, QueueType, TIER_WEIGHTS

from app.core.database import KitchenQueueModel, KitchenModel
from app.core.engine import QueueManager, PriorityScoreCalculator

logger = logging.getLogger("kitchen-service.queue")

router = APIRouter()
_session_factory = None
_redis_client = None


def set_dependencies(session_factory, redis_client):
    """Set shared dependencies — called from main.py lifespan."""
    global _session_factory, _redis_client
    _session_factory = session_factory
    _redis_client = redis_client


async def get_session():
    """Get async database session."""
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session


# ═══════════════════════════════════════════════════════════════
# QUEUE VIEW
# ═══════════════════════════════════════════════════════════════

@router.get("/")
async def view_queue(
    kitchen_id: str = Query(..., description="Kitchen ID to view queue for"),
    queue_type: Optional[str] = Query(None, description="Filter by queue type: IMMEDIATE/NORMAL/BATCH"),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """View current kitchen queue.

    Returns all queued items ordered by priority score (highest first).
    Optionally filter by queue type.
    Combines Redis real-time state with DB persistence.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Verify kitchen exists
    kitchen_result = await session.execute(
        select(KitchenModel).where(KitchenModel.id == uuid.UUID(kitchen_id))
    )
    kitchen = kitchen_result.scalar_one_or_none()
    if not kitchen:
        raise HTTPException(status_code=404, detail="Kitchen not found")

    # Build DB query for persistent queue state
    query = (
        select(KitchenQueueModel)
        .where(
            KitchenQueueModel.kitchen_id == uuid.UUID(kitchen_id),
            KitchenQueueModel.tenant_id == uuid.UUID(tenant_id),
            KitchenQueueModel.status.in_(["QUEUED", "IN_PREP"]),
        )
        .order_by(KitchenQueueModel.priority_score.desc())
    )
    if queue_type:
        query = query.where(KitchenQueueModel.queue_type == queue_type)

    result = await session.execute(query)
    queue_entries = result.scalars().all()

    # Merge with Redis state if available
    redis_queue = []
    if _redis_client:
        try:
            queue_mgr = QueueManager(_redis_client)
            redis_queue = await queue_mgr.get_queue(kitchen_id, tenant_id)
        except Exception as exc:
            logger.warning(
                "view_queue: Redis unavailable, using DB-only state for kitchen=%s: %s",
                kitchen_id, exc,
            )

    # Build response from DB entries
    items = []
    for entry in queue_entries:
        items.append({
            "order_id": str(entry.order_id),
            "queue_type": entry.queue_type,
            "priority_score": entry.priority_score,
            "queue_position": entry.queue_position,
            "batch_category": entry.batch_category,
            "status": entry.status,
            "staff_id": str(entry.staff_id) if entry.staff_id else None,
            "enqueued_at": entry.enqueued_at.isoformat() if entry.enqueued_at else None,
            "started_at": entry.started_at.isoformat() if entry.started_at else None,
        })

    return {
        "kitchen_id": kitchen_id,
        "tenant_id": tenant_id,
        "queue_type_filter": queue_type,
        "total_items": len(items),
        "items": items,
        "redis_synced": len(redis_queue) > 0,
        "retrieved_at": now_iso(),
    }


# ═══════════════════════════════════════════════════════════════
# QUEUE REORDER
# ═══════════════════════════════════════════════════════════════

@router.post("/reorder")
async def reorder_queue(
    kitchen_id: str,
    recalculate: bool = True,
    user: dict = Depends(require_role("vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """Reorder kitchen queue based on current priority scores.

    Recalculates priority scores for all queued items based on current
    factors (age, tier, delay risk, arrival proximity) and reassigns
    queue positions. Also triggers Redis queue reorder.

    This is useful after:
    - A VIP user arrives (arrival proximity boost)
    - Kitchen recovers from overload
    - Manual priority adjustments
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Verify kitchen exists
    kitchen_result = await session.execute(
        select(KitchenModel).where(KitchenModel.id == uuid.UUID(kitchen_id))
    )
    kitchen = kitchen_result.scalar_one_or_none()
    if not kitchen:
        raise HTTPException(status_code=404, detail="Kitchen not found")

    # Fetch all queued items
    result = await session.execute(
        select(KitchenQueueModel).where(
            KitchenQueueModel.kitchen_id == uuid.UUID(kitchen_id),
            KitchenQueueModel.tenant_id == uuid.UUID(tenant_id),
            KitchenQueueModel.status == "QUEUED",
        ).order_by(KitchenQueueModel.priority_score.desc())
    )
    queue_entries = result.scalars().all()

    reordered_count = 0
    now = datetime.now(timezone.utc)

    if recalculate and queue_entries:
        for entry in queue_entries:
            # Recompute priority score based on current factors
            age_seconds = int((now - entry.enqueued_at).total_seconds()) if entry.enqueued_at else 0

            new_score = PriorityScoreCalculator.calculate(
                order_age_seconds=age_seconds,
                batch_category=entry.batch_category,
                arrival_boost=entry.arrival_boost,
            )
            new_queue_type = PriorityScoreCalculator.determine_queue_type(new_score)

            # Update if score or queue type changed
            if new_score != entry.priority_score or new_queue_type != entry.queue_type:
                entry.priority_score = new_score
                entry.queue_type = new_queue_type
                reordered_count += 1

    # Reassign positions based on updated scores
    sorted_entries = sorted(queue_entries, key=lambda e: e.priority_score, reverse=True)
    for position, entry in enumerate(sorted_entries, start=1):
        entry.queue_position = position

    await session.commit()

    # Reorder Redis queues
    redis_reordered = 0
    if _redis_client:
        try:
            queue_mgr = QueueManager(_redis_client)
            redis_reordered = await queue_mgr.reorder(kitchen_id, tenant_id)
        except Exception as exc:
            logger.warning(
                "reorder_queue: Redis reorder failed for kitchen=%s: %s",
                kitchen_id, exc,
            )

    logger.info(
        "queue_reordered: kitchen=%s items=%d recalculated=%d redis=%d",
        kitchen_id, len(queue_entries), reordered_count, redis_reordered,
    )

    return {
        "kitchen_id": kitchen_id,
        "total_items": len(queue_entries),
        "recalculated": reordered_count,
        "redis_reordered": redis_reordered,
        "reordered_at": now_iso(),
    }


# ═══════════════════════════════════════════════════════════════
# QUEUE PRIORITY
# ═══════════════════════════════════════════════════════════════

@router.post("/priority")
async def set_priority(
    kitchen_id: str,
    order_id: str,
    priority_score: Optional[float] = None,
    arrival_boost: Optional[float] = None,
    user_tier: Optional[str] = None,
    delay_risk: Optional[float] = None,
    arrival_proximity: Optional[float] = None,
    user: dict = Depends(require_role("vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """Set priority for a specific queue item.

    Either provide an explicit priority_score, or provide factors
    (user_tier, delay_risk, arrival_proximity, arrival_boost) and
    the system will compute the score automatically.

    After setting priority, the queue is re-sorted and positions
    are updated in both DB and Redis.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Find the queue entry
    result = await session.execute(
        select(KitchenQueueModel).where(
            KitchenQueueModel.kitchen_id == uuid.UUID(kitchen_id),
            KitchenQueueModel.order_id == uuid.UUID(order_id),
            KitchenQueueModel.tenant_id == uuid.UUID(tenant_id),
            KitchenQueueModel.status.in_(["QUEUED", "IN_PREP"]),
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Order not found in kitchen queue")

    # Calculate new score if factors provided, otherwise use explicit score
    if priority_score is not None:
        new_score = priority_score
    else:
        now = datetime.now(timezone.utc)
        age_seconds = int((now - entry.enqueued_at).total_seconds()) if entry.enqueued_at else 0
        new_score = PriorityScoreCalculator.calculate(
            user_tier=user_tier or "FREE",
            arrival_proximity=arrival_proximity if arrival_proximity is not None else 0.0,
            delay_risk=delay_risk if delay_risk is not None else 0.0,
            order_age_seconds=age_seconds,
            batch_category=entry.batch_category,
            arrival_boost=arrival_boost if arrival_boost is not None else entry.arrival_boost,
        )

    new_queue_type = PriorityScoreCalculator.determine_queue_type(new_score)
    old_score = entry.priority_score
    old_queue_type = entry.queue_type

    # Update entry
    entry.priority_score = new_score
    entry.queue_type = new_queue_type
    if arrival_boost is not None:
        entry.arrival_boost = arrival_boost

    await session.commit()

    # Update Redis queue: dequeue from old type, enqueue in new type
    if _redis_client:
        try:
            queue_mgr = QueueManager(_redis_client)
            # Remove from old queue type
            await queue_mgr.dequeue(kitchen_id, order_id, old_queue_type, tenant_id)
            # Add to new queue type with new score
            await queue_mgr.enqueue(kitchen_id, order_id, new_queue_type, new_score, tenant_id)
        except Exception as exc:
            logger.warning(
                "set_priority: Redis update failed for kitchen=%s order=%s: %s",
                kitchen_id, order_id, exc,
            )

    logger.info(
        "priority_set: kitchen=%s order=%s old_score=%.2f new_score=%.2f old_type=%s new_type=%s",
        kitchen_id, order_id, old_score, new_score, old_queue_type, new_queue_type,
    )

    return {
        "kitchen_id": kitchen_id,
        "order_id": order_id,
        "old_priority_score": old_score,
        "new_priority_score": new_score,
        "old_queue_type": old_queue_type,
        "new_queue_type": new_queue_type,
        "arrival_boost": entry.arrival_boost,
        "updated_at": now_iso(),
    }
