"""
Compensation Decision Engine
----------------------------
Core logic that determines refund type, amount, and tracks penalties
when compensation-triggering events are received.

Decision Rules
──────────────
  SHELF_EXPIRED                 → FULL_REFUND (food quality degraded)
  KITCHEN_FAILURE + PAID        → FULL_REFUND
  Delay > 15 min                → CREDIT_NOTE  (₹50–100)
  Wrong order delivered         → FULL_REFUND + RE_MAKE_ORDER

Idempotency: every refund is keyed by (order_id, reason) so that
duplicate event delivery never creates double refunds.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import redis
from kafka import KafkaProducer

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────

class CompensationReason(str, Enum):
    SHELF_EXPIRED = "SHELF_EXPIRED"
    KITCHEN_FAILURE = "KITCHEN_FAILURE"
    PAYMENT_CONFLICT = "PAYMENT_CONFLICT"
    DELAY_EXCEEDED = "DELAY_EXCEEDED"
    WRONG_ORDER_DELIVERED = "WRONG_ORDER_DELIVERED"
    CUSTOMER_COMPLAINT = "CUSTOMER_COMPLAINT"


class RefundType(str, Enum):
    FULL_REFUND = "FULL_REFUND"
    PARTIAL_REFUND = "PARTIAL_REFUND"
    CREDIT_NOTE = "CREDIT_NOTE"
    RE_MAKE_ORDER = "RE_MAKE_ORDER"


class CompensationStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"


class OrderPaymentStatus(str, Enum):
    PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    NOT_PAID = "NOT_PAID"


# ── Pydantic-style data models (kept lightweight without pydantic here
#    so the engine core has zero web-framework coupling) ──────────────────

class CompensationRequest:
    """Inbound event that may trigger a compensation."""

    def __init__(
        self,
        order_id: str,
        reason: CompensationReason,
        kitchen_id: str,
        payment_status: OrderPaymentStatus = OrderPaymentStatus.NOT_PAID,
        order_amount: float = 0.0,
        delay_minutes: Optional[int] = None,
        payment_ref: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        self.order_id = order_id
        self.reason = reason
        self.kitchen_id = kitchen_id
        self.payment_status = payment_status
        self.order_amount = order_amount
        self.delay_minutes = delay_minutes
        self.payment_ref = payment_ref
        self.metadata = metadata or {}


class CompensationResult:
    """Outcome produced by the decision engine."""

    def __init__(
        self,
        compensation_id: str,
        order_id: str,
        reason: CompensationReason,
        refund_type: RefundType,
        amount: float,
        status: CompensationStatus,
        payment_ref: Optional[str] = None,
        upi_refund_ref: Optional[str] = None,
        is_duplicate: bool = False,
        created_at: Optional[str] = None,
    ):
        self.compensation_id = compensation_id
        self.order_id = order_id
        self.reason = reason
        self.refund_type = refund_type
        self.amount = amount
        self.status = status
        self.payment_ref = payment_ref
        self.upi_refund_ref = upi_refund_ref
        self.is_duplicate = is_duplicate
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "compensation_id": self.compensation_id,
            "order_id": self.order_id,
            "reason": self.reason.value,
            "refund_type": self.refund_type.value,
            "amount": self.amount,
            "status": self.status.value,
            "payment_ref": self.payment_ref,
            "upi_refund_ref": self.upi_refund_ref,
            "is_duplicate": self.is_duplicate,
            "created_at": self.created_at,
        }


# ── Engine ───────────────────────────────────────────────────────────────

class CompensationEngine:
    """
    Stateless decision engine that evaluates compensation requests and
    produces refund actions.

    Side-effects (Redis, Kafka) are performed inside the engine so that
    a single call from the route layer is sufficient.
    """

    def __init__(self):
        self.redis_client: Optional[redis.Redis] = None
        self.kafka_producer: Optional[KafkaProducer] = None

    # ── Lifecycle ────────────────────────────────────────────────────

    def init_connections(self) -> None:
        """Initialise Redis and Kafka connections.  Called at app startup."""
        try:
            self.redis_client = redis.from_url(
                settings.REDIS_URL, decode_responses=True
            )
            self.redis_client.ping()
            logger.info("CompensationEngine: Redis connected")
        except Exception as exc:
            logger.error("CompensationEngine: Redis connection failed – %s", exc)
            self.redis_client = None

        try:
            self.kafka_producer = KafkaProducer(
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS.split(","),
                client_id=settings.KAFKA_CLIENT_ID,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                retries=3,
                linger_ms=10,
            )
            logger.info("CompensationEngine: Kafka producer ready")
        except Exception as exc:
            logger.error("CompensationEngine: Kafka producer failed – %s", exc)
            self.kafka_producer = None

    def close_connections(self) -> None:
        """Graceful shutdown."""
        if self.kafka_producer:
            try:
                self.kafka_producer.flush()
                self.kafka_producer.close()
            except Exception:
                pass
        if self.redis_client:
            try:
                self.redis_client.close()
            except Exception:
                pass

    # ── Idempotency ──────────────────────────────────────────────────

    def _idempotency_key(self, order_id: str, reason: CompensationReason) -> str:
        return f"{settings.REDIS_IDEMPOTENCY_PREFIX}{order_id}:{reason.value}"

    def _check_idempotency(self, order_id: str, reason: CompensationReason) -> Optional[dict]:
        """Return cached result if this (order_id, reason) was already processed."""
        if not self.redis_client:
            return None
        key = self._idempotency_key(order_id, reason)
        cached = self.redis_client.get(key)
        if cached:
            logger.info("Idempotency hit for %s", key)
            return json.loads(cached)
        return None

    def _mark_idempotency(self, order_id: str, reason: CompensationReason, result: dict) -> None:
        if not self.redis_client:
            return
        key = self._idempotency_key(order_id, reason)
        # TTL of 24 hours – more than enough to catch replays
        self.redis_client.setex(key, 86400, json.dumps(result))

    # ── Decision logic ───────────────────────────────────────────────

    def _decide(self, request: CompensationRequest) -> tuple[list[RefundType], float]:
        """
        Core decision function.  Returns (refund_types, total_amount).

        Business rules (in priority order):
        1. SHELF_EXPIRED                → FULL_REFUND
        2. KITCHEN_FAILURE + PAID       → FULL_REFUND
        3. Delay > threshold            → CREDIT_NOTE ₹50–100
        4. WRONG_ORDER_DELIVERED        → FULL_REFUND + RE_MAKE_ORDER
        """
        refund_types: list[RefundType] = []
        total_amount = 0.0

        if request.reason == CompensationReason.SHELF_EXPIRED:
            refund_types.append(RefundType.FULL_REFUND)
            total_amount = request.order_amount
            # Track shelf expiration penalty for kitchen
            self._track_shelf_expiration(request.kitchen_id)

        elif request.reason == CompensationReason.KITCHEN_FAILURE:
            if request.payment_status in (
                OrderPaymentStatus.PAYMENT_CONFIRMED,
                OrderPaymentStatus.PAYMENT_PENDING,
            ):
                refund_types.append(RefundType.FULL_REFUND)
                total_amount = request.order_amount

        elif request.reason == CompensationReason.PAYMENT_CONFLICT:
            # Payment was deducted but order never confirmed
            refund_types.append(RefundType.FULL_REFUND)
            total_amount = request.order_amount

        elif request.reason == CompensationReason.DELAY_EXCEEDED:
            refund_types.append(RefundType.CREDIT_NOTE)
            # Credit note amount scales with delay severity
            if request.delay_minutes and request.delay_minutes > 30:
                total_amount = settings.CREDIT_NOTE_MAX_AMOUNT
            else:
                total_amount = settings.CREDIT_NOTE_MIN_AMOUNT

        elif request.reason == CompensationReason.WRONG_ORDER_DELIVERED:
            refund_types.append(RefundType.FULL_REFUND)
            refund_types.append(RefundType.RE_MAKE_ORDER)
            total_amount = request.order_amount

        elif request.reason == CompensationReason.CUSTOMER_COMPLAINT:
            # Partial refund for validated customer complaints
            refund_types.append(RefundType.PARTIAL_REFUND)
            total_amount = round(request.order_amount * 0.5, 2)

        # Safety: if no rule matched, still record the event with zero amount
        if not refund_types:
            logger.warning(
                "No compensation rule matched for order=%s reason=%s",
                request.order_id,
                request.reason.value,
            )
            refund_types.append(RefundType.PARTIAL_REFUND)
            total_amount = 0.0

        return refund_types, total_amount

    # ── Shelf expiration penalty tracking ────────────────────────────

    def _track_shelf_expiration(self, kitchen_id: str) -> None:
        """
        Increment daily counter for shelf expirations on a kitchen.
        If count > threshold, flag the kitchen for review.
        """
        if not self.redis_client:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        penalty_key = f"{settings.REDIS_PENALTY_PREFIX}{kitchen_id}:{today}"
        count = self.redis_client.incr(penalty_key)
        # Expire at end of day (86400 s is generous)
        self.redis_client.expire(penalty_key, 86400)

        if count > settings.SHELF_EXPIRATION_PENALTY_THRESHOLD:
            logger.warning(
                "Kitchen %s has %d shelf expirations today (threshold=%d) – FLAGGED FOR REVIEW",
                kitchen_id,
                count,
                settings.SHELF_EXPIRATION_PENALTY_THRESHOLD,
            )
            self._emit_penalty_event(kitchen_id, count)

    def _emit_penalty_event(self, kitchen_id: str, expiration_count: int) -> None:
        """Emit a penalty flag event to Kafka."""
        if not self.kafka_producer:
            return
        event = {
            "event_type": "KITCHEN_PENALTY_FLAG",
            "kitchen_id": kitchen_id,
            "shelf_expiration_count": expiration_count,
            "threshold": settings.SHELF_EXPIRATION_PENALTY_THRESHOLD,
            "action": "REVIEW_REQUIRED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.kafka_producer.send(
                settings.KAFKA_COMPENSATION_TOPIC,
                key=kitchen_id.encode("utf-8"),
                value=event,
            )
            logger.info("Penalty event emitted for kitchen %s", kitchen_id)
        except Exception as exc:
            logger.error("Failed to emit penalty event: %s", exc)

    # ── UPI Refund (simulated) ───────────────────────────────────────

    def _process_upi_refund(
        self, order_id: str, amount: float, payment_ref: Optional[str]
    ) -> str:
        """
        Simulate a UPI refund.  In production this would call the
        payment gateway's refund API.  Here we generate a tracking ref.
        """
        upi_refund_ref = f"UPI_REF_{uuid.uuid4().hex[:12].upper()}"
        logger.info(
            "UPI refund simulated: order=%s amount=%.2f payment_ref=%s → upi_ref=%s",
            order_id,
            amount,
            payment_ref,
            upi_refund_ref,
        )
        return upi_refund_ref

    # ── Kafka event emission ─────────────────────────────────────────

    def _emit_compensation_event(self, result: CompensationResult) -> None:
        if not self.kafka_producer:
            return
        event = {
            "event_type": "COMPENSATION_PROCESSED",
            "compensation_id": result.compensation_id,
            "order_id": result.order_id,
            "reason": result.reason.value,
            "refund_type": result.refund_type.value,
            "amount": result.amount,
            "status": result.status.value,
            "upi_refund_ref": result.upi_refund_ref,
            "timestamp": result.created_at,
        }
        try:
            self.kafka_producer.send(
                settings.KAFKA_COMPENSATION_TOPIC,
                key=result.order_id.encode("utf-8"),
                value=event,
            )
            logger.info("Compensation event emitted for order %s", result.order_id)
        except Exception as exc:
            logger.error("Failed to emit compensation event: %s", exc)

    # ── Public API ───────────────────────────────────────────────────

    def evaluate(self, request: CompensationRequest) -> CompensationResult:
        """
        Main entry point.  Evaluates the request, enforces idempotency,
        executes the refund decision, persists state, and emits events.
        """
        # 1. Idempotency check
        cached = self._check_idempotency(request.order_id, request.reason)
        if cached:
            return CompensationResult(
                compensation_id=cached["compensation_id"],
                order_id=cached["order_id"],
                reason=CompensationReason(cached["reason"]),
                refund_type=RefundType(cached["refund_type"]),
                amount=cached["amount"],
                status=CompensationStatus.DUPLICATE_SKIPPED,
                payment_ref=cached.get("payment_ref"),
                upi_refund_ref=cached.get("upi_refund_ref"),
                is_duplicate=True,
                created_at=cached.get("created_at"),
            )

        # 2. Decision
        refund_types, total_amount = self._decide(request)
        primary_refund = refund_types[0]

        # 3. Process UPI refund if money is owed
        upi_refund_ref = None
        if total_amount > 0 and primary_refund in (
            RefundType.FULL_REFUND,
            RefundType.PARTIAL_REFUND,
        ):
            upi_refund_ref = self._process_upi_refund(
                request.order_id, total_amount, request.payment_ref
            )

        # 4. Build result
        compensation_id = f"COMP_{uuid.uuid4().hex[:10].upper()}"
        result = CompensationResult(
            compensation_id=compensation_id,
            order_id=request.order_id,
            reason=request.reason,
            refund_type=primary_refund,
            amount=total_amount,
            status=CompensationStatus.COMPLETED,
            payment_ref=request.payment_ref,
            upi_refund_ref=upi_refund_ref,
            is_duplicate=False,
        )

        # 5. Persist to Redis
        self._persist_compensation(result)

        # 6. Mark idempotency
        self._mark_idempotency(request.order_id, request.reason, result.to_dict())

        # 7. Emit Kafka event
        self._emit_compensation_event(result)

        # 8. If RE_MAKE_ORDER is part of the decision, emit a separate event
        if RefundType.RE_MAKE_ORDER in refund_types:
            self._emit_remake_order_event(request, compensation_id)

        return result

    def _persist_compensation(self, result: CompensationResult) -> None:
        """Store compensation record in Redis for status queries."""
        if not self.redis_client:
            return
        key = f"{settings.REDIS_COMPENSATION_PREFIX}{result.order_id}"
        self.redis_client.set(key, json.dumps(result.to_dict()))
        logger.info("Compensation persisted for order %s", result.order_id)

    def _emit_remake_order_event(
        self, request: CompensationRequest, compensation_id: str
    ) -> None:
        """Emit a RE_MAKE_ORDER event so the order service can recreate the order."""
        if not self.kafka_producer:
            return
        event = {
            "event_type": "RE_MAKE_ORDER",
            "compensation_id": compensation_id,
            "order_id": request.order_id,
            "kitchen_id": request.kitchen_id,
            "original_amount": request.order_amount,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.kafka_producer.send(
                settings.KAFKA_COMPENSATION_TOPIC,
                key=request.order_id.encode("utf-8"),
                value=event,
            )
        except Exception as exc:
            logger.error("Failed to emit RE_MAKE_ORDER event: %s", exc)

    # ── Status lookup ────────────────────────────────────────────────

    def get_compensation_status(self, order_id: str) -> Optional[dict]:
        """Retrieve stored compensation record for an order."""
        if not self.redis_client:
            return None
        key = f"{settings.REDIS_COMPENSATION_PREFIX}{order_id}"
        data = self.redis_client.get(key)
        if data:
            return json.loads(data)
        return None

    # ── Penalty lookup ───────────────────────────────────────────────

    def get_kitchen_penalty(self, kitchen_id: str) -> dict:
        """Get current penalty status for a kitchen."""
        if not self.redis_client:
            return {"kitchen_id": kitchen_id, "expiration_count": 0, "flagged": False}
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        penalty_key = f"{settings.REDIS_PENALTY_PREFIX}{kitchen_id}:{today}"
        count = int(self.redis_client.get(penalty_key) or 0)
        return {
            "kitchen_id": kitchen_id,
            "date": today,
            "shelf_expiration_count": count,
            "threshold": settings.SHELF_EXPIRATION_PENALTY_THRESHOLD,
            "flagged": count > settings.SHELF_EXPIRATION_PENALTY_THRESHOLD,
        }


# ── Module-level singleton ──────────────────────────────────────────────

compensation_engine = CompensationEngine()
