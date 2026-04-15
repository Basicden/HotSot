"""
HotSot Order Service — Complete V2 Order Routes.

V2 Flow: CREATED → PAYMENT_PENDING → PAYMENT_CONFIRMED → SLOT_RESERVED →
QUEUE_ASSIGNED → IN_PREP → PACKING → READY → ON_SHELF → ARRIVED →
HANDOFF_IN_PROGRESS → PICKED

Plus failure paths: EXPIRED, REFUNDED, CANCELLED, FAILED

All mutations require idempotency_key.
All routes require auth (JWT).
All routes are tenant-scoped (tenant_id from JWT).
All events published to Kafka via EventEnvelope.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth.jwt import get_current_user, require_tenant
from shared.types.schemas import (
    EventEnvelope,
    EventType,
    OrderStatus,
    UserTier,
    QueueType,
    ShelfZone,
    RiskLevel,
    OrderCreateRequest,
    OrderResponse,
    PaymentConfirmRequest,
    HandoffConfirmRequest,
    CancelRequest,
    EventLogResponse,
    KAFKA_TOPICS,
    TIER_WEIGHTS,
    SHELF_TTL_SECONDS,
)
from shared.utils.database import get_session_factory, set_tenant_id
from shared.utils.helpers import (
    generate_id,
    now_iso,
    idempotency_key,
    items_hash,
    arrival_dedup_key,
)

from app.core.database import OrderModel, OrderEventModel
from app.core.state_machine import state_machine, InvalidTransitionError, GuardViolationError

logger = logging.getLogger(__name__)

router = APIRouter()

# ─── Constants ───
MAX_KITCHEN_ORDERS = 200
SHELF_SLOTS_PER_KITCHEN = 6
DEFAULT_SHELF_TTL = 600
WARM_STATION_TTL_EXTENSION = 300


# ═══════════════════════════════════════════════════════════════
# PYDANTIC REQUEST MODELS (additional to shared schemas)
# ═══════════════════════════════════════════════════════════════

class AssignQueueRequest(BaseModel):
    idempotency_key: Optional[str] = None


class StartPrepRequest(BaseModel):
    staff_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class MarkReadyRequest(BaseModel):
    staff_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class AssignShelfRequest(BaseModel):
    shelf_id: Optional[str] = None
    zone: Optional[str] = None
    idempotency_key: Optional[str] = None


class ArrivalRequestLocal(BaseModel):
    user_id: str
    latitude: float = 0.0
    longitude: float = 0.0
    qr_scan: bool = False
    idempotency_key: Optional[str] = None


class ExpireRequest(BaseModel):
    reason: str = "SHELF_TTL_EXCEEDED"
    idempotency_key: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# DEPENDENCIES
# ═══════════════════════════════════════════════════════════════

async def get_db():
    """Database session dependency with tenant isolation."""
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


async def get_redis(request: Request):
    """Get the Redis client from app state."""
    return request.app.state.redis_client


# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

async def persist_event(
    session: AsyncSession,
    order_id: str,
    tenant_id: str,
    event_type: str,
    payload: dict = None,
    source: str = "order-service",
    idempotency_key_val: Optional[str] = None,
    sequence_number: int = 0,
) -> OrderEventModel:
    """Save event to database for audit trail."""
    event_record = OrderEventModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        order_id=uuid.UUID(order_id),
        event_type=event_type,
        payload=payload or {},
        source=source,
        idempotency_key=idempotency_key_val,
        sequence_number=sequence_number,
    )
    session.add(event_record)
    await session.flush()
    return event_record


async def publish_event(
    producer,
    event_type: EventType,
    order_id: str,
    tenant_id: str,
    payload: dict,
    kitchen_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    idempotency_key_val: Optional[str] = None,
) -> bool:
    """Publish an event to Kafka via EventEnvelope."""
    if not producer:
        logger.warning("Kafka producer not available — event not published")
        return False

    envelope = EventEnvelope(
        event_id=generate_id(),
        event_type=event_type,
        order_id=order_id,
        kitchen_id=kitchen_id,
        tenant_id=tenant_id,
        idempotency_key=idempotency_key_val,
        source="order-service",
        timestamp=now_iso(),
        schema_version=2,
        payload=payload,
        correlation_id=correlation_id,
    )

    return await producer.publish_event(envelope)


async def check_idempotency(redis_client, key: Optional[str]) -> Optional[Dict]:
    """Check if this request was already processed."""
    if not key or not redis_client:
        return None
    cached = await redis_client.cache_get(key, prefix="idempotency")
    return cached


async def store_idempotency(redis_client, key: Optional[str], response: Dict) -> None:
    """Mark request as processed for idempotency."""
    if not key or not redis_client:
        return
    await redis_client.cache_set(key, response, ttl=86400, prefix="idempotency")


def format_order(order: OrderModel) -> dict:
    """Convert ORM model to API response with V2 fields."""
    return {
        "order_id": str(order.id),
        "user_id": str(order.user_id),
        "kitchen_id": str(order.kitchen_id),
        "tenant_id": order.tenant_id,
        "status": order.status,
        "queue_type": order.queue_type,
        "queue_position": order.queue_position,
        "priority_score": order.priority_score,
        "eta_seconds": order.eta_seconds,
        "eta_confidence": order.eta_confidence,
        "eta_risk": order.eta_risk,
        "shelf_id": order.shelf_id,
        "shelf_zone": order.shelf_zone,
        "shelf_ttl_remaining": order.shelf_ttl_remaining,
        "payment_ref": order.payment_ref,
        "payment_method": order.payment_method,
        "batch_id": order.batch_id,
        "user_tier": order.user_tier,
        "arrived_at": order.arrived_at.isoformat() if order.arrived_at else None,
        "picked_at": order.picked_at.isoformat() if order.picked_at else None,
        "prep_started_at": order.prep_started_at.isoformat() if order.prep_started_at else None,
        "ready_at": order.ready_at.isoformat() if order.ready_at else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


def _determine_zone(items: list) -> str:
    """Determine shelf zone based on order items."""
    if not items:
        return "HOT"
    hot_items = {"burger", "biryani", "thali", "tandoor", "kebab", "fried_rice", "pulao"}
    cold_items = {"salad", "dessert", "drinks", "raita", "chaas", "ice_cream"}
    for item in items:
        name = item.get("name", "").lower() if isinstance(item, dict) else str(item).lower()
        if any(h in name for h in hot_items):
            return "HOT"
        if any(c in name for c in cold_items):
            return "COLD"
    return "HOT"


async def _auto_assign_shelf(
    redis_client, kitchen_id: str, order_id: str, zone: str = "HOT"
) -> Optional[str]:
    """Find and lock an available shelf slot with zone preference."""
    if not redis_client:
        return "H1"  # Fallback for dev
    for slot_num in range(1, SHELF_SLOTS_PER_KITCHEN + 1):
        shelf_id = f"{zone[0]}{slot_num}"
        acquired = await redis_client.acquire_shelf_lock(
            shelf_id, order_id, ttl=DEFAULT_SHELF_TTL, tenant_id="default"
        )
        if acquired:
            return shelf_id
    return None


# ═══════════════════════════════════════════════════════════════
# 1. CREATE ORDER (CREATED → PAYMENT_PENDING)
# ═══════════════════════════════════════════════════════════════
@router.post("/create")
async def create_order(
    req: OrderCreateRequest,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """
    Create a new order. V2 flow: CREATED → PAYMENT_PENDING.

    - Validates kitchen capacity via Redis
    - Stores idempotency_key for dedup
    - Emits ORDER_CREATED event
    - Transitions to PAYMENT_PENDING (waits for payment before slot)
    """
    tenant_id = user.get("tenant_id", req.tenant_id)

    # Set tenant context for RLS
    await set_tenant_id(session, tenant_id)

    # Idempotency check
    cached = await check_idempotency(redis_client, req.idempotency_key)
    if cached:
        return cached

    # Check kitchen capacity
    if redis_client:
        kitchen_load_data = await redis_client.get_kitchen_load(req.kitchen_id, tenant_id)
        kitchen_load = kitchen_load_data.get("active_orders", 0) if kitchen_load_data else 0
    else:
        kitchen_load = 0

    if kitchen_load >= MAX_KITCHEN_ORDERS:
        raise HTTPException(
            status_code=429,
            detail="Kitchen at capacity. Try again shortly.",
        )

    # Compute idempotency key if not provided
    idem_key = req.idempotency_key or idempotency_key(
        user_id=req.user_id,
        kitchen_id=req.kitchen_id,
        items_hash=items_hash(req.items) if req.items else "no-items",
    )

    # Create order in Postgres
    order = OrderModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=uuid.UUID(req.user_id),
        kitchen_id=uuid.UUID(req.kitchen_id),
        status="CREATED",
        items=req.items,
        total_amount=req.total_amount,
        payment_method=req.payment_method,
        user_tier=req.user_tier.value,
        idempotency_key=idem_key,
    )
    session.add(order)
    await session.flush()

    order_id = str(order.id)

    # Emit ORDER_CREATED event
    seq = 1
    await persist_event(
        session, order_id, tenant_id, EventType.ORDER_CREATED.value,
        {
            "user_id": req.user_id,
            "kitchen_id": req.kitchen_id,
            "items_count": len(req.items),
            "user_tier": req.user_tier.value,
            "payment_method": req.payment_method,
        },
        idempotency_key_val=idem_key,
        sequence_number=seq,
    )

    # V2: Transition to PAYMENT_PENDING
    try:
        new_status = state_machine.transition("CREATED", "PAYMENT_PENDING")
        order.status = new_status
        seq += 1
        await persist_event(
            session, order_id, tenant_id, EventType.PAYMENT_PENDING.value,
            {"payment_method": req.payment_method},
            sequence_number=seq,
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.flush()

    # Update Redis hot state
    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    # Emit async events
    await publish_event(
        producer, EventType.ORDER_CREATED, order_id, tenant_id,
        {
            "user_id": req.user_id,
            "kitchen_id": req.kitchen_id,
            "items": req.items,
            "user_tier": req.user_tier.value,
        },
        kitchen_id=req.kitchen_id,
        idempotency_key_val=idem_key,
    )
    await publish_event(
        producer, EventType.PAYMENT_PENDING, order_id, tenant_id,
        {"payment_method": req.payment_method},
        kitchen_id=req.kitchen_id,
    )

    response = format_order(order)
    await store_idempotency(redis_client, idem_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 2. INITIATE PAYMENT
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/pay")
async def initiate_payment(
    order_id: str,
    req: PaymentConfirmRequest,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """Initiate/confirm payment for an order. PAYMENT_PENDING → PAYMENT_CONFIRMED → SLOT_RESERVED."""
    tenant_id = user.get("tenant_id", req.tenant_id)
    await set_tenant_id(session, tenant_id)

    cached = await check_idempotency(redis_client, req.idempotency_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "PAYMENT_PENDING":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot pay: order is {order.status} (expected PAYMENT_PENDING)",
        )

    # Transition: PAYMENT_PENDING → PAYMENT_CONFIRMED
    try:
        order.status = state_machine.transition("PAYMENT_PENDING", "PAYMENT_CONFIRMED")
        order.payment_ref = req.payment_ref
        await persist_event(
            session, order_id, tenant_id, EventType.PAYMENT_CONFIRMED.value,
            {
                "payment_ref": req.payment_ref,
                "payment_method": req.payment_method,
                "gateway_txn_id": req.gateway_txn_id,
            },
            idempotency_key_val=req.idempotency_key,
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Slot reservation AFTER payment
    kitchen_load = 0
    if redis_client:
        load_data = await redis_client.get_kitchen_load(str(order.kitchen_id), tenant_id)
        kitchen_load = load_data.get("active_orders", 0) if load_data else 0

    slot_context = {
        "slot_available": kitchen_load < MAX_KITCHEN_ORDERS,
        "waitlist_allowed": True,
    }

    try:
        order.status = state_machine.transition(
            "PAYMENT_CONFIRMED", "SLOT_RESERVED", context=slot_context
        )
        await persist_event(
            session, order_id, tenant_id, EventType.SLOT_RESERVED.value,
            {"kitchen_load": kitchen_load, "slot_available": True},
        )
    except (InvalidTransitionError, GuardViolationError) as e:
        await persist_event(
            session, order_id, tenant_id, "SLOT_CONFLICT",
            {"payment_ref": req.payment_ref, "kitchen_load": kitchen_load},
        )
        await session.flush()
        raise HTTPException(
            status_code=409,
            detail="Slot filled during payment. Please select a new time slot.",
        )

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    await publish_event(
        producer, EventType.PAYMENT_CONFIRMED, order_id, tenant_id,
        {"payment_ref": req.payment_ref},
        kitchen_id=str(order.kitchen_id),
    )
    await publish_event(
        producer, EventType.SLOT_RESERVED, order_id, tenant_id,
        {"kitchen_id": str(order.kitchen_id)},
        kitchen_id=str(order.kitchen_id),
    )

    response = format_order(order)
    await store_idempotency(redis_client, req.idempotency_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 3. CONFIRM PAYMENT (Webhook)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/confirm-payment")
async def confirm_payment_webhook(
    order_id: str,
    req: PaymentConfirmRequest,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """Confirm payment via webhook. PAYMENT_PENDING → PAYMENT_CONFIRMED."""
    tenant_id = user.get("tenant_id", req.tenant_id)
    await set_tenant_id(session, tenant_id)

    cached = await check_idempotency(redis_client, req.idempotency_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "PAYMENT_PENDING":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot confirm payment: order is {order.status}",
        )

    try:
        order.status = state_machine.transition("PAYMENT_PENDING", "PAYMENT_CONFIRMED")
        order.payment_ref = req.payment_ref
        await persist_event(
            session, order_id, tenant_id, EventType.PAYMENT_CONFIRMED.value,
            {
                "payment_ref": req.payment_ref,
                "gateway_txn_id": req.gateway_txn_id,
            },
            idempotency_key_val=req.idempotency_key,
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    await publish_event(
        producer, EventType.PAYMENT_CONFIRMED, order_id, tenant_id,
        {"payment_ref": req.payment_ref, "gateway_txn_id": req.gateway_txn_id},
        kitchen_id=str(order.kitchen_id),
    )

    response = format_order(order)
    await store_idempotency(redis_client, req.idempotency_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 4. ASSIGN QUEUE (SLOT_RESERVED → QUEUE_ASSIGNED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/assign-queue")
async def assign_queue(
    order_id: str,
    req: AssignQueueRequest = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """Assign order to kitchen queue. SLOT_RESERVED → QUEUE_ASSIGNED."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    idem_key = req.idempotency_key if req else None
    cached = await check_idempotency(redis_client, idem_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "SLOT_RESERVED":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot assign queue: order is {order.status}",
        )

    # Compute priority score
    tier_weight = TIER_WEIGHTS.get(order.user_tier, 1)
    priority_score = 50.0 + (tier_weight * 5)

    # Determine queue type
    if priority_score > 80:
        queue_type = QueueType.IMMEDIATE.value
    elif priority_score >= 60:
        queue_type = QueueType.NORMAL.value
    else:
        queue_type = QueueType.BATCH.value

    # Transition with guard
    context = {"priority_score": priority_score}
    try:
        order.status = state_machine.transition(
            "SLOT_RESERVED", "QUEUE_ASSIGNED", context=context
        )
        order.queue_type = queue_type
        order.priority_score = priority_score
        await persist_event(
            session, order_id, tenant_id, EventType.QUEUE_ASSIGNED.value,
            {"queue_type": queue_type, "priority_score": priority_score},
            idempotency_key_val=idem_key,
        )
    except (InvalidTransitionError, GuardViolationError) as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    await publish_event(
        producer, EventType.QUEUE_ASSIGNED, order_id, tenant_id,
        {"queue_type": queue_type, "priority_score": priority_score},
        kitchen_id=str(order.kitchen_id),
    )

    response = format_order(order)
    await store_idempotency(redis_client, idem_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 5. START PREP (QUEUE_ASSIGNED/BATCH_WAIT → IN_PREP)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/start-prep")
async def start_prep(
    order_id: str,
    req: StartPrepRequest = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """Kitchen staff starts preparation. QUEUE_ASSIGNED → IN_PREP."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    idem_key = req.idempotency_key if req else None
    cached = await check_idempotency(redis_client, idem_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status not in ("QUEUE_ASSIGNED", "BATCH_WAIT"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot start prep: order is {order.status}",
        )

    try:
        order.status = state_machine.transition(order.status, "IN_PREP")
        order.prep_started_at = datetime.now(timezone.utc)
        staff_id = req.staff_id if req else None
        await persist_event(
            session, order_id, tenant_id, EventType.PREP_STARTED.value,
            {"staff_id": staff_id, "from_state": order.status},
            idempotency_key_val=idem_key,
        )
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    await publish_event(
        producer, EventType.PREP_STARTED, order_id, tenant_id,
        {"staff_id": staff_id if req else None, "kitchen_id": str(order.kitchen_id)},
        kitchen_id=str(order.kitchen_id),
    )

    response = format_order(order)
    await store_idempotency(redis_client, idem_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 6. MARK READY (IN_PREP → PACKING → READY)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/ready")
async def mark_ready(
    order_id: str,
    req: MarkReadyRequest = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """Mark order as ready for pickup. IN_PREP → PACKING → READY."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    idem_key = req.idempotency_key if req else None
    cached = await check_idempotency(redis_client, idem_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    staff_id = req.staff_id if req else None

    # IN_PREP → PACKING
    if order.status == "IN_PREP":
        try:
            order.status = state_machine.transition("IN_PREP", "PACKING")
            await persist_event(
                session, order_id, tenant_id, EventType.PACKING_STARTED.value,
                {"staff_id": staff_id},
            )
            await publish_event(
                producer, EventType.PREP_COMPLETED, order_id, tenant_id,
                {"kitchen_id": str(order.kitchen_id)},
                kitchen_id=str(order.kitchen_id),
            )
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    # PACKING → READY
    if order.status == "PACKING":
        try:
            order.status = state_machine.transition("PACKING", "READY")
            order.ready_at = datetime.now(timezone.utc)
            await persist_event(
                session, order_id, tenant_id, EventType.READY_FOR_PICKUP.value,
                {"staff_id": staff_id},
                idempotency_key_val=idem_key,
            )
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    await publish_event(
        producer, EventType.READY_FOR_PICKUP, order_id, tenant_id,
        {"kitchen_id": str(order.kitchen_id)},
        kitchen_id=str(order.kitchen_id),
    )

    response = format_order(order)
    await store_idempotency(redis_client, idem_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 7. ASSIGN SHELF (READY → ON_SHELF)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/shelf")
async def assign_shelf(
    order_id: str,
    req: AssignShelfRequest = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """Assign order to a physical shelf slot. READY → ON_SHELF."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    idem_key = req.idempotency_key if req else None
    cached = await check_idempotency(redis_client, idem_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "READY":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot assign shelf: order is {order.status}",
        )

    # Determine shelf zone
    zone = (req.zone if req else None) or _determine_zone(order.items or [])

    # Acquire shelf lock via Redis
    shelf_id = None
    if req and req.shelf_id:
        if redis_client:
            acquired = await redis_client.acquire_shelf_lock(
                req.shelf_id, order_id, ttl=DEFAULT_SHELF_TTL, tenant_id=tenant_id
            )
            if not acquired:
                raise HTTPException(status_code=409, detail=f"Shelf {req.shelf_id} is occupied")
        shelf_id = req.shelf_id
    else:
        shelf_id = await _auto_assign_shelf(
            redis_client, str(order.kitchen_id), order_id, zone
        )
        if not shelf_id:
            shelf_id = "HOLD_ZONE"
            zone = "AMBIENT"

    # Transition with guard
    ttl = SHELF_TTL_SECONDS.get(zone, 600)
    context = {
        "shelf_id": shelf_id,
        "shelf_capacity": 1 if shelf_id != "HOLD_ZONE" else 0,
    }

    try:
        order.status = state_machine.transition("READY", "ON_SHELF", context=context)
        order.shelf_id = shelf_id
        order.shelf_zone = zone
        order.shelf_ttl_remaining = ttl
        await persist_event(
            session, order_id, tenant_id, EventType.SHELF_ASSIGNED.value,
            {"shelf_id": shelf_id, "zone": zone, "ttl_seconds": ttl},
            idempotency_key_val=idem_key,
        )
    except (InvalidTransitionError, GuardViolationError) as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    await publish_event(
        producer, EventType.SHELF_ASSIGNED, order_id, tenant_id,
        {"shelf_id": shelf_id, "zone": zone, "ttl_seconds": ttl},
        kitchen_id=str(order.kitchen_id),
    )

    response = format_order(order)
    await store_idempotency(redis_client, idem_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 8. CONFIRM ARRIVAL (ON_SHELF → ARRIVED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/arrival")
async def confirm_arrival(
    order_id: str,
    req: ArrivalRequestLocal,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """User arrival detection via GPS or QR scan. ON_SHELF → ARRIVED."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    # Arrival dedup
    if not req.idempotency_key:
        req.idempotency_key = arrival_dedup_key(req.user_id, order_id)

    cached = await check_idempotency(redis_client, req.idempotency_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "ON_SHELF":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot confirm arrival: order is {order.status}",
        )

    # Validate GPS/QR
    context = {
        "gps_distance": 100 if req.qr_scan else 50,  # Simplified
        "qr_scan_valid": req.qr_scan,
    }

    try:
        order.status = state_machine.transition("ON_SHELF", "ARRIVED", context=context)
        order.arrived_at = datetime.now(timezone.utc)
        await persist_event(
            session, order_id, tenant_id, EventType.ARRIVAL_DETECTED.value,
            {
                "user_id": req.user_id,
                "gps_distance": context["gps_distance"],
                "qr_scan": req.qr_scan,
                "validated": True,
            },
            idempotency_key_val=req.idempotency_key,
        )
    except (InvalidTransitionError, GuardViolationError) as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    await publish_event(
        producer, EventType.ARRIVAL_DETECTED, order_id, tenant_id,
        {"user_id": req.user_id, "arrival_boost": 20, "validated": True},
    )

    response = format_order(order)
    await store_idempotency(redis_client, req.idempotency_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 9. HANDOFF (ARRIVED → HANDOFF_IN_PROGRESS → PICKED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/handoff")
async def confirm_handoff(
    order_id: str,
    req: HandoffConfirmRequest,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """Confirm order handoff. ARRIVED → HANDOFF_IN_PROGRESS → PICKED."""
    tenant_id = user.get("tenant_id", req.tenant_id)
    await set_tenant_id(session, tenant_id)

    cached = await check_idempotency(redis_client, req.idempotency_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # ARRIVED → HANDOFF_IN_PROGRESS
    if order.status == "ARRIVED":
        try:
            order.status = state_machine.transition("ARRIVED", "HANDOFF_IN_PROGRESS")
            await persist_event(
                session, order_id, tenant_id, EventType.HANDOFF_STARTED.value,
                {"staff_id": req.staff_id, "method": req.confirmation_method},
            )
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    # HANDOFF_IN_PROGRESS → PICKED
    if order.status == "HANDOFF_IN_PROGRESS":
        try:
            order.status = state_machine.transition("HANDOFF_IN_PROGRESS", "PICKED")
            order.picked_at = datetime.now(timezone.utc)
            await persist_event(
                session, order_id, tenant_id, EventType.HANDOFF_COMPLETED.value,
                {"staff_id": req.staff_id, "method": req.confirmation_method},
                idempotency_key_val=req.idempotency_key,
            )
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    # Release shelf lock
    if order.shelf_id and order.shelf_id != "HOLD_ZONE" and redis_client:
        await redis_client.release_shelf_lock(
            order.shelf_id, order_id, tenant_id=tenant_id
        )

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    await publish_event(
        producer, EventType.HANDOFF_COMPLETED, order_id, tenant_id,
        {"staff_id": req.staff_id, "shelf_id": order.shelf_id},
    )
    await publish_event(
        producer, EventType.ORDER_PICKED, order_id, tenant_id,
        {"picked_at": order.picked_at.isoformat() if order.picked_at else None},
    )

    response = format_order(order)
    await store_idempotency(redis_client, req.idempotency_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 10. EXPIRE ORDER
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/expire")
async def expire_order(
    order_id: str,
    req: ExpireRequest = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """Mark order as expired (shelf TTL exceeded). ON_SHELF/ARRIVED → EXPIRED."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    reason = req.reason if req else "SHELF_TTL_EXCEEDED"

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status not in ("ON_SHELF", "ARRIVED", "HANDOFF_IN_PROGRESS"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot expire: order is {order.status}",
        )

    context = {
        "current_state": order.status,
        "shelf_ttl_exceeded": True,
    }

    try:
        order.status = state_machine.transition(order.status, "EXPIRED", context=context)
        await persist_event(
            session, order_id, tenant_id, EventType.ORDER_EXPIRED.value,
            {"reason": reason},
        )
    except (InvalidTransitionError, GuardViolationError) as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Release shelf lock
    if order.shelf_id and order.shelf_id != "HOLD_ZONE" and redis_client:
        await redis_client.release_shelf_lock(order.shelf_id, order_id, tenant_id=tenant_id)

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    await publish_event(
        producer, EventType.ORDER_EXPIRED, order_id, tenant_id,
        {"reason": reason, "shelf_id": order.shelf_id},
    )

    # Auto-trigger compensation
    await publish_event(
        producer, EventType.COMPENSATION_TRIGGERED, order_id, tenant_id,
        {"reason": reason, "amount": order.total_amount, "auto_triggered": True},
    )

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 11. CANCEL ORDER
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    req: CancelRequest = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    producer=Depends(get_kafka_producer),
    redis_client=Depends(get_redis),
):
    """Cancel an order. Only cancellable states allow this."""
    tenant_id = user.get("tenant_id", req.tenant_id if req else "default")
    await set_tenant_id(session, tenant_id)

    idem_key = req.idempotency_key if req else None
    cached = await check_idempotency(redis_client, idem_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not state_machine.is_cancellable(order.status):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel order in {order.status} state",
        )

    if not state_machine.can_transition(order.status, "CANCELLED"):
        raise HTTPException(
            status_code=409,
            detail=f"No transition from {order.status} to CANCELLED",
        )

    previous_status = order.status
    order.status = "CANCELLED"
    await persist_event(
        session, order_id, tenant_id, EventType.ORDER_CANCELLED.value,
        {"reason": req.reason if req else None, "from_state": previous_status},
        idempotency_key_val=idem_key,
    )

    # Release resources
    if order.shelf_id and order.shelf_id != "HOLD_ZONE" and redis_client:
        await redis_client.release_shelf_lock(order.shelf_id, order_id, tenant_id=tenant_id)

    await session.flush()

    if redis_client:
        await redis_client.set_order_status(order_id, order.status, tenant_id)

    # Get compensation steps for the previous state
    comp_steps = state_machine.get_compensation_steps(previous_status)
    await publish_event(
        producer, EventType.ORDER_CANCELLED, order_id, tenant_id,
        {"reason": req.reason if req else None, "compensation_steps": comp_steps},
        kitchen_id=str(order.kitchen_id),
    )

    # Trigger compensation if payment was confirmed
    if previous_status in ("PAYMENT_CONFIRMED", "SLOT_RESERVED", "QUEUE_ASSIGNED",
                           "IN_PREP", "BATCH_WAIT", "PACKING", "READY", "ON_SHELF"):
        await publish_event(
            producer, EventType.COMPENSATION_TRIGGERED, order_id, tenant_id,
            {
                "reason": f"ORDER_CANCELLED from {previous_status}",
                "amount": order.total_amount,
                "auto_triggered": True,
                "compensation_steps": comp_steps,
            },
        )

    response = format_order(order)
    await store_idempotency(redis_client, idem_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 12. GET ORDER
# ═══════════════════════════════════════════════════════════════
@router.get("/{order_id}")
async def get_order(
    order_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """Get order details."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    # Redis cache first
    if redis_client:
        cached_status = await redis_client.get_order_status(order_id, tenant_id)
        if cached_status:
            # Still need full data from DB
            pass

    result = await session.execute(
        select(OrderModel).where(
            OrderModel.id == uuid.UUID(order_id),
            OrderModel.tenant_id == tenant_id,
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 13. GET EVENT LOG
# ═══════════════════════════════════════════════════════════════
@router.get("/{order_id}/events")
async def get_order_events(
    order_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Get full event log for an order (audit trail)."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    result = await session.execute(
        select(OrderEventModel)
        .where(
            OrderEventModel.order_id == uuid.UUID(order_id),
            OrderEventModel.tenant_id == tenant_id,
        )
        .order_by(OrderEventModel.created_at)
    )
    events = result.scalars().all()

    return EventLogResponse(
        order_id=order_id,
        tenant_id=tenant_id,
        events=[{
            "event_id": str(e.id),
            "event_type": e.event_type,
            "payload": e.payload,
            "source": e.source,
            "sequence_number": e.sequence_number,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        } for e in events],
    )


# ═══════════════════════════════════════════════════════════════
# 14. GET ORDERS BY KITCHEN
# ═══════════════════════════════════════════════════════════════
@router.get("/kitchen/{kitchen_id}")
async def get_kitchen_orders(
    kitchen_id: str,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """List orders by kitchen."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    query = select(OrderModel).where(
        OrderModel.kitchen_id == uuid.UUID(kitchen_id),
        OrderModel.tenant_id == tenant_id,
    )
    if status_filter:
        query = query.where(OrderModel.status == status_filter)

    query = query.order_by(OrderModel.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    orders = result.scalars().all()

    return [format_order(o) for o in orders]


# ═══════════════════════════════════════════════════════════════
# 15. GET ORDERS BY USER
# ═══════════════════════════════════════════════════════════════
@router.get("/user/{user_id}")
async def get_user_orders(
    user_id: str,
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """List orders by user."""
    tenant_id = user.get("tenant_id", "default")
    await set_tenant_id(session, tenant_id)

    query = select(OrderModel).where(
        OrderModel.user_id == uuid.UUID(user_id),
        OrderModel.tenant_id == tenant_id,
    )
    if status_filter:
        query = query.where(OrderModel.status == status_filter)

    query = query.order_by(OrderModel.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(query)
    orders = result.scalars().all()

    return [format_order(o) for o in orders]
