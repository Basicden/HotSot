"""HotSot Compensation Service — Database Models."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import Base as SharedBase, BaseModelMixin

class Base(SharedBase):
    pass

class CompensationCaseModel(Base, BaseModelMixin):
    __tablename__ = "compensation_cases"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    reason = Column(String(50), nullable=False)  # SHELF_EXPIRED/KITCHEN_FAILURE/PAYMENT_CONFLICT/DELAY
    amount = Column(Float, nullable=False)
    currency = Column(String(3), default="INR")
    status = Column(String(20), default="PENDING")  # PENDING/APPROVED/PROCESSING/COMPLETED/FAILED
    auto_triggered = Column(Boolean, default=True)
    payment_ref = Column(String(100), nullable=True)
    refund_gateway_ref = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_cc_tenant_order", "tenant_id", "order_id"),
        Index("idx_cc_tenant_status", "tenant_id", "status"),
    )

class CompensationRuleModel(Base, BaseModelMixin):
    __tablename__ = "compensation_rules"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    reason = Column(String(50), nullable=False)
    compensation_type = Column(String(20), default="FULL_REFUND")  # FULL_REFUND/PARTIAL_REFUND/CREDIT
    percentage = Column(Float, default=100.0)
    max_amount = Column(Float, default=5000.0)
    auto_approve = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
