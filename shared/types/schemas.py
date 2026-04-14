"""HotSot Shared Types — V2 Production-Grade Cross-service Type Definitions."""

from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


# ═══════════════════════════════════════════════════════════════
# V2 ORDER STATES (16 states — production-grade lifecycle)
# ═══════════════════════════════════════════════════════════════
class OrderStatus(str, Enum):
    CREATED = "CREATED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    SLOT_RESERVED = "SLOT_RESERVED"
    QUEUE_ASSIGNED = "QUEUE_ASSIGNED"
    IN_PREP = "IN_PREP"
    BATCH_WAIT = "BATCH_WAIT"
    PACKING = "PACKING"
    READY = "READY"
    ON_SHELF = "ON_SHELF"
    ARRIVED = "ARRIVED"
    HANDOFF_IN_PROGRESS = "HANDOFF_IN_PROGRESS"
    PICKED = "PICKED"
    EXPIRED = "EXPIRED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


# ═══════════════════════════════════════════════════════════════
# V2 VALID TRANSITIONS (Distributed-safe state machine)
# ═══════════════════════════════════════════════════════════════
VALID_TRANSITIONS: Dict[str, List[str]] = {
    "CREATED": ["PAYMENT_PENDING", "CANCELLED", "FAILED"],
    "PAYMENT_PENDING": ["PAYMENT_CONFIRMED", "CANCELLED", "FAILED"],
    "PAYMENT_CONFIRMED": ["SLOT_RESERVED", "CANCELLED"],
    "SLOT_RESERVED": ["QUEUE_ASSIGNED", "CANCELLED"],
    "QUEUE_ASSIGNED": ["IN_PREP", "BATCH_WAIT", "CANCELLED"],
    "BATCH_WAIT": ["IN_PREP", "CANCELLED"],
    "IN_PREP": ["PACKING", "FAILED"],
    "PACKING": ["READY", "FAILED"],
    "READY": ["ON_SHELF", "CANCELLED"],
    "ON_SHELF": ["ARRIVED", "EXPIRED", "CANCELLED"],
    "ARRIVED": ["HANDOFF_IN_PROGRESS", "EXPIRED"],
    "HANDOFF_IN_PROGRESS": ["PICKED", "EXPIRED"],
    "PICKED": [],
    "EXPIRED": ["REFUNDED"],
    "REFUNDED": [],
    "CANCELLED": [],
    "FAILED": [],
}

# Terminal states (no further transitions possible)
TERMINAL_STATES = {"PICKED", "EXPIRED", "REFUNDED", "CANCELLED", "FAILED"}

# States where cancellation is allowed
CANCELLABLE_STATES = {
    "CREATED", "PAYMENT_PENDING", "PAYMENT_CONFIRMED",
    "SLOT_RESERVED", "QUEUE_ASSIGNED", "BATCH_WAIT", "READY"
}


# ═══════════════════════════════════════════════════════════════
# V2 EVENT TYPES (Complete event taxonomy)
# ═══════════════════════════════════════════════════════════════
class EventType(str, Enum):
    # Order lifecycle
    ORDER_CREATED = "ORDER_CREATED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    SLOT_RESERVED = "SLOT_RESERVED"
    SLOT_FILLED = "SLOT_FILLED"
    PRIORITY_UPDATED = "PRIORITY_UPDATED"
    QUEUE_ASSIGNED = "QUEUE_ASSIGNED"
    QUEUE_REORDERED = "QUEUE_REORDERED"
    PREP_STARTED = "PREP_STARTED"
    PREP_COMPLETED = "PREP_COMPLETED"
    BATCH_FORMED = "BATCH_FORMED"
    PACKING_STARTED = "PACKING_STARTED"
    PACKING_COMPLETED = "PACKING_COMPLETED"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    SHELF_ASSIGNED = "SHELF_ASSIGNED"
    SHELF_EXPIRY_WARNING = "SHELF_EXPIRY_WARNING"
    SHELF_EXPIRED = "SHELF_EXPIRED"
    ARRIVAL_DETECTED = "ARRIVAL_DETECTED"
    HANDOFF_STARTED = "HANDOFF_STARTED"
    HANDOFF_COMPLETED = "HANDOFF_COMPLETED"
    ORDER_PICKED = "ORDER_PICKED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    ORDER_REFUNDED = "ORDER_REFUNDED"
    ORDER_FAILED = "ORDER_FAILED"
    # Kitchen events
    KITCHEN_OVERLOAD = "KITCHEN_OVERLOAD"
    KITCHEN_DEGRADED = "KITCHEN_DEGRADED"
    KITCHEN_RECOVERED = "KITCHEN_RECOVERED"
    THROTTLE_APPLIED = "THROTTLE_APPLIED"
    KITCHEN_THROUGHPUT_UPDATE = "KITCHEN_THROUGHPUT_UPDATE"
    # ETA events
    ETA_UPDATED = "ETA_UPDATED"
    ETA_RECALCULATED = "ETA_RECALCULATED"
    # Compensation events
    COMPENSATION_TRIGGERED = "COMPENSATION_TRIGGERED"
    COMPENSATION_COMPLETED = "COMPENSATION_COMPLETED"
    # Incident events
    INCIDENT_DELAY_DETECTED = "INCIDENT_DELAY_DETECTED"
    INCIDENT_ESCALATED = "INCIDENT_ESCALATED"


# ═══════════════════════════════════════════════════════════════
# V2 RISK LEVELS + PRIORITY TIERS
# ═══════════════════════════════════════════════════════════════
class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class UserTier(str, Enum):
    FREE = "FREE"
    PLUS = "PLUS"
    PRO = "PRO"
    VIP = "VIP"


class QueueType(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    NORMAL = "NORMAL"
    BATCH = "BATCH"


class ShelfZone(str, Enum):
    HOT = "HOT"
    COLD = "COLD"
    AMBIENT = "AMBIENT"


class BatchCategory(str, Enum):
    GRILL = "GRILL"
    FRYER = "FRYER"
    COLD = "COLD"
    RICE_BOWL = "RICE_BOWL"


# Batch category → item mapping (India food reality model)
BATCH_CATEGORY_MAP: Dict[str, List[str]] = {
    "GRILL": ["burger", "tandoor", "kebab", "tikka", "grilled_sandwich"],
    "FRYER": ["fries", "pakoda", "fried_chicken", "samosa", "vada"],
    "COLD": ["salad", "dessert", "drinks", "raita", "chaas"],
    "RICE_BOWL": ["biryani", "fried_rice", "thali", "dal_rice", "pulao"],
}

# User tier weights for priority engine
TIER_WEIGHTS: Dict[str, int] = {
    "FREE": 1,
    "PLUS": 2,
    "PRO": 3,
    "VIP": 4,
}

# Shelf TTL by zone
SHELF_TTL_SECONDS: Dict[str, int] = {
    "HOT": 600,     # 10 minutes
    "COLD": 900,    # 15 minutes
    "AMBIENT": 1200, # 20 minutes
}

# Kitchen throughput assumptions (India Tier-2 realistic)
KITCHEN_THROUGHPUT = {
    "small": 8,     # orders/min
    "medium": 15,   # orders/min
    "large": 25,    # orders/min
}

PEAK_DEGRADATION = 0.40  # -40% throughput during peak


# ═══════════════════════════════════════════════════════════════
# V2 PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════

class EventEnvelope(BaseModel):
    """Canonical event envelope — every event inherits this structure."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    order_id: str
    kitchen_id: Optional[str] = None
    sequence_number: int = 0
    idempotency_key: Optional[str] = None
    source: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    schema_version: int = 2
    payload: Dict[str, Any] = Field(default_factory=dict)


class OrderCreateRequest(BaseModel):
    user_id: str
    kitchen_id: str
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total_amount: Optional[float] = None
    payment_method: str = "UPI"
    user_tier: UserTier = UserTier.FREE
    requested_time: Optional[str] = None
    idempotency_key: Optional[str] = None


class PaymentInitRequest(BaseModel):
    order_id: str
    payment_method: str = "UPI"
    upi_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class PaymentConfirmRequest(BaseModel):
    payment_ref: str
    payment_method: str = "UPI"
    gateway_txn_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class ArrivalRequest(BaseModel):
    order_id: str
    user_id: str
    latitude: float
    longitude: float
    qr_scan: bool = False
    idempotency_key: Optional[str] = None


class HandoffConfirmRequest(BaseModel):
    order_id: str
    staff_id: str
    confirmation_method: str = "QR_SCAN"  # QR_SCAN, MANUAL_ACK
    idempotency_key: Optional[str] = None


class CancelRequest(BaseModel):
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None


class OrderResponse(BaseModel):
    order_id: str
    user_id: str
    kitchen_id: str
    status: str
    queue_type: Optional[str] = None
    queue_position: Optional[int] = None
    priority_score: Optional[float] = None
    eta_seconds: Optional[int] = None
    eta_confidence: Optional[float] = None
    eta_risk: Optional[str] = None
    shelf_id: Optional[str] = None
    shelf_zone: Optional[str] = None
    shelf_ttl_remaining: Optional[int] = None
    payment_ref: Optional[str] = None
    batch_id: Optional[str] = None
    arrived_at: Optional[str] = None
    picked_at: Optional[str] = None
    created_at: Optional[str] = None


class EventLogResponse(BaseModel):
    order_id: str
    events: List[Dict[str, Any]] = Field(default_factory=list)


class ETAResponse(BaseModel):
    order_id: str
    eta_seconds: int
    confidence_interval: List[int] = Field(default_factory=lambda: [0, 0])
    risk_level: RiskLevel = RiskLevel.LOW
    delay_probability: float = 0.0
    features_used: Dict[str, Any] = Field(default_factory=dict)


class ShelfAssignment(BaseModel):
    order_id: str
    shelf_id: str
    zone: ShelfZone = ShelfZone.HOT
    ttl_seconds: int = 600


class KitchenPriorityScore(BaseModel):
    order_id: str
    score: float
    arrival_proximity: float
    delay_risk: float
    age_factor: float
    tier_bonus: float
    queue_type: QueueType = QueueType.NORMAL
    batch_category: Optional[str] = None


class CompensationEvent(BaseModel):
    order_id: str
    reason: str  # SHELF_EXPIRED, KITCHEN_FAILURE, PAYMENT_CONFLICT
    amount: float
    currency: str = "INR"
    auto_triggered: bool = True


class IncidentEvent(BaseModel):
    incident_id: str
    kitchen_id: str
    incident_type: str  # DELAY_SPIKE, KITCHEN_DOWN, SHELF_OVERFLOW
    severity: RiskLevel = RiskLevel.HIGH
    affected_orders: List[str] = Field(default_factory=list)
    mitigation_applied: Optional[str] = None


class DLQMessage(BaseModel):
    original_event: Dict[str, Any]
    failure_reason: str
    consumer_service: str
    retry_count: int = 0
    last_error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


# ═══════════════════════════════════════════════════════════════
# V2 KAFKA TOPICS (Complete topic taxonomy)
# ═══════════════════════════════════════════════════════════════
KAFKA_TOPICS = {
    # Order lifecycle topics (partition key: order_id)
    "hotsot.order.events.v1": "All order lifecycle events — partitioned by order_id for strict ordering",
    # Kitchen operation topics (partition key: kitchen_id)
    "hotsot.kitchen.events.v1": "Kitchen execution events — partitioned by kitchen_id",
    # Slot engine topics (partition key: order_id)
    "hotsot.slot.events.v1": "Slot reservation and conflict events",
    # Priority engine topics (partition key: order_id)
    "hotsot.priority.events.v1": "Priority scoring and queue assignment events",
    # Shelf management topics (partition key: kitchen_id)
    "hotsot.shelf.events.v1": "Shelf assignment, TTL, and expiry events",
    # Arrival detection topics (partition key: order_id)
    "hotsot.arrival.events.v1": "User arrival and geofence events",
    # Notification topics (partition key: user_id)
    "hotsot.notification.events.v1": "Push, SMS, and in-app notification events",
    # Compensation topics (partition key: order_id)
    "hotsot.compensation.events.v1": "Refund and compensation events",
    # Incident topics (partition key: kitchen_id)
    "hotsot.incident.events.v1": "Incident detection and escalation events",
    # DLQ topics
    "hotsot.dlq.order.events.v1": "Dead letter queue for order events",
    "hotsot.dlq.kitchen.events.v1": "Dead letter queue for kitchen events",
    "hotsot.dlq.slot.events.v1": "Dead letter queue for slot events",
}


# ═══════════════════════════════════════════════════════════════
# V2 GUARD RULES (State machine guard conditions)
# ═══════════════════════════════════════════════════════════════
GUARD_RULES = {
    "SLOT_RESERVED": {
        "condition": "slot_available == true OR waitlist_allowed == true",
        "description": "Cannot proceed to SLOT_RESERVED if no slot available and waitlist not allowed",
    },
    "QUEUE_ASSIGNED": {
        "condition": "priority_score computed AND kitchen_ack_ready",
        "description": "Priority score must be computed before queue assignment",
    },
    "ON_SHELF": {
        "condition": "shelf_id != null AND shelf_capacity > 0",
        "description": "Shelf assignment requires a valid shelf with capacity",
    },
    "ARRIVED": {
        "condition": "gps_distance <= 150m OR qr_scan_valid == true",
        "description": "Arrival requires GPS within 150m or valid QR scan",
    },
    "EXPIRED": {
        "condition": "shelf_ttl_exceeded == true AND state != PICKED",
        "description": "Expiry triggers only if TTL exceeded and order not picked",
    },
}

# Idempotency dedup strategies
IDEMPOTENCY_STRATEGIES = {
    "arrival_event_key": "hash(user_id + order_id + floor(timestamp/30s))",
    "payment_event_key": "gateway_txn_id",
    "kitchen_event_key": "order_id + kitchen_state_hash",
    "order_create_key": "idempotency_key from client",
}

# Conflict resolution rules
CONFLICT_RESOLUTION = {
    "payment_vs_slot_release": "payment wins over slot release",
    "kitchen_state": "eventually consistent",
    "arrival": "soft-consistent (never blocks order state)",
    "duplicate_handoff": "first HANDOFF_COMPLETED wins, rest are idempotent no-ops",
}
