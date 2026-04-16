"""
HotSot Order Service — Production Database Layer.

Uses shared.utils.database for the database-per-service pattern.
Every model has tenant_id for Row-Level Security (RLS).

Models:
    - OrderModel: Full order lifecycle with 16-state status
    - OrderEventModel: Event sourcing audit trail
    - PaymentModel: Payment tracking with escrow support
    - SagaInstanceModel: Saga state persistence for recovery
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Index, Text,
)
from sqlalchemy.dialects.postgresql import Numeric, UUID as PG_UUID, JSONB
from sqlalchemy.orm import Mapped

from shared.utils.database import BaseModel


# ═══════════════════════════════════════════════════════════════
# ORDER MODEL
# ═══════════════════════════════════════════════════════════════

class OrderModel(BaseModel):
    """
    Order model — full lifecycle with V2 16-state status.

    Tracks:
    - Identity: id, tenant_id, user_id, kitchen_id
    - Status: status (16-state), idempotency_key
    - ETA: eta_seconds, eta_confidence, eta_risk
    - Items: items (JSONB), total_amount
    - Payment: payment_method, payment_ref
    - Shelf: shelf_id, shelf_zone, shelf_ttl_remaining
    - Queue: queue_type, queue_position, priority_score, batch_id
    - User: user_tier
    - Timestamps: prep_started_at, ready_at, arrived_at, picked_at
    """
    __tablename__ = "orders"

    user_id = Column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="User who placed the order",
    )
    kitchen_id = Column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="Kitchen assigned to prepare the order",
    )
    status = Column(
        String(50),
        nullable=False,
        default="CREATED",
        comment="V2 16-state order status",
    )
    eta_seconds = Column(Integer, nullable=True, comment="Estimated prep time in seconds")
    eta_confidence = Column(Float, nullable=True, comment="ETA confidence score 0-1")
    eta_risk = Column(String(20), default="LOW", comment="ETA risk level: LOW/MEDIUM/HIGH/CRITICAL")

    items = Column(JSONB, default=list, comment="Order items as JSONB array")
    total_amount = Column(Numeric(10, 2), nullable=True, comment="Total order amount in INR")

    payment_method = Column(String(30), default="UPI", comment="Payment method: UPI/CARD/WALLET/COD")
    payment_ref = Column(String(100), nullable=True, comment="External payment reference")

    shelf_id = Column(String(20), nullable=True, comment="Physical shelf slot ID")
    shelf_zone = Column(String(20), nullable=True, comment="Shelf zone: HOT/COLD/AMBIENT")
    shelf_ttl_remaining = Column(Integer, nullable=True, comment="Seconds remaining before shelf expiry")

    queue_type = Column(String(20), nullable=True, comment="Queue type: IMMEDIATE/NORMAL/BATCH")
    queue_position = Column(Integer, nullable=True, comment="Position in kitchen queue")
    priority_score = Column(Float, nullable=True, comment="Computed priority score")
    batch_id = Column(String(50), nullable=True, comment="Batch ID for batch cooking")

    user_tier = Column(String(20), default="FREE", comment="User tier: FREE/PLUS/PRO/VIP")

    idempotency_key = Column(
        String(64),
        nullable=True,
        unique=True,
        comment="Idempotency key for safe retries",
    )

    prep_started_at = Column(DateTime(timezone=True), nullable=True)
    ready_at = Column(DateTime(timezone=True), nullable=True)
    arrived_at = Column(DateTime(timezone=True), nullable=True)
    picked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_orders_tenant_status", "tenant_id", "status"),
        Index("ix_orders_tenant_kitchen_status", "tenant_id", "kitchen_id", "status"),
        Index("ix_orders_tenant_user", "tenant_id", "user_id"),
        Index("ix_orders_idempotency_key", "idempotency_key", unique=True),
    )


# ═══════════════════════════════════════════════════════════════
# ORDER EVENT MODEL (Event Sourcing)
# ═══════════════════════════════════════════════════════════════

class OrderEventModel(BaseModel):
    """
    Event sourcing audit trail for all order state changes.

    Every mutation on an order produces an event record.
    Events are immutable — they are never updated or deleted.
    """
    __tablename__ = "order_events"

    order_id = Column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="FK to the order this event belongs to",
    )
    event_type = Column(
        String(60),
        nullable=False,
        comment="Event type from V2 EventType enum",
    )
    payload = Column(JSONB, default=dict, comment="Event-specific data")
    source = Column(
        String(50),
        default="order-service",
        comment="Service that produced this event",
    )
    sequence_number = Column(
        Integer,
        default=0,
        comment="Monotonic sequence number for ordering",
    )
    idempotency_key = Column(
        String(64),
        nullable=True,
        comment="Idempotency key for dedup",
    )

    __table_args__ = (
        Index("ix_order_events_tenant_order", "tenant_id", "order_id"),
        Index("ix_order_events_tenant_type", "tenant_id", "event_type"),
    )


# ═══════════════════════════════════════════════════════════════
# PAYMENT MODEL
# ═══════════════════════════════════════════════════════════════

class PaymentModel(BaseModel):
    """
    Payment tracking with escrow support.

    Payment states: PENDING → PROCESSING → CONFIRMED → RELEASED
    Failure path:  PENDING → PROCESSING → FAILED
    Refund path:   CONFIRMED → REFUNDED

    Escrow model: payment is HELD until order is PICKED,
    then released to the vendor.
    """
    __tablename__ = "payments"

    order_id = Column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="FK to the order this payment is for",
    )
    payment_ref = Column(
        String(100),
        nullable=True,
        unique=True,
        comment="External payment reference from gateway",
    )
    payment_method = Column(
        String(30),
        default="UPI",
        comment="Payment method: UPI/CARD/WALLET/COD",
    )
    gateway_txn_id = Column(
        String(100),
        nullable=True,
        comment="Gateway transaction ID (e.g., Razorpay payment ID)",
    )
    amount = Column(Numeric(10, 2), nullable=False, comment="Payment amount in INR")
    currency = Column(String(3), default="INR", comment="Currency code")
    status = Column(
        String(20),
        default="PENDING",
        comment="Payment status: PENDING/PROCESSING/CONFIRMED/FAILED/REFUNDED/RELEASED",
    )
    webhook_data = Column(JSONB, default=dict, comment="Raw webhook data from payment gateway")
    idempotency_key = Column(
        String(64),
        nullable=True,
        comment="Idempotency key for safe retries",
    )

    __table_args__ = (
        Index("ix_payments_tenant_order", "tenant_id", "order_id"),
        Index("ix_payments_tenant_status", "tenant_id", "status"),
        Index("ix_payments_payment_ref", "payment_ref", unique=True),
    )


# ═══════════════════════════════════════════════════════════════
# SAGA INSTANCE MODEL
# ═══════════════════════════════════════════════════════════════

class SagaInstanceModel(BaseModel):
    """
    Saga state persistence for recovery and observability.

    Tracks the progress of each saga (order lifecycle) through
    its steps. Used for:
    - Crash recovery: resume saga from last completed step
    - Compensation: know which steps to undo on failure
    - Observability: track saga execution time and success rate
    """
    __tablename__ = "saga_instances"

    order_id = Column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="FK to the order this saga manages",
    )
    saga_type = Column(
        String(50),
        default="OrderLifecycle",
        comment="Type of saga (OrderLifecycle, Refund, etc.)",
    )
    current_step = Column(
        String(50),
        nullable=False,
        comment="Current saga step name",
    )
    status = Column(
        String(20),
        default="RUNNING",
        comment="Saga status: RUNNING/COMPLETED/FAILED/COMPENSATING/COMPENSATED",
    )
    steps_completed = Column(
        JSONB,
        default=list,
        comment="List of completed step names",
    )
    steps_failed = Column(
        JSONB,
        default=list,
        comment="List of failed step names",
    )
    compensation_steps = Column(
        JSONB,
        default=list,
        comment="Compensation steps to execute on rollback",
    )
    error_message = Column(Text, nullable=True, comment="Last error message")
    timeout_at = Column(DateTime(timezone=True), nullable=True, comment="Saga timeout deadline")
    completed_at = Column(DateTime(timezone=True), nullable=True, comment="When saga completed")

    __table_args__ = (
        Index("ix_saga_tenant_order", "tenant_id", "order_id"),
        Index("ix_saga_tenant_status", "tenant_id", "status"),
    )


# ═══════════════════════════════════════════════════════════════
# DLQ MESSAGE MODEL (for admin endpoint)
# ═══════════════════════════════════════════════════════════════

class DLQMessageModel(BaseModel):
    """
    Local DLQ storage for failed Kafka messages.

    Stores messages that failed processing after max retries,
    enabling admin inspection and replay.
    """
    __tablename__ = "dlq_messages"

    original_topic = Column(String(200), nullable=True, comment="Original Kafka topic")
    original_event = Column(JSONB, default=dict, comment="Original event payload")
    failure_reason = Column(Text, nullable=True, comment="Why the message failed")
    consumer_service = Column(String(50), default="order-service")
    retry_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    replayed = Column(
        String(10),
        default="PENDING",
        comment="PENDING/REPLAYED/DISCARDED",
    )

    __table_args__ = (
        Index("ix_dlq_tenant_status", "tenant_id", "replayed"),
    )


# ═══════════════════════════════════════════════════════════════
# MODEL LIST FOR DB INIT
# ═══════════════════════════════════════════════════════════════

ALL_MODELS = [OrderModel, OrderEventModel, PaymentModel, SagaInstanceModel, DLQMessageModel]
