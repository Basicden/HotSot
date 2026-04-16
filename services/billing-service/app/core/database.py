"""HotSot Billing Service — Database Models."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import Numeric, UUID as PG_UUID, JSONB
from shared.utils.database import BaseModel

class Base(SharedBase):
    pass

class InvoiceModel(BaseModel):
    __tablename__ = "invoices"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    invoice_number = Column(String(50), nullable=False, unique=True)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    total_orders = Column(Integer, default=0)
    total_revenue = Column(Numeric(12, 2), default=0.0)
    commission_amount = Column(Numeric(12, 2), default=0.0)
    gst_amount = Column(Numeric(10, 2), default=0.0)
    payout_amount = Column(Numeric(12, 2), default=0.0)
    status = Column(String(20), default="DRAFT")  # DRAFT/SENT/PAID/OVERDUE
    due_date = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class PayoutModel(BaseModel):
    __tablename__ = "payouts"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    invoice_id = Column(PG_UUID(as_uuid=True), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String(20), default="BANK_TRANSFER")  # BANK_TRANSFER/UPI
    status = Column(String(20), default="PENDING")  # PENDING/PROCESSING/COMPLETED/FAILED
    reference = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)


# Model list for DB init
ALL_MODELS = [InvoiceModel, PayoutModel]
