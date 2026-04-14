"""
HotSot Shared Types — Domain models used across all services.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ─── Order States ───

class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SLOT_RESERVED = "SLOT_RESERVED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    IN_PREP = "IN_PREP"
    READY = "READY"
    ON_SHELF = "ON_SHELF"
    PICKED = "PICKED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# Valid state transitions (state machine enforcement)
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


# ─── Event Types ───

class EventType(str, Enum):
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


# ─── Domain Models ───

class Order(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    kitchen_id: UUID
    status: OrderStatus = OrderStatus.CREATED
    eta_seconds: int | None = None
    eta_confidence: float | None = None
    shelf_id: str | None = None
    items: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OrderEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    event_type: EventType
    payload: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ShelfSlot(BaseModel):
    id: str
    kitchen_id: UUID
    order_id: UUID | None = None
    status: str = "AVAILABLE"  # AVAILABLE | OCCUPIED | LOCKED
    ttl_seconds: int = 600  # 10 minutes default
    assigned_at: datetime | None = None


class KitchenLoad(BaseModel):
    kitchen_id: UUID
    active_orders: int = 0
    queue_length: int = 0
    staff_available: int = 0
    load_percentage: float = 0.0
    is_overloaded: bool = False


class ETAPrediction(BaseModel):
    order_id: UUID
    eta_seconds: int
    confidence_interval: tuple[int, int]
    confidence_score: float
    risk_level: str  # LOW | MEDIUM | HIGH
    delay_probability: float
    buffer_seconds: int


class ArrivalSignal(BaseModel):
    user_id: UUID
    order_id: UUID
    kitchen_id: UUID
    estimated_arrival_seconds: int
    signal_type: str  # GPS | MANUAL | APP_FOREGROUND
    detected_at: datetime = Field(default_factory=datetime.utcnow)
