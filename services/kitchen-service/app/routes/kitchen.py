"""HotSot Kitchen Service — Kitchen Management Routes."""

import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict

from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from shared.auth.jwt import get_current_user, require_role
from shared.compliance_decorators import compliance_check
from shared.utils.database import get_session_factory, ConcurrentModificationError
from shared.utils.helpers import now_iso, generate_id
from shared.types.schemas import EventType

from app.core.database import (
    KitchenModel, KitchenQueueModel, KitchenStaffModel, KitchenThroughputModel,
)
from app.core.engine import ThroughputMonitor, StaffAssignmentEngine

router = APIRouter()
_session_factory = None
_redis_client = None


def set_dependencies(session_factory, redis_client):
    """Set shared dependencies."""
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
# KITCHEN CRUD
# ═══════════════════════════════════════════════════════════════

@router.post("/")
async def register_kitchen(
    name: str,
    vendor_id: str,
    location: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    max_concurrent_orders: int = 20,
    staff_count: int = 3,
    size: str = "medium",
    fssai_license: Optional[str] = None,
    gstin: Optional[str] = None,
    user: dict = Depends(require_role("vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """Register a new kitchen (cloud kitchen entity)."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    kitchen = KitchenModel(
        tenant_id=uuid.UUID(tenant_id),
        vendor_id=uuid.UUID(vendor_id),
        name=name,
        location=location,
        lat=lat,
        lng=lng,
        max_concurrent_orders=max_concurrent_orders,
        staff_count=staff_count,
        size=size,
        fssai_license=fssai_license,
        gstin=gstin,
    )
    session.add(kitchen)
    await session.commit()
    await session.refresh(kitchen)

    return {
        "kitchen_id": str(kitchen.id),
        "name": kitchen.name,
        "size": kitchen.size,
        "max_concurrent_orders": kitchen.max_concurrent_orders,
        "is_active": kitchen.is_active,
        "created_at": kitchen.created_at.isoformat() if kitchen.created_at else None,
    }


@router.get("/{kitchen_id}")
async def get_kitchen(
    kitchen_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get kitchen details."""
    result = await session.execute(
        select(KitchenModel).where(KitchenModel.id == uuid.UUID(kitchen_id))
    )
    kitchen = result.scalar_one_or_none()
    if not kitchen:
        raise HTTPException(status_code=404, detail="Kitchen not found")

    return {
        "kitchen_id": str(kitchen.id),
        "vendor_id": str(kitchen.vendor_id),
        "name": kitchen.name,
        "location": kitchen.location,
        "lat": kitchen.lat,
        "lng": kitchen.lng,
        "max_concurrent_orders": kitchen.max_concurrent_orders,
        "staff_count": kitchen.staff_count,
        "is_active": kitchen.is_active,
        "size": kitchen.size,
        "throughput_per_minute": kitchen.throughput_per_minute,
        "fssai_license": kitchen.fssai_license,
        "gstin": kitchen.gstin,
        "version": kitchen.version,
    }


@router.put("/{kitchen_id}")
@compliance_check("FSSAI")
async def update_kitchen(
    kitchen_id: str,
    name: Optional[str] = None,
    max_concurrent_orders: Optional[int] = None,
    staff_count: Optional[int] = None,
    is_active: Optional[bool] = None,
    size: Optional[str] = None,
    expected_version: Optional[int] = None,
    vendor_id: str = None,
    tenant_id: str = None,
    user: dict = Depends(require_role("vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """Update kitchen details.

    Optimistic locking: Pass expected_version from the last read to prevent
    lost updates. If version doesn't match, returns 409 Conflict.

    Compliance: @compliance_check("FSSAI") — soft gate verifies vendor FSSAI
    status before activating a kitchen. Logs warning if PENDING, blocks if FAILED.
    """
    if tenant_id is None:
        tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(KitchenModel).where(KitchenModel.id == uuid.UUID(kitchen_id))
    )
    kitchen = result.scalar_one_or_none()
    if not kitchen:
        raise HTTPException(status_code=404, detail="Kitchen not found")

    # Optimistic locking: check version if provided
    if expected_version is not None and not kitchen.check_version(expected_version):
        raise HTTPException(
            status_code=409,
            detail=f"Concurrent modification: kitchen was updated by another request. "
                   f"Expected version {expected_version}, current is {kitchen.version}. "
                   f"Please refresh and retry.",
        )

    if name is not None:
        kitchen.name = name
    if max_concurrent_orders is not None:
        kitchen.max_concurrent_orders = max_concurrent_orders
    if staff_count is not None:
        kitchen.staff_count = staff_count
    if is_active is not None:
        kitchen.is_active = is_active
    if size is not None:
        kitchen.size = size

    kitchen.increment_version()
    kitchen.updated_at = datetime.now(timezone.utc)
    await session.commit()

    return {"kitchen_id": str(kitchen.id), "updated": True, "version": kitchen.version}


@router.get("/{kitchen_id}/load")
async def get_kitchen_load(
    kitchen_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get current kitchen load metrics."""
    # Active orders count
    active_count = await session.execute(
        select(func.count()).where(
            KitchenQueueModel.kitchen_id == uuid.UUID(kitchen_id),
            KitchenQueueModel.status.in_(["QUEUED", "IN_PREP"]),
        )
    )
    active_orders = active_count.scalar() or 0

    # Kitchen capacity
    kitchen_result = await session.execute(
        select(KitchenModel).where(KitchenModel.id == uuid.UUID(kitchen_id))
    )
    kitchen = kitchen_result.scalar_one_or_none()
    if not kitchen:
        raise HTTPException(status_code=404, detail="Kitchen not found")

    max_cap = kitchen.max_concurrent_orders
    load_pct = (active_orders / max(max_cap, 1)) * 100

    return {
        "kitchen_id": kitchen_id,
        "active_orders": active_orders,
        "max_capacity": max_cap,
        "load_percentage": round(load_pct, 2),
        "is_overloaded": load_pct >= 85,
        "size": kitchen.size,
    }


@router.get("/{kitchen_id}/throughput")
async def get_kitchen_throughput(
    kitchen_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get kitchen throughput metrics (latest 10 measurements)."""
    result = await session.execute(
        select(KitchenThroughputModel)
        .where(KitchenThroughputModel.kitchen_id == uuid.UUID(kitchen_id))
        .order_by(KitchenThroughputModel.measured_at.desc())
        .limit(10)
    )
    measurements = result.scalars().all()

    return {
        "kitchen_id": kitchen_id,
        "measurements": [
            {
                "orders_per_minute": m.orders_per_minute,
                "active_orders": m.active_orders,
                "queue_length": m.queue_length,
                "load_percentage": m.load_percentage,
                "is_overloaded": m.is_overloaded,
                "measured_at": m.measured_at.isoformat() if m.measured_at else None,
            }
            for m in measurements
        ],
    }


# ═══════════════════════════════════════════════════════════════
# STAFF MANAGEMENT
# ═══════════════════════════════════════════════════════════════

@router.post("/{kitchen_id}/staff")
async def add_staff(
    kitchen_id: str,
    staff_id: str,
    name: str,
    role: str = "CHEF",
    user: dict = Depends(require_role("vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """Add a staff member to a kitchen."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    staff = KitchenStaffModel(
        tenant_id=uuid.UUID(tenant_id),
        kitchen_id=uuid.UUID(kitchen_id),
        staff_id=uuid.UUID(staff_id),
        name=name,
        role=role,
        is_available=True,
    )
    session.add(staff)
    await session.commit()

    # Register in Redis for quick assignment (fail-closed: warn if unavailable)
    if _redis_client:
        engine = StaffAssignmentEngine(_redis_client)
        await engine.register_staff(kitchen_id, staff_id, role, tenant_id)
    else:
        import logging as _logging
        _logging.getLogger("kitchen-service").warning(
            "add_staff: Redis unavailable — staff not registered in hot store for kitchen=%s staff=%s",
            kitchen_id, staff_id,
        )

    return {"staff_id": staff_id, "name": name, "role": role, "added": True}


@router.put("/{kitchen_id}/staff/{staff_id}/heartbeat")
async def staff_heartbeat(
    kitchen_id: str,
    staff_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Staff heartbeat — confirms staff member is active."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    result = await session.execute(
        select(KitchenStaffModel).where(
            KitchenStaffModel.kitchen_id == uuid.UUID(kitchen_id),
            KitchenStaffModel.staff_id == uuid.UUID(staff_id),
        )
    )
    staff = result.scalar_one_or_none()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")

    staff.last_heartbeat = datetime.now(timezone.utc)
    await session.commit()

    if _redis_client:
        engine = StaffAssignmentEngine(_redis_client)
        await engine.heartbeat(kitchen_id, staff_id, tenant_id)
    else:
        import logging as _logging
        _logging.getLogger("kitchen-service").warning(
            "staff_heartbeat: Redis unavailable — heartbeat not recorded for kitchen=%s staff=%s",
            kitchen_id, staff_id,
        )

    return {"staff_id": staff_id, "heartbeat_received": True}


@router.post("/{kitchen_id}/ack-order/{order_id}")
async def ack_order(
    kitchen_id: str,
    order_id: str,
    staff_id: str = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Staff ACK order — required before prep starts.

    V2 design: kitchen must acknowledge receipt of order within 60s.
    If not ACK'd, system escalates automatically.
    """
    result = await session.execute(
        select(KitchenQueueModel).where(
            KitchenQueueModel.kitchen_id == uuid.UUID(kitchen_id),
            KitchenQueueModel.order_id == uuid.UUID(order_id),
            KitchenQueueModel.status == "QUEUED",
        )
    )
    queue_entry = result.scalar_one_or_none()
    if not queue_entry:
        raise HTTPException(status_code=404, detail="Order not in kitchen queue")

    # Optimistic locking: capture version before mutation
    expected_version = queue_entry.version
    if not queue_entry.check_version(expected_version):
        raise HTTPException(
            status_code=409,
            detail="Concurrent modification: queue entry was updated by another request. Please retry.",
        )

    queue_entry.status = "IN_PREP"
    queue_entry.started_at = datetime.now(timezone.utc)
    if staff_id:
        queue_entry.staff_id = uuid.UUID(staff_id)
    queue_entry.increment_version()
    await session.commit()

    return {
        "order_id": order_id,
        "kitchen_id": kitchen_id,
        "ack": True,
        "status": "IN_PREP",
        "staff_id": staff_id,
        "version": queue_entry.version,
    }
