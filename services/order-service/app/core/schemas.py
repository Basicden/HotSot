"""HotSot Order Service — Pydantic Schemas (local copy for service independence)."""

from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SLOT_RESERVED = "SLOT_RESERVED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    IN_PREP = "IN_PREP"
    READY = "READY"
    ON_SHELF = "ON_SHELF"
    PICKED = "PICKED"
    CANCELLED = "CANCELLED"


VALID_TRANSITIONS: Dict[str, List[str]] = {
    "CREATED": ["SLOT_RESERVED", "CANCELLED"],
    "SLOT_RESERVED": ["PAYMENT_CONFIRMED", "CANCELLED"],
    "PAYMENT_CONFIRMED": ["IN_PREP", "CANCELLED"],
    "IN_PREP": ["READY"],
    "READY": ["ON_SHELF"],
    "ON_SHELF": ["PICKED"],
    "PICKED": [],
    "CANCELLED": [],
}


class EventType(str, Enum):
    ORDER_CREATED = "ORDER_CREATED"
    SLOT_RESERVED = "SLOT_RESERVED"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    PREP_STARTED = "PREP_STARTED"
    ETA_UPDATED = "ETA_UPDATED"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    SHELF_ASSIGNED = "SHELF_ASSIGNED"
    ORDER_PICKED = "ORDER_PICKED"
    ORDER_CANCELLED = "ORDER_CANCELLED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ─── API MODELS ───
class OrderCreateRequest(BaseModel):
    user_id: str
    kitchen_id: str
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total_amount: Optional[float] = None
    payment_method: str = "UPI"


class OrderResponse(BaseModel):
    order_id: str
    user_id: str
    kitchen_id: str
    status: str
    eta_seconds: Optional[int] = None
    eta_confidence: Optional[float] = None
    eta_risk: Optional[str] = None
    shelf_id: Optional[str] = None
    payment_ref: Optional[str] = None
    created_at: Optional[str] = None


class PaymentConfirmRequest(BaseModel):
    payment_ref: str
    payment_method: str = "UPI"


class CancelRequest(BaseModel):
    reason: Optional[str] = None


class EventLogResponse(BaseModel):
    order_id: str
    events: List[Dict[str, Any]]
