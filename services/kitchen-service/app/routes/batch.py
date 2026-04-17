"""HotSot Kitchen Service — Batch Cooking Management Routes.

Batch cooking management endpoints: create batches, add items,
mark batches complete, and list active batches.

Batch cooking groups orders by cooking category (GRILL, FRYER, COLD,
RICE_BOWL) for efficiency — a tandoor can cook 6 kebabs in the same
time as 1, saving 40-60% kitchen time during peak hours.

Uses Redis for real-time batch candidate tracking and PostgreSQL
for persistent batch group records. Follows the set_dependencies()
injection pattern used across the service.
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
from shared.utils.helpers import now_iso, generate_id
from shared.types.schemas import EventType, BatchCategory, BATCH_CATEGORY_MAP

from app.core.database import BatchGroupModel, KitchenQueueModel, KitchenModel
from app.core.engine import BatchEngine, PriorityScoreCalculator

logger = logging.getLogger("kitchen-service.batch")

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
# BATCH CREATION
# ═══════════════════════════════════════════════════════════════

@router.post("/")
async def create_batch(
    kitchen_id: str,
    category: str,
    order_ids: List[str] = [],
    user: dict = Depends(require_role("vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """Create a new batch cooking group.

    A batch groups orders with the same cooking category so they can
    be prepared together for efficiency. The kitchen must exist and
    the category must be a valid BatchCategory.

    Optionally provide initial order_ids to seed the batch, or let
    the system suggest candidates from the Redis candidate pool.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Validate category
    valid_categories = [bc.value for bc in BatchCategory]
    if category not in valid_categories:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid batch category. Must be one of: {valid_categories}",
        )

    # Verify kitchen exists
    kitchen_result = await session.execute(
        select(KitchenModel).where(KitchenModel.id == uuid.UUID(kitchen_id))
    )
    kitchen = kitchen_result.scalar_one_or_none()
    if not kitchen:
        raise HTTPException(status_code=404, detail="Kitchen not found")

    # If no order_ids provided, try to get candidates from Redis
    resolved_order_ids = list(order_ids)
    if not resolved_order_ids and _redis_client:
        try:
            batch_engine = BatchEngine(_redis_client)
            suggestions = await batch_engine.suggest_batch(kitchen_id, tenant_id)
            for suggestion in suggestions:
                if suggestion.get("category") == category:
                    resolved_order_ids = suggestion.get("order_ids", [])
                    break
        except Exception as exc:
            logger.warning(
                "create_batch: Redis suggest failed for kitchen=%s: %s",
                kitchen_id, exc,
            )

    # Create batch group record
    batch_id = uuid.uuid4()
    batch_group = BatchGroupModel(
        tenant_id=uuid.UUID(tenant_id),
        kitchen_id=uuid.UUID(kitchen_id),
        batch_id=batch_id,
        category=category,
        order_ids=[uuid.UUID(oid) for oid in resolved_order_ids],
        status="FORMING",
    )
    session.add(batch_group)

    # Update kitchen queue entries to reference this batch
    if resolved_order_ids:
        for oid in resolved_order_ids:
            result = await session.execute(
                select(KitchenQueueModel).where(
                    KitchenQueueModel.kitchen_id == uuid.UUID(kitchen_id),
                    KitchenQueueModel.order_id == uuid.UUID(oid),
                    KitchenQueueModel.tenant_id == uuid.UUID(tenant_id),
                    KitchenQueueModel.status == "QUEUED",
                )
            )
            entry = result.scalar_one_or_none()
            if entry:
                entry.batch_id = batch_group.id
                entry.batch_category = category

    await session.commit()
    await session.refresh(batch_group)

    # Remove candidates from Redis pool
    if _redis_client:
        try:
            batch_engine = BatchEngine(_redis_client)
            for oid in resolved_order_ids:
                await batch_engine.remove_from_batch_candidates(
                    kitchen_id, oid, category, tenant_id
                )
        except Exception as exc:
            logger.warning(
                "create_batch: Redis candidate removal failed for kitchen=%s: %s",
                kitchen_id, exc,
            )

    logger.info(
        "batch_created: kitchen=%s batch=%s category=%s items=%d",
        kitchen_id, batch_id, category, len(resolved_order_ids),
    )

    return {
        "batch_id": str(batch_id),
        "kitchen_id": kitchen_id,
        "category": category,
        "order_ids": resolved_order_ids,
        "status": batch_group.status,
        "item_count": len(resolved_order_ids),
        "created_at": batch_group.created_at.isoformat() if batch_group.created_at else None,
    }


# ═══════════════════════════════════════════════════════════════
# ADD ITEMS TO BATCH
# ═══════════════════════════════════════════════════════════════

@router.post("/{batch_id}/items")
async def add_batch_items(
    batch_id: str,
    order_ids: List[str],
    user: dict = Depends(require_role("vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """Add items to an existing batch.

    The batch must be in FORMING status — once cooking starts
    (IN_PROGRESS), items cannot be added.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Find batch
    result = await session.execute(
        select(BatchGroupModel).where(
            BatchGroupModel.batch_id == uuid.UUID(batch_id),
            BatchGroupModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch.status not in ("FORMING",):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot add items to batch in {batch.status} status. Batch must be in FORMING status.",
        )

    # Merge new order IDs
    existing_ids = set(str(oid) for oid in (batch.order_ids or []))
    new_ids = [oid for oid in order_ids if oid not in existing_ids]
    merged_ids = list(existing_ids) + new_ids

    batch.order_ids = [uuid.UUID(oid) for oid in merged_ids]
    await session.commit()

    # Update kitchen queue entries for new orders
    for oid in new_ids:
        queue_result = await session.execute(
            select(KitchenQueueModel).where(
                KitchenQueueModel.kitchen_id == batch.kitchen_id,
                KitchenQueueModel.order_id == uuid.UUID(oid),
                KitchenQueueModel.tenant_id == uuid.UUID(tenant_id),
                KitchenQueueModel.status == "QUEUED",
            )
        )
        entry = queue_result.scalar_one_or_none()
        if entry:
            entry.batch_id = batch.id
            entry.batch_category = batch.category

    # Remove new items from Redis candidate pool
    if _redis_client:
        try:
            batch_engine = BatchEngine(_redis_client)
            for oid in new_ids:
                await batch_engine.remove_from_batch_candidates(
                    str(batch.kitchen_id), oid, batch.category, tenant_id
                )
        except Exception as exc:
            logger.warning(
                "add_batch_items: Redis candidate removal failed for batch=%s: %s",
                batch_id, exc,
            )

    await session.commit()

    logger.info(
        "batch_items_added: batch=%s new_items=%d total=%d",
        batch_id, len(new_ids), len(merged_ids),
    )

    return {
        "batch_id": batch_id,
        "category": batch.category,
        "added_count": len(new_ids),
        "total_items": len(merged_ids),
        "order_ids": merged_ids,
        "status": batch.status,
        "updated_at": now_iso(),
    }


# ═══════════════════════════════════════════════════════════════
# MARK BATCH COMPLETE
# ═══════════════════════════════════════════════════════════════

@router.put("/{batch_id}/complete")
async def complete_batch(
    batch_id: str,
    user: dict = Depends(require_role("vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """Mark a batch as complete.

    Transitions batch status from IN_PROGRESS (or FORMING) to COMPLETED.
    Also updates all associated kitchen queue entries to COMPLETED status.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Find batch
    result = await session.execute(
        select(BatchGroupModel).where(
            BatchGroupModel.batch_id == uuid.UUID(batch_id),
            BatchGroupModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch.status == "COMPLETED":
        raise HTTPException(status_code=409, detail="Batch already completed")

    # Update batch status
    old_status = batch.status
    batch.status = "COMPLETED"
    batch.completed_at = datetime.now(timezone.utc)

    # Update all queue entries for orders in this batch
    now = datetime.now(timezone.utc)
    for oid in (batch.order_ids or []):
        queue_result = await session.execute(
            select(KitchenQueueModel).where(
                KitchenQueueModel.kitchen_id == batch.kitchen_id,
                KitchenQueueModel.order_id == oid,
                KitchenQueueModel.tenant_id == uuid.UUID(tenant_id),
                KitchenQueueModel.status.in_(["QUEUED", "IN_PREP"]),
            )
        )
        entry = queue_result.scalar_one_or_none()
        if entry:
            entry.status = "COMPLETED"
            entry.completed_at = now

    await session.commit()

    logger.info(
        "batch_completed: batch=%s kitchen=%s category=%s old_status=%s items=%d",
        batch_id, str(batch.kitchen_id), batch.category, old_status,
        len(batch.order_ids or []),
    )

    return {
        "batch_id": batch_id,
        "category": batch.category,
        "old_status": old_status,
        "new_status": "COMPLETED",
        "items_completed": len(batch.order_ids or []),
        "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
    }


# ═══════════════════════════════════════════════════════════════
# LIST ACTIVE BATCHES
# ═══════════════════════════════════════════════════════════════

@router.get("/active")
async def list_active_batches(
    kitchen_id: Optional[str] = Query(None, description="Filter by kitchen ID"),
    category: Optional[str] = Query(None, description="Filter by batch category"),
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List active (non-completed) batches.

    Returns batches in FORMING or IN_PROGRESS status.
    Optionally filter by kitchen_id and/or category.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Build query
    query = select(BatchGroupModel).where(
        BatchGroupModel.tenant_id == uuid.UUID(tenant_id),
        BatchGroupModel.status.in_(["FORMING", "IN_PROGRESS"]),
    )

    if kitchen_id:
        query = query.where(BatchGroupModel.kitchen_id == uuid.UUID(kitchen_id))
    if category:
        valid_categories = [bc.value for bc in BatchCategory]
        if category not in valid_categories:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category. Must be one of: {valid_categories}",
            )
        query = query.where(BatchGroupModel.category == category)

    query = query.order_by(BatchGroupModel.created_at.desc())

    result = await session.execute(query)
    batches = result.scalars().all()

    # Also check Redis for batch suggestions if kitchen_id provided
    suggestions = []
    if kitchen_id and _redis_client:
        try:
            batch_engine = BatchEngine(_redis_client)
            suggestions = await batch_engine.suggest_batch(kitchen_id, tenant_id)
        except Exception as exc:
            logger.warning(
                "list_active_batches: Redis suggest failed for kitchen=%s: %s",
                kitchen_id, exc,
            )

    active_batches = []
    for batch in batches:
        active_batches.append({
            "batch_id": str(batch.batch_id),
            "kitchen_id": str(batch.kitchen_id),
            "category": batch.category,
            "status": batch.status,
            "item_count": len(batch.order_ids or []),
            "order_ids": [str(oid) for oid in (batch.order_ids or [])],
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
        })

    return {
        "tenant_id": tenant_id,
        "kitchen_id_filter": kitchen_id,
        "category_filter": category,
        "active_batches": active_batches,
        "total_active": len(active_batches),
        "batch_suggestions": suggestions,
        "retrieved_at": now_iso(),
    }
