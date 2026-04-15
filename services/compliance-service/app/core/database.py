"""HotSot Compliance Service — Database Models."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import Base as SharedBase, BaseModelMixin

class Base(SharedBase):
    pass

class ComplianceCheckModel(Base, BaseModelMixin):
    __tablename__ = "compliance_checks"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    entity_type = Column(String(30), nullable=False)  # VENDOR/ORDER/KITCHEN/PAYMENT
    entity_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    check_type = Column(String(30), nullable=False)  # FSSAI/GST/DPDP/RBI/FOOD_SAFETY
    status = Column(String(20), default="PENDING")  # PENDING/PASSED/FAILED/WARNING
    details = Column(JSONB, default=dict)
    checked_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    next_check_at = Column(DateTime(timezone=True), nullable=True)

class GSTRecordModel(Base, BaseModelMixin):
    __tablename__ = "gst_records"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False)
    taxable_amount = Column(Float, nullable=False)
    cgst = Column(Float, default=0.0)
    sgst = Column(Float, default=0.0)
    igst = Column(Float, default=0.0)
    total_gst = Column(Float, nullable=False)
    gstin = Column(String(20), nullable=True)
    hsn_code = Column(String(10), default="9963")  # Food services
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
