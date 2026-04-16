"""
HotSot Order Service — Admin Routes.

Administrative endpoints for:
- Order statistics dashboard
- Active saga instances
- Event replay
- Dead letter queue management
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth.jwt import require_role
from shared.types.schemas import (
    EventEnvelope,
    EventType,
    KAFKA_TOPICS,
)
from shared.utils.database import get_session_factory, set_tenant_id
from shared.utils.helpers import generate_id, now_iso

from app.core.database import OrderModel, OrderEventModel, PaymentModel, SagaInstanceModel, DLQMessageModel
from app.core.saga import order_saga

logger = logging.getLogger(__name__)

router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════

class ReplayEventRequest(BaseModel):
    """Request to replay a specific event."""
    event_id: Optional[str] = None
    order_id: Optional[str] = None
    event_type: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    reason: str = "manual_replay"


# ═══════════════════════════════════════════════════════════════
# DEPENDENCIES
# ═══════════════════════════════════════════════════════════════

async def get_db():
    """Database session dependency."""
    session_factory = get_session_factory("order")
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_kafka_producer(request: Request):
    """Get the Kafka producer from app state."""
    return request.app.state.kafka_producer


# ═══════════════════════════════════════════════════════════════
# 1. ORDER STATS DASHBOARD
# ═══════════════════════════════════════════════════════════════
@router.get("/stats")
async def get_stats(
    user: dict = Depends(require_role("admin", "vendor_admin")),
    session: AsyncSession = Depends(get_db),
):
    """
    Get order statistics dashboard data.

    Returns:
    - Orders by status
    - Total counts
    - Average processing time
    - Payment stats
    """
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    # Orders by status
    result = await session.execute(
        select(OrderModel.status, func.count(OrderModel.id))
        .where(OrderModel.tenant_id == tenant_id)
        .group_by(OrderModel.status)
    )
    status_counts = {row[0]: row[1] for row in result.all()}

    # Total orders
    total_result = await session.execute(
        select(func.count(OrderModel.id))
        .where(OrderModel.tenant_id == tenant_id)
    )
    total_orders = total_result.scalar() or 0

    # Total events
    event_result = await session.execute(
        select(func.count(OrderEventModel.id))
        .where(OrderEventModel.tenant_id == tenant_id)
    )
    total_events = event_result.scalar() or 0

    # Payment stats
    payment_result = await session.execute(
        select(PaymentModel.status, func.count(PaymentModel.id), func.sum(PaymentModel.amount))
        .where(PaymentModel.tenant_id == tenant_id)
        .group_by(PaymentModel.status)
    )
    payment_stats = {}
    for row in payment_result.all():
        payment_stats[row[0]] = {
            "count": row[1],
            "total_amount": Decimal(str(row[2])) if row[2] else Decimal("0"),
        }

    # Active sagas (from in-memory engine)
    active_sagas = [
        saga for saga in order_saga._active_sagas.values()
        if saga.get("tenant_id") == tenant_id and saga.get("status") == "RUNNING"
    ]

    # Average time from CREATED to PICKED
    avg_time_result = await session.execute(
        select(
            func.avg(
                func.extract("epoch", OrderModel.picked_at - OrderModel.created_at)
            )
        ).where(
            OrderModel.tenant_id == tenant_id,
            OrderModel.status == "PICKED",
            OrderModel.picked_at.isnot(None),
        )
    )
    avg_completion_seconds = avg_time_result.scalar()

    return {
        "tenant_id": tenant_id,
        "orders_by_status": status_counts,
        "total_orders": total_orders,
        "total_events": total_events,
        "payment_stats": payment_stats,
        "active_sagas_count": len(active_sagas),
        "avg_completion_seconds": round(float(avg_completion_seconds), 1) if avg_completion_seconds else None,
        "generated_at": now_iso(),
    }


# ═══════════════════════════════════════════════════════════════
# 2. ACTIVE SAGA INSTANCES
# ═══════════════════════════════════════════════════════════════
@router.get("/sagas")
async def get_sagas(
    status_filter: Optional[str] = None,
    user: dict = Depends(require_role("admin", "vendor_admin")),
    session: AsyncSession = Depends(get_db),
):
    """
    Get active saga instances.

    Returns in-memory sagas and can also query persisted sagas from DB.
    """
    tenant_id = user.get("tenant_id", "default")

    # Get in-memory active sagas
    sagas = [
        saga for saga in order_saga._active_sagas.values()
        if saga.get("tenant_id") == tenant_id and saga.get("status") in ("RUNNING", "COMPENSATING")
    ]

    # Also query persisted sagas
    await set_tenant_id(session, tenant_id)
    query = select(SagaInstanceModel).where(
        SagaInstanceModel.tenant_id == tenant_id,
    )
    if status_filter:
        query = query.where(SagaInstanceModel.status == status_filter)
    else:
        query = query.where(SagaInstanceModel.status.in_(["RUNNING", "COMPENSATING"]))

    query = query.order_by(SagaInstanceModel.created_at.desc()).limit(100)
    result = await session.execute(query)
    db_sagas = result.scalars().all()

    # Combine results
    in_memory = sagas  # Already dicts from _active_sagas
    persisted = [{
        "saga_id": str(s.id),
        "order_id": str(s.order_id),
        "tenant_id": s.tenant_id,
        "saga_type": s.saga_type,
        "current_step": s.current_step,
        "status": s.status,
        "steps_completed": s.steps_completed,
        "steps_failed": s.steps_failed,
        "error_message": s.error_message,
        "started_at": s.created_at.isoformat() if s.created_at else None,
    } for s in db_sagas]

    return {
        "tenant_id": tenant_id,
        "in_memory_sagas": in_memory,
        "persisted_sagas": persisted,
        "total_active": len(in_memory) + len(persisted),
    }


# ═══════════════════════════════════════════════════════════════
# 3. REPLAY EVENT
# ═══════════════════════════════════════════════════════════════
@router.post("/replay-event")
async def replay_event(
    req: ReplayEventRequest,
    user: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
):
    """
    Replay a specific event.

    Used for:
    - Re-processing events that failed due to transient errors
    - Re-triggering saga steps after fixes
    - Manual recovery from DLQ

    The event is re-published to Kafka with the same payload
    but a new event_id and correlation_id.
    """
    tenant_id = user.get("tenant_id", "default")

    # Find the original event
    await set_tenant_id(session, tenant_id)
    query = select(OrderEventModel).where(
        OrderEventModel.tenant_id == tenant_id,
    )

    if req.event_id:
        query = query.where(OrderEventModel.id == uuid.UUID(req.event_id))
    elif req.order_id and req.event_type:
        query = query.where(
            OrderEventModel.order_id == uuid.UUID(req.order_id),
            OrderEventModel.event_type == req.event_type,
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide event_id or (order_id + event_type)",
        )

    result = await session.execute(query.order_by(OrderEventModel.created_at.desc()).limit(1))
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Build replay event envelope
    try:
        event_type = EventType(event.event_type)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown event type: {event.event_type}",
        )

    replay_payload = {
        **(event.payload or {}),
        **(req.payload or {}),
        "replayed": True,
        "original_event_id": str(event.id),
        "replay_reason": req.reason,
        "replayed_by": user.get("user_id"),
        "replayed_at": now_iso(),
    }

    envelope = EventEnvelope(
        event_id=generate_id(),
        event_type=event_type,
        order_id=str(event.order_id),
        tenant_id=tenant_id,
        source="order-service-admin",
        timestamp=now_iso(),
        schema_version=2,
        payload=replay_payload,
        correlation_id=f"replay-{generate_id()[:8]}",
    )

    if producer:
        success = await producer.publish_event(envelope)
        if not success:
            raise HTTPException(
                status_code=500,
                detail="Failed to publish replay event to Kafka",
            )

    return {
        "status": "replayed",
        "original_event_id": str(event.id),
        "replay_event_id": envelope.event_id,
        "event_type": event.event_type,
        "order_id": str(event.order_id),
        "replay_reason": req.reason,
        "replayed_by": user.get("user_id"),
        "timestamp": now_iso(),
    }


# ═══════════════════════════════════════════════════════════════
# 4. DEAD LETTER QUEUE
# ═══════════════════════════════════════════════════════════════
@router.get("/dlq")
async def get_dlq_messages(
    limit: int = 50,
    offset: int = 0,
    status_filter: Optional[str] = None,
    user: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_db),
):
    """
    Get Dead Letter Queue messages.

    Shows messages that failed processing after max retries.
    """
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    query = select(DLQMessageModel).where(
        DLQMessageModel.tenant_id == tenant_id,
    )

    if status_filter:
        query = query.where(DLQMessageModel.replayed == status_filter)

    query = query.order_by(DLQMessageModel.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    messages = result.scalars().all()

    # Get count
    count_result = await session.execute(
        select(func.count(DLQMessageModel.id))
        .where(DLQMessageModel.tenant_id == tenant_id)
    )
    total = count_result.scalar() or 0

    return {
        "tenant_id": tenant_id,
        "total": total,
        "messages": [{
            "id": str(m.id),
            "original_topic": m.original_topic,
            "original_event": m.original_event,
            "failure_reason": m.failure_reason,
            "consumer_service": m.consumer_service,
            "retry_count": m.retry_count,
            "last_error": m.last_error,
            "status": m.replayed,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        } for m in messages],
    }


# ═══════════════════════════════════════════════════════════════
# 5. RECENT EVENTS
# ═══════════════════════════════════════════════════════════════
@router.get("/events/recent")
async def get_recent_events(
    limit: int = 50,
    user: dict = Depends(require_role("admin", "vendor_admin")),
    session: AsyncSession = Depends(get_db),
):
    """Get recent events across all orders."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    result = await session.execute(
        select(OrderEventModel)
        .where(OrderEventModel.tenant_id == tenant_id)
        .order_by(OrderEventModel.created_at.desc())
        .limit(limit)
    )
    events = result.scalars().all()

    return [{
        "event_id": str(e.id),
        "order_id": str(e.order_id),
        "event_type": e.event_type,
        "payload": e.payload,
        "source": e.source,
        "sequence_number": e.sequence_number,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in events]


# ═══════════════════════════════════════════════════════════════
# 6. KITCHEN PERFORMANCE
# ═══════════════════════════════════════════════════════════════
@router.get("/kitchen-performance")
async def get_kitchen_performance(
    user: dict = Depends(require_role("admin", "vendor_admin")),
    session: AsyncSession = Depends(get_db),
):
    """Get kitchen performance metrics."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    # Orders per kitchen
    result = await session.execute(
        select(
            OrderModel.kitchen_id,
            func.count(OrderModel.id),
        )
        .where(OrderModel.tenant_id == tenant_id)
        .group_by(OrderModel.kitchen_id)
    )
    kitchen_stats = {}
    for row in result.all():
        kitchen_stats[str(row[0])] = {"total_orders": row[1]}

    # Average completion time per kitchen
    avg_result = await session.execute(
        select(
            OrderModel.kitchen_id,
            func.avg(
                func.extract("epoch", OrderModel.picked_at - OrderModel.created_at)
            ),
        )
        .where(
            OrderModel.tenant_id == tenant_id,
            OrderModel.status == "PICKED",
            OrderModel.picked_at.isnot(None),
        )
        .group_by(OrderModel.kitchen_id)
    )
    for row in avg_result.all():
        kid = str(row[0])
        if kid in kitchen_stats:
            kitchen_stats[kid]["avg_completion_seconds"] = round(float(row[1]), 1) if row[1] else None

    return {
        "tenant_id": tenant_id,
        "kitchens": kitchen_stats,
    }
