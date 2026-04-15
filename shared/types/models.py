"""
HotSot Shared Types — V1 Domain Models (Backward Compatibility).

These models are kept for backward compatibility with existing services.
New services should use V2 schemas from shared.types.schemas instead.

V1 Status: DEPRECATED — use shared.types.schemas for new code.
Migration guide:
    - V1 OrderStatus (9 states) → V2 OrderStatus (16 states)
    - V1 EventType (12 types) → V2 EventType (28+ types)
    - V1 Order → V2 OrderCreateRequest / OrderResponse
    - V1 OrderEvent → V2 EventEnvelope
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ─── V1 Order States (9 states — deprecated, use V2) ───

class OrderStatus(str, Enum):
    """V1 Order status — 9 states. Use V2 OrderStatus with 16 states for new code."""
    CREATED = "CREATED"
    SLOT_RESERVED = "SLOT_RESERVED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    IN_PREP = "IN_PREP"
    READY = "READY"
    ON_SHELF = "ON_SHELF"
    PICKED = "PICKED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# Valid V1 state transitions (state machine enforcement)
VALID_TRANSITIONS: dict[OrderStatus, list[OrderStatus]] = {
    OrderStatus.CREATED: [OrderStatus.SLOT_RESERVED, OrderStatus.CANCELLED],
    OrderStatus.SLOT_RESERVED: [OrderStatus.PAYMENT_CONFIRMED, OrderStatus.CANCELLED],
    OrderStatus.PAYMENT_CONFIRMED: [OrderStatus.IN_PREP, OrderStatus.CANCELLED],
    OrderStatus.IN_PREP: [OrderStatus.READY, OrderStatus.CANCELLED],
    OrderStatus.READY: [OrderStatus.ON_SHELF, OrderStatus.CANCELLED],
    OrderStatus.ON_SHELF: [OrderStatus.PICKED, OrderStatus.EXPIRED],
    OrderStatus.PICKED: [],
    OrderStatus.CANCELLED: [],
    OrderStatus.EXPIRED: [],
}


# ─── V1 Event Types ───

class EventType(str, Enum):
    """V1 Event types — 12 types. Use V2 EventType with 28+ types for new code."""
    ORDER_CREATED = "ORDER_CREATED"
    SLOT_RESERVED = "SLOT_RESERVED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    PREP_STARTED = "PREP_STARTED"
    ETA_UPDATED = "ETA_UPDATED"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    SHELF_ASSIGNED = "SHELF_ASSIGNED"
    SHELF_EXPIRED = "SHELF_EXPIRED"
    ORDER_PICKED = "ORDER_PICKED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    KITCHEN_OVERLOAD = "KITCHEN_OVERLOAD"
    ARRIVAL_DETECTED = "ARRIVAL_DETECTED"


# ─── V1 Domain Models ───

class Order(BaseModel):
    """
    V1 Order model — backward compatible.

    NOTE: New services should use V2 OrderCreateRequest / OrderResponse
    from shared.types.schemas.
    """
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    kitchen_id: UUID
    tenant_id: str = Field(default="default")
    status: OrderStatus = OrderStatus.CREATED
    eta_seconds: Optional[int] = None
    eta_confidence: Optional[float] = None
    shelf_id: Optional[str] = None
    items: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrderEvent(BaseModel):
    """
    V1 OrderEvent model — backward compatible.

    NOTE: New services should use V2 EventEnvelope from shared.types.schemas.
    """
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    tenant_id: str = Field(default="default")
    event_type: EventType
    payload: dict = Field(default_factory=dict)
    idempotency_key: Optional[str] = None
    source: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ShelfSlot(BaseModel):
    """V1 Shelf slot model."""
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kitchen_id: UUID
    tenant_id: str = Field(default="default")
    order_id: Optional[UUID] = None
    status: str = "AVAILABLE"  # AVAILABLE | OCCUPIED | LOCKED
    ttl_seconds: int = 600  # 10 minutes default
    assigned_at: Optional[datetime] = None


class KitchenLoad(BaseModel):
    """V1 Kitchen load model."""
    model_config = ConfigDict(populate_by_name=True)

    kitchen_id: UUID
    tenant_id: str = Field(default="default")
    active_orders: int = 0
    queue_length: int = 0
    staff_available: int = 0
    load_percentage: float = 0.0
    is_overloaded: bool = False


class ETAPrediction(BaseModel):
    """V1 ETA prediction model."""
    model_config = ConfigDict(populate_by_name=True)

    order_id: UUID
    tenant_id: str = Field(default="default")
    eta_seconds: int
    confidence_interval: tuple[int, int]
    confidence_score: float
    risk_level: str  # LOW | MEDIUM | HIGH
    delay_probability: float
    buffer_seconds: int


class ArrivalSignal(BaseModel):
    """V1 Arrival signal model."""
    model_config = ConfigDict(populate_by_name=True)

    user_id: UUID
    order_id: UUID
    kitchen_id: UUID
    tenant_id: str = Field(default="default")
    estimated_arrival_seconds: int
    signal_type: str  # GPS | MANUAL | APP_FOREGROUND
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
