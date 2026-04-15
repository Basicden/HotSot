"""HotSot Shelf Service — Shelf Management Routes."""

import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from shared.auth.jwt import get_current_user, require_role
from shared.utils.helpers import now_iso, generate_id
from shared.types.schemas import ShelfZone

from app.core.database import ShelfSlotModel, ShelfAssignmentModel
from app.core.ttl_engine import TTLEngine, SHELF_TTL_SECONDS

router = APIRouter()
_session_factory = None
_redis_client = None


def set_dependencies(session_factory, redis_client):
    global _session_factory, _redis_client
    _session_factory = session_factory
    _redis_client = redis_client


async def get_session():
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session


@router.get("/kitchen/{kitchen_id}")
async def list_kitchen_shelves(
    kitchen_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List all shelves for a kitchen."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    result = await session.execute(
        select(ShelfSlotModel).where(
            ShelfSlotModel.kitchen_id == uuid.UUID(kitchen_id),
            ShelfSlotModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    shelves = result.scalars().all()

    # Enrich with TTL info
    ttl_engine = TTLEngine(_redis_client) if _redis_client else None
    shelf_data = []
    for s in shelves:
        info = {
            "shelf_id": s.id,
            "zone": s.zone,
            "status": s.status,
            "order_id": str(s.order_id) if s.order_id else None,
        }
        if ttl_engine:
            ttl_status = await ttl_engine.check_shelf_status(s.id, tenant_id)
            if ttl_status:
                info["ttl_remaining"] = ttl_status.get("ttl_remaining")
                info["warning_level"] = ttl_status.get("warning_level")
        shelf_data.append(info)

    return {"kitchen_id": kitchen_id, "shelves": shelf_data}


@router.post("/assign")
async def assign_shelf(
    order_id: str,
    kitchen_id: str,
    zone: str = "HOT",
    shelf_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Assign order to a shelf slot."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    ttl_seconds = SHELF_TTL_SECONDS.get(zone, 600)

    # Auto-assign shelf if not specified
    if not shelf_id:
        result = await session.execute(
            select(ShelfSlotModel).where(
                ShelfSlotModel.kitchen_id == uuid.UUID(kitchen_id),
                ShelfSlotModel.tenant_id == uuid.UUID(tenant_id),
                ShelfSlotModel.status == "AVAILABLE",
                ShelfSlotModel.zone == zone,
            ).limit(1)
        )
        slot = result.scalar_one_or_none()
        if not slot:
            raise HTTPException(status_code=409, detail="No available shelf slots in this zone")
        shelf_id = slot.id
    else:
        result = await session.execute(
            select(ShelfSlotModel).where(ShelfSlotModel.id == shelf_id)
        )
        slot = result.scalar_one_or_none()
        if not slot:
            raise HTTPException(status_code=404, detail="Shelf not found")
        if slot.status != "AVAILABLE":
            raise HTTPException(status_code=409, detail="Shelf is not available")

    # Acquire distributed lock
    if _redis_client:
        lock_key = f"shelf_lock:{tenant_id}:{shelf_id}"
        acquired = await _redis_client.client.set(lock_key, order_id, nx=True, ex=ttl_seconds)
        if not acquired:
            raise HTTPException(status_code=409, detail="Shelf lock acquisition failed")

    # Update shelf slot
    slot.status = "OCCUPIED"
    slot.order_id = uuid.UUID(order_id)
    slot.ttl_seconds = ttl_seconds
    slot.assigned_at = datetime.now(timezone.utc)
    await session.commit()

    # Create assignment record
    assignment = ShelfAssignmentModel(
        tenant_id=uuid.UUID(tenant_id),
        order_id=uuid.UUID(order_id),
        kitchen_id=uuid.UUID(kitchen_id),
        shelf_id=shelf_id,
        zone=zone,
        ttl_seconds=ttl_seconds,
        ttl_remaining=ttl_seconds,
    )
    session.add(assignment)
    await session.commit()

    # Set TTL tracking
    if _redis_client:
        ttl_engine = TTLEngine(_redis_client)
        await ttl_engine.assign_shelf_ttl(shelf_id, order_id, zone, kitchen_id, tenant_id)

    return {
        "shelf_id": shelf_id,
        "order_id": order_id,
        "zone": zone,
        "ttl_seconds": ttl_seconds,
        "assigned": True,
    }


@router.post("/{shelf_id}/release")
async def release_shelf(
    shelf_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Release a shelf slot."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    result = await session.execute(
        select(ShelfSlotModel).where(ShelfSlotModel.id == shelf_id)
    )
    slot = result.scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Shelf not found")

    # Release distributed lock
    if _redis_client:
        lock_key = f"shelf_lock:{tenant_id}:{shelf_id}"
        await _redis_client.client.delete(lock_key)

    order_id = str(slot.order_id) if slot.order_id else None
    slot.status = "AVAILABLE"
    slot.order_id = None
    slot.released_at = datetime.now(timezone.utc)
    await session.commit()

    return {"shelf_id": shelf_id, "released": True, "order_id": order_id}


@router.post("/{shelf_id}/extend-ttl")
async def extend_ttl(
    shelf_id: str,
    additional_seconds: int = 300,
    user: dict = Depends(require_role("vendor_admin")),
):
    """Extend shelf TTL (warm station extension)."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    if not _redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    ttl_engine = TTLEngine(_redis_client)
    extended = await ttl_engine.extend_ttl(shelf_id, tenant_id, additional_seconds)

    if not extended:
        raise HTTPException(status_code=404, detail="Shelf TTL not found")

    return {"shelf_id": shelf_id, "extended_by": additional_seconds, "extended": True}


@router.get("/{shelf_id}/status")
async def get_shelf_status(
    shelf_id: str,
    user: dict = Depends(get_current_user),
):
    """Get shelf TTL status."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    if not _redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    ttl_engine = TTLEngine(_redis_client)
    status = await ttl_engine.check_shelf_status(shelf_id, tenant_id)

    if not status:
        raise HTTPException(status_code=404, detail="Shelf status not found")

    return status


@router.get("/kitchen/{kitchen_id}/expired")
async def get_expired_shelves(
    kitchen_id: str,
    user: dict = Depends(get_current_user),
):
    """Get all expired shelves for a kitchen."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    if not _redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    ttl_engine = TTLEngine(_redis_client)
    expired = await ttl_engine.get_expired_shelves(kitchen_id, tenant_id)

    return {"kitchen_id": kitchen_id, "expired_shelves": expired}
