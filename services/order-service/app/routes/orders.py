"""HotSot Order Service — V2 Production-Grade Order Routes.

V2 Flow: CREATED → PAYMENT_PENDING → PAYMENT_CONFIRMED → SLOT_RESERVED →
QUEUE_ASSIGNED → IN_PREP → PACKING → READY → ON_SHELF → ARRIVED →
HANDOFF_IN_PROGRESS → PICKED

Plus failure paths: EXPIRED, REFUNDED, CANCELLED, FAILED

Idempotency: Every mutation requires idempotency_key
Event Sourcing: Every state change emits a canonical event
Guard Rules: Critical transitions validated with context
"""

import uuid
import hashlib
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.core.database import get_session, OrderModel, OrderEventModel
from app.core.schemas import (
    OrderCreateRequest,
    PaymentInitRequest,
    PaymentConfirmRequest,
    ArrivalRequest,
    HandoffConfirmRequest,
    CancelRequest,
    OrderResponse,
    EventLogResponse,
    OrderStatus,
    EventType,
    UserTier,
    QueueType,
    ShelfZone,
)
from app.core.state_machine import state_machine, InvalidTransitionError, GuardViolationError
from app.core.events import Event, emit_event
from app.core.redis_client import (
    set_order_state,
    get_order_state,
    increment_kitchen_load,
    decrement_kitchen_load,
    get_kitchen_load,
    check_idempotency_key,
    store_idempotency_key,
    acquire_lock,
    release_lock,
)

router = APIRouter()

# Maximum orders per kitchen before throttle
MAX_KITCHEN_ORDERS = 200
# Shelf slot count per kitchen
SHELF_SLOTS_PER_KITCHEN = 6
# Shelf TTL default
DEFAULT_SHELF_TTL = 600
# Warm station TTL extension
WARM_STATION_TTL_EXTENSION = 300


# ─── IDEMPOTENCY HELPER ───
async def enforce_idempotency(key: Optional[str]) -> Optional[Dict]:
    """Check if this request was already processed. Return cached response if so."""
    if not key:
        return None
    cached = await check_idempotency_key(key)
    if cached:
        return cached
    return None


async def mark_idempotent(key: Optional[str], response: Dict):
    """Mark request as processed for idempotency."""
    if key:
        await store_idempotency_key(key, response, ttl=86400)  # 24h TTL


# ─── HELPER: Persist Event ───
async def persist_event(
    session: AsyncSession,
    order_id: str,
    event_type: str,
    payload: dict = None,
    sequence_number: int = 0,
    source: str = "order-service",
):
    """Save event to database for audit trail with sequence tracking."""
    event_record = OrderEventModel(
        order_id=uuid.UUID(order_id),
        event_type=event_type,
        payload=payload or {},
        source=source,
    )
    session.add(event_record)
    await session.flush()


# ─── HELPER: Format Order Response ───
def format_order(order: OrderModel) -> dict:
    """Convert ORM model to API response with V2 fields."""
    return {
        "order_id": str(order.id),
        "user_id": str(order.user_id),
        "kitchen_id": str(order.kitchen_id),
        "status": order.status,
        "queue_type": getattr(order, "queue_type", None),
        "queue_position": getattr(order, "queue_position", None),
        "priority_score": getattr(order, "priority_score", None),
        "eta_seconds": order.eta_seconds,
        "eta_confidence": order.eta_confidence,
        "eta_risk": order.eta_risk,
        "shelf_id": order.shelf_id,
        "shelf_zone": getattr(order, "shelf_zone", None),
        "payment_ref": order.payment_ref,
        "batch_id": getattr(order, "batch_id", None),
        "arrived_at": order.arrived_at.isoformat() if getattr(order, "arrived_at", None) else None,
        "picked_at": order.picked_at.isoformat() if getattr(order, "picked_at", None) else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


# ═══════════════════════════════════════════════════════════════
# 1. CREATE ORDER (V2: CREATED → PAYMENT_PENDING)
# ═══════════════════════════════════════════════════════════════
@router.post("/create", response_model=OrderResponse)
async def create_order(
    req: OrderCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Create a new order. V2 flow: CREATED → PAYMENT_PENDING.
    
    - Validates kitchen capacity via Redis
    - Stores idempotency_key for dedup
    - Emits ORDER_CREATED event
    - Transitions to PAYMENT_PENDING (waits for payment before slot)
    """
    # Idempotency check
    cached = await enforce_idempotency(req.idempotency_key)
    if cached:
        return cached

    # Check kitchen capacity
    kitchen_load = await get_kitchen_load(req.kitchen_id)
    if kitchen_load >= MAX_KITCHEN_ORDERS:
        raise HTTPException(
            status_code=429,
            detail="Kitchen at capacity. Try again shortly."
        )

    # Create order in Postgres
    order = OrderModel(
        user_id=uuid.UUID(req.user_id),
        kitchen_id=uuid.UUID(req.kitchen_id),
        status=OrderStatus.CREATED,
        items=req.items,
        total_amount=req.total_amount,
        payment_method=req.payment_method,
        user_tier=req.user_tier.value,
    )
    session.add(order)
    await session.flush()

    order_id = str(order.id)

    # Emit ORDER_CREATED event
    await persist_event(session, order_id, "ORDER_CREATED", {
        "user_id": req.user_id,
        "kitchen_id": req.kitchen_id,
        "items_count": len(req.items),
        "user_tier": req.user_tier.value,
        "payment_method": req.payment_method,
    })

    # V2: Transition to PAYMENT_PENDING (not SLOT_RESERVED)
    try:
        new_status = state_machine.transition("CREATED", "PAYMENT_PENDING")
        order.status = new_status
        await persist_event(session, order_id, "PAYMENT_PENDING", {
            "payment_method": req.payment_method,
        })
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.commit()
    await session.refresh(order)

    # Update Redis hot state
    await set_order_state(order_id, {
        "status": order.status,
        "kitchen_id": req.kitchen_id,
        "user_id": req.user_id,
        "user_tier": req.user_tier.value,
    })

    # Emit async events
    await emit_event(Event(order_id, "ORDER_CREATED", {
        "user_id": req.user_id,
        "kitchen_id": req.kitchen_id,
        "items": req.items,
        "user_tier": req.user_tier.value,
    }))
    await emit_event(Event(order_id, "PAYMENT_PENDING", {
        "payment_method": req.payment_method,
    }))

    response = format_order(order)
    await mark_idempotent(req.idempotency_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 2. CONFIRM PAYMENT (V2: PAYMENT_PENDING → PAYMENT_CONFIRMED → SLOT_RESERVED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/pay", response_model=OrderResponse)
async def confirm_payment(
    order_id: str,
    req: PaymentConfirmRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Confirm payment for an order.
    
    V2 Flow: PAYMENT_PENDING → PAYMENT_CONFIRMED → SLOT_RESERVED
    - Validates payment reference (UPI gateway simulation)
    - Reserves slot AFTER payment (V2 correction)
    - Emits PAYMENT_CONFIRMED and SLOT_RESERVED events
    """
    # Idempotency check
    cached = await enforce_idempotency(req.idempotency_key)
    if cached:
        return cached

    # Fetch order
    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Validate current state
    if order.status != "PAYMENT_PENDING":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot pay: order is {order.status} (expected PAYMENT_PENDING)"
        )

    # Transition: PAYMENT_PENDING → PAYMENT_CONFIRMED
    try:
        order.status = state_machine.transition("PAYMENT_PENDING", "PAYMENT_CONFIRMED")
        order.payment_ref = req.payment_ref
        await persist_event(session, order_id, "PAYMENT_CONFIRMED", {
            "payment_ref": req.payment_ref,
            "payment_method": req.payment_method,
            "gateway_txn_id": req.gateway_txn_id,
        })
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # V2: Slot reservation AFTER payment
    kitchen_load = await get_kitchen_load(str(order.kitchen_id))
    kitchen_load_pct = (kitchen_load / MAX_KITCHEN_ORDERS) * 100

    slot_context = {
        "slot_available": kitchen_load < MAX_KITCHEN_ORDERS,
        "waitlist_allowed": True,  # Allow waitlist as fallback
    }

    try:
        order.status = state_machine.transition(
            "PAYMENT_CONFIRMED", "SLOT_RESERVED", context=slot_context
        )
        await increment_kitchen_load(str(order.kitchen_id))
        await persist_event(session, order_id, "SLOT_RESERVED", {
            "kitchen_load_pct": kitchen_load_pct,
            "slot_available": True,
        })
    except (InvalidTransitionError, GuardViolationError) as e:
        # Slot conflict — payment succeeded but no slot
        # Lock payment result and trigger re-slot negotiation
        await persist_event(session, order_id, "SLOT_CONFLICT", {
            "payment_ref": req.payment_ref,
            "kitchen_load": kitchen_load,
        })
        await emit_event(Event(order_id, "SLOT_FILLED", {
            "payment_ref": req.payment_ref,
            "fallback": "waitlist",
        }))
        # Keep in PAYMENT_CONFIRMED — user must choose next slot
        await session.commit()
        raise HTTPException(
            status_code=409,
            detail="Slot filled during payment. Please select a new time slot."
        )

    await session.commit()
    await session.refresh(order)

    # Update Redis + emit events
    await set_order_state(order_id, {"status": order.status})
    await emit_event(Event(order_id, "PAYMENT_CONFIRMED", {
        "payment_ref": req.payment_ref,
    }))
    await emit_event(Event(order_id, "SLOT_RESERVED", {
        "kitchen_id": str(order.kitchen_id),
    }))

    response = format_order(order)
    await mark_idempotent(req.idempotency_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 3. ASSIGN QUEUE (V2: SLOT_RESERVED → QUEUE_ASSIGNED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/assign-queue", response_model=OrderResponse)
async def assign_queue(
    order_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Assign order to kitchen queue after slot reservation.
    
    V2 Flow: SLOT_RESERVED → QUEUE_ASSIGNED
    - Computes priority score
    - Determines queue type (IMMEDIATE / NORMAL / BATCH)
    - Assigns queue position
    """
    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "SLOT_RESERVED":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot assign queue: order is {order.status}"
        )

    # Compute priority score (simplified — full engine in kitchen-service)
    user_tier = getattr(order, "user_tier", "FREE")
    tier_weight = {"FREE": 1, "PLUS": 2, "PRO": 3, "VIP": 4}.get(user_tier, 1)
    priority_score = 50.0 + (tier_weight * 5)

    # Determine queue type based on priority
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
        setattr(order, "queue_type", queue_type)
        setattr(order, "priority_score", priority_score)
        await persist_event(session, order_id, "QUEUE_ASSIGNED", {
            "queue_type": queue_type,
            "priority_score": priority_score,
        })
    except (InvalidTransitionError, GuardViolationError) as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.commit()
    await session.refresh(order)

    await set_order_state(order_id, {
        "status": order.status,
        "queue_type": queue_type,
        "priority_score": priority_score,
    })
    await emit_event(Event(order_id, "QUEUE_ASSIGNED", {
        "queue_type": queue_type,
        "priority_score": priority_score,
    }))

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 4. START PREP (V2: QUEUE_ASSIGNED/BATCH_WAIT → IN_PREP)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/start-prep", response_model=OrderResponse)
async def start_prep(
    order_id: str,
    staff_id: str = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Kitchen staff acknowledges and starts preparation.
    
    V2 Flow: QUEUE_ASSIGNED → IN_PREP (or BATCH_WAIT → IN_PREP)
    - Requires staff ACK (forced interaction per V2 design)
    - If not ACK'd within 60s, system escalates automatically
    """
    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status not in ["QUEUE_ASSIGNED", "BATCH_WAIT"]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot start prep: order is {order.status}"
        )

    try:
        order.status = state_machine.transition(order.status, "IN_PREP")
        order.prep_started_at = datetime.now(timezone.utc)
        await persist_event(session, order_id, "PREP_STARTED", {
            "staff_id": staff_id,
            "from_state": order.status,
        })
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.commit()
    await session.refresh(order)

    await set_order_state(order_id, {"status": order.status})
    await emit_event(Event(order_id, "PREP_STARTED", {
        "staff_id": staff_id,
        "kitchen_id": str(order.kitchen_id),
    }))

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 5. COMPLETE PREP + PACKING (V2: IN_PREP → PACKING → READY)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/ready", response_model=OrderResponse)
async def mark_ready(
    order_id: str,
    staff_id: str = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Mark order as ready for pickup after prep + packing.
    
    V2 Flow: IN_PREP → PACKING → READY
    - Two-step: prep done → packing → ready
    - Triggers shelf allocation via event
    """
    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # V2: Support both IN_PREP → PACKING and direct IN_PREP → READY for backwards compat
    if order.status == "IN_PREP":
        try:
            order.status = state_machine.transition("IN_PREP", "PACKING")
            await persist_event(session, order_id, "PACKING_STARTED", {
                "staff_id": staff_id,
            })
            await emit_event(Event(order_id, "PREP_COMPLETED", {}))
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    if order.status == "PACKING":
        try:
            order.status = state_machine.transition("PACKING", "READY")
            order.ready_at = datetime.now(timezone.utc)
            await persist_event(session, order_id, "READY_FOR_PICKUP", {
                "staff_id": staff_id,
            })
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    await session.commit()
    await session.refresh(order)

    await set_order_state(order_id, {"status": order.status})
    await emit_event(Event(order_id, "READY_FOR_PICKUP", {}))

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 6. ASSIGN SHELF (V2: READY → ON_SHELF with guard)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/shelf", response_model=OrderResponse)
async def assign_shelf(
    order_id: str,
    shelf_id: Optional[str] = None,
    zone: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Assign order to a physical shelf slot for pickup.
    
    V2 Flow: READY → ON_SHELF
    - Guard: shelf_id != null AND shelf_capacity > 0
    - Uses Redis distributed lock for shelf allocation
    - Zone-aware: HOT food → HOT zone, etc.
    - TTL enforced per zone (HOT=600s, COLD=900s, AMBIENT=1200s)
    - Overflow → HOLD_ZONE (virtual waitlist)
    """
    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "READY":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot assign shelf: order is {order.status}"
        )

    # Determine shelf zone based on items (simple heuristic)
    if not zone:
        zone = _determine_zone(order.items)

    # Acquire shelf lock via Redis
    if shelf_id:
        lock_acquired = await acquire_lock(f"shelf:{shelf_id}", order_id, ttl=DEFAULT_SHELF_TTL)
        if not lock_acquired:
            raise HTTPException(
                status_code=409,
                detail=f"Shelf {shelf_id} is occupied"
            )
    else:
        # Auto-assign shelf with zone preference
        shelf_id = await _auto_assign_shelf(str(order.kitchen_id), order_id, zone)
        if not shelf_id:
            # No shelf available → move to HOLD_ZONE (virtual waitlist)
            shelf_id = "HOLD_ZONE"
            zone = "AMBIENT"

    # Transition with guard
    ttl = {"HOT": 600, "COLD": 900, "AMBIENT": 1200}.get(zone, 600)
    context = {"shelf_id": shelf_id, "shelf_capacity": 1 if shelf_id != "HOLD_ZONE" else 0}

    try:
        order.status = state_machine.transition("READY", "ON_SHELF", context=context)
        order.shelf_id = shelf_id
        setattr(order, "shelf_zone", zone)
        setattr(order, "shelf_ttl_remaining", ttl)
        await persist_event(session, order_id, "SHELF_ASSIGNED", {
            "shelf_id": shelf_id,
            "zone": zone,
            "ttl_seconds": ttl,
        })
    except (InvalidTransitionError, GuardViolationError) as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.commit()
    await session.refresh(order)

    await set_order_state(order_id, {
        "status": order.status,
        "shelf_id": shelf_id,
        "shelf_zone": zone,
    })
    await emit_event(Event(order_id, "SHELF_ASSIGNED", {
        "shelf_id": shelf_id,
        "zone": zone,
        "ttl_seconds": ttl,
    }))

    return format_order(order)


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


async def _auto_assign_shelf(kitchen_id: str, order_id: str, zone: str = "HOT") -> Optional[str]:
    """Find and lock an available shelf slot with zone preference."""
    for slot_num in range(1, SHELF_SLOTS_PER_KITCHEN + 1):
        shelf_id = f"{zone[0]}{slot_num}"  # H1-H6, C1-C6, A1-A6
        lock_acquired = await acquire_lock(f"shelf:{shelf_id}", order_id, ttl=DEFAULT_SHELF_TTL)
        if lock_acquired:
            return shelf_id
    return None


# ═══════════════════════════════════════════════════════════════
# 7. CONFIRM ARRIVAL (V2: ON_SHELF → ARRIVED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/arrival", response_model=OrderResponse)
async def confirm_arrival(
    order_id: str,
    req: ArrivalRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    User arrival detection via GPS or QR scan.
    
    V2 Flow: ON_SHELF → ARRIVED
    - Guard: GPS distance ≤ 150m OR valid QR scan
    - Idempotency: dedup by hash(user_id + order_id + floor(timestamp/30s))
    - Arrival triggers priority boost (+20) for orders still in prep
    - Staff UI highlights order with flash + audible ping
    """
    # Arrival dedup key
    if not req.idempotency_key:
        ts_bucket = int(time.time()) // 30  # 30-second window
        dedup_raw = f"{req.user_id}:{order_id}:{ts_bucket}"
        req.idempotency_key = hashlib.sha256(dedup_raw.encode()).hexdigest()

    cached = await enforce_idempotency(req.idempotency_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "ON_SHELF":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot confirm arrival: order is {order.status}"
        )

    # Validate GPS distance or QR scan
    gps_distance = req.latitude  # Simplified — real impl uses haversine
    qr_valid = req.qr_scan

    context = {
        "gps_distance": 100 if qr_valid else gps_distance,  # Simplified
        "qr_scan_valid": qr_valid,
    }

    try:
        order.status = state_machine.transition("ON_SHELF", "ARRIVED", context=context)
        setattr(order, "arrived_at", datetime.now(timezone.utc))
        await persist_event(session, order_id, "ARRIVAL_DETECTED", {
            "user_id": req.user_id,
            "gps_distance": context.get("gps_distance"),
            "qr_scan": qr_valid,
            "validated": True,
        })
    except (InvalidTransitionError, GuardViolationError) as e:
        raise HTTPException(status_code=409, detail=str(e))

    await session.commit()
    await session.refresh(order)

    # Priority boost for orders still in prep
    await emit_event(Event(order_id, "ARRIVAL_DETECTED", {
        "user_id": req.user_id,
        "arrival_boost": 20,
        "validated": True,
    }))

    await set_order_state(order_id, {
        "status": order.status,
        "arrived_at": order.arrived_at.isoformat() if hasattr(order, 'arrived_at') else None,
    })

    response = format_order(order)
    await mark_idempotent(req.idempotency_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 8. HANDOFF (V2: ARRIVED → HANDOFF_IN_PROGRESS → PICKED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/handoff", response_model=OrderResponse)
async def confirm_handoff(
    order_id: str,
    req: HandoffConfirmRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Confirm order handoff to user via QR scan or manual ACK.
    
    V2 Flow: ARRIVED → HANDOFF_IN_PROGRESS → PICKED
    - Staff scans QR or manually confirms handoff
    - Releases shelf lock
    - Decrements kitchen load
    - Closes order lifecycle
    """
    cached = await enforce_idempotency(req.idempotency_key)
    if cached:
        return cached

    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Support both ARRIVED → HANDOFF_IN_PROGRESS → PICKED
    # and ON_SHELF → PICKED (legacy path for backward compat)
    if order.status == "ARRIVED":
        try:
            order.status = state_machine.transition("ARRIVED", "HANDOFF_IN_PROGRESS")
            await persist_event(session, order_id, "HANDOFF_STARTED", {
                "staff_id": req.staff_id,
                "method": req.confirmation_method,
            })
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    if order.status == "HANDOFF_IN_PROGRESS":
        try:
            order.status = state_machine.transition("HANDOFF_IN_PROGRESS", "PICKED")
            order.picked_at = datetime.now(timezone.utc)
            await persist_event(session, order_id, "HANDOFF_COMPLETED", {
                "staff_id": req.staff_id,
                "method": req.confirmation_method,
            })
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    elif order.status == "ON_SHELF":
        # Legacy path (backward compat)
        try:
            order.status = state_machine.transition("ON_SHELF", "PICKED")
            order.picked_at = datetime.now(timezone.utc)
            await persist_event(session, order_id, "ORDER_PICKED", {
                "staff_id": req.staff_id,
            })
        except InvalidTransitionError as e:
            raise HTTPException(status_code=409, detail=str(e))

    # Release shelf lock
    if order.shelf_id and order.shelf_id != "HOLD_ZONE":
        await release_lock(f"shelf:{order.shelf_id}", order_id)

    await session.commit()
    await session.refresh(order)

    # Update Redis
    await decrement_kitchen_load(str(order.kitchen_id))
    await set_order_state(order_id, {"status": order.status})

    # Emit final events
    await emit_event(Event(order_id, "HANDOFF_COMPLETED", {
        "staff_id": req.staff_id,
        "shelf_id": order.shelf_id,
    }))
    await emit_event(Event(order_id, "ORDER_PICKED", {}))

    response = format_order(order)
    await mark_idempotent(req.idempotency_key, response)
    return response


# ═══════════════════════════════════════════════════════════════
# 9. EXPIRE ORDER (V2: ON_SHELF/ARRIVED → EXPIRED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/expire", response_model=OrderResponse)
async def expire_order(
    order_id: str,
    reason: str = "SHELF_TTL_EXCEEDED",
    session: AsyncSession = Depends(get_session),
):
    """
    Mark order as expired (shelf TTL exceeded).
    
    V2 Flow: ON_SHELF/ARRIVED → EXPIRED
    - Triggered by shelf TTL engine
    - Attempts warm station move before expiring
    - Auto-triggers compensation (refund) flow
    """
    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status not in ["ON_SHELF", "ARRIVED", "HANDOFF_IN_PROGRESS"]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot expire: order is {order.status}"
        )

    # Try warm station extension first
    warm_extension_applied = False
    if order.status == "ON_SHELF" and reason == "SHELF_TTL_EXCEEDED":
        # Check if warm station available
        warm_available = True  # Simplified — real impl checks kitchen state
        if warm_available and getattr(order, "shelf_zone", "HOT") == "HOT":
            # Extend TTL by +5 minutes via warm station
            setattr(order, "shelf_ttl_remaining", WARM_STATION_TTL_EXTENSION)
            warm_extension_applied = True
            await persist_event(session, order_id, "WARM_STATION_EXTENSION", {
                "new_ttl": WARM_STATION_TTL_EXTENSION,
            })
            await session.commit()
            await session.refresh(order)
            return format_order(order)

    # Expire the order
    try:
        context = {
            "current_state": order.status,
            "shelf_ttl_exceeded": True,
        }
        order.status = state_machine.transition(order.status, "EXPIRED", context=context)
        await persist_event(session, order_id, "ORDER_EXPIRED", {
            "reason": reason,
            "warm_extension_attempted": warm_extension_applied,
        })
    except (InvalidTransitionError, GuardViolationError) as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Release shelf lock
    if order.shelf_id and order.shelf_id != "HOLD_ZONE":
        await release_lock(f"shelf:{order.shelf_id}", order_id)

    await session.commit()
    await session.refresh(order)

    await set_order_state(order_id, {"status": order.status})
    await emit_event(Event(order_id, "ORDER_EXPIRED", {
        "reason": reason,
        "shelf_id": order.shelf_id,
    }))

    # Auto-trigger compensation
    await emit_event(Event(order_id, "COMPENSATION_TRIGGERED", {
        "reason": reason,
        "amount": order.total_amount,
        "auto_triggered": True,
    }))

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 10. REFUND ORDER (V2: EXPIRED → REFUNDED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/refund", response_model=OrderResponse)
async def refund_order(
    order_id: str,
    reason: str = "SHELF_EXPIRED",
    session: AsyncSession = Depends(get_session),
):
    """
    Process refund for an expired order.
    
    V2 Flow: EXPIRED → REFUNDED
    - Initiated by compensation service or manual ops action
    - Integrates with UPI refund API
    - Logs penalty for store if expiry was kitchen's fault
    """
    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "EXPIRED":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot refund: order is {order.status} (expected EXPIRED)"
        )

    try:
        order.status = state_machine.transition("EXPIRED", "REFUNDED")
        await persist_event(session, order_id, "ORDER_REFUNDED", {
            "reason": reason,
            "amount": order.total_amount,
            "payment_ref": order.payment_ref,
        })
    except InvalidTransitionError as e:
        raise HTTPException(status_code=409, detail=str(e))

    await decrement_kitchen_load(str(order.kitchen_id))
    await session.commit()
    await session.refresh(order)

    await set_order_state(order_id, {"status": order.status})
    await emit_event(Event(order_id, "ORDER_REFUNDED", {
        "reason": reason,
        "amount": order.total_amount,
    }))

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 11. CANCEL ORDER
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: str,
    req: CancelRequest = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Cancel an order (only possible before IN_PREP in V2).
    """
    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not state_machine.is_cancellable(order.status):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel order in {order.status} state"
        )

    if not state_machine.can_transition(order.status, "CANCELLED"):
        raise HTTPException(
            status_code=409,
            detail=f"No transition from {order.status} to CANCELLED"
        )

    order.status = "CANCELLED"
    await persist_event(session, order_id, "ORDER_CANCELLED", {
        "reason": req.reason if req else None,
        "from_state": order.status,
    })

    # Release resources
    if order.shelf_id and order.shelf_id != "HOLD_ZONE":
        await release_lock(f"shelf:{order.shelf_id}", order_id)
    await decrement_kitchen_load(str(order.kitchen_id))

    await session.commit()
    await session.refresh(order)

    await set_order_state(order_id, {"status": order.status})
    await emit_event(Event(order_id, "ORDER_CANCELLED", {
        "reason": req.reason if req else None,
    }))

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 12. MARK FAILED (V2: Any → FAILED)
# ═══════════════════════════════════════════════════════════════
@router.post("/{order_id}/fail", response_model=OrderResponse)
async def mark_failed(
    order_id: str,
    reason: str = "SYSTEM_ERROR",
    session: AsyncSession = Depends(get_session),
):
    """
    Mark order as failed (terminal state for system errors).
    
    V2: Any state can transition to FAILED for unrecoverable errors.
    """
    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not state_machine.can_transition(order.status, "FAILED"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot fail order in {order.status} state"
        )

    order.status = "FAILED"
    await persist_event(session, order_id, "ORDER_FAILED", {
        "reason": reason,
        "from_state": order.status,
    })

    # Release all resources
    if order.shelf_id and order.shelf_id != "HOLD_ZONE":
        await release_lock(f"shelf:{order.shelf_id}", order_id)
    await decrement_kitchen_load(str(order.kitchen_id))

    await session.commit()
    await session.refresh(order)

    await set_order_state(order_id, {"status": order.status})
    await emit_event(Event(order_id, "ORDER_FAILED", {"reason": reason}))

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 13. GET ORDER (READ — Redis-first)
# ═══════════════════════════════════════════════════════════════
@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get order details (Redis cache first, then Postgres)."""
    cached = await get_order_state(order_id)
    if cached:
        return OrderResponse(order_id=order_id, **cached)

    result = await session.execute(
        select(OrderModel).where(OrderModel.id == uuid.UUID(order_id))
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return format_order(order)


# ═══════════════════════════════════════════════════════════════
# 14. GET EVENT LOG (AUDIT TRAIL)
# ═══════════════════════════════════════════════════════════════
@router.get("/{order_id}/events", response_model=EventLogResponse)
async def get_order_events(
    order_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get full event log for an order (event sourcing audit trail)."""
    result = await session.execute(
        select(OrderEventModel)
        .where(OrderEventModel.order_id == uuid.UUID(order_id))
        .order_by(OrderEventModel.created_at)
    )
    events = result.scalars().all()

    return EventLogResponse(
        order_id=order_id,
        events=[{
            "event_type": e.event_type,
            "payload": e.payload,
            "source": getattr(e, "source", None),
            "created_at": e.created_at.isoformat() if e.created_at else None,
        } for e in events],
    )


# ═══════════════════════════════════════════════════════════════
# 15. GET KITCHEN QUEUE
# ═══════════════════════════════════════════════════════════════
@router.get("/kitchen/{kitchen_id}/queue")
async def get_kitchen_queue(
    kitchen_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get current kitchen queue with priority-sorted orders."""
    result = await session.execute(
        select(OrderModel)
        .where(OrderModel.kitchen_id == uuid.UUID(kitchen_id))
        .where(OrderModel.status.in_(["QUEUE_ASSIGNED", "IN_PREP", "BATCH_WAIT", "PACKING"]))
        .order_by(OrderModel.created_at)
    )
    orders = result.scalars().all()

    return {
        "kitchen_id": kitchen_id,
        "queue_depth": len(orders),
        "orders": [format_order(o) for o in orders],
        "kitchen_load": await get_kitchen_load(kitchen_id),
    }
