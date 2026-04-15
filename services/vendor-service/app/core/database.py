"""HotSot Vendor Service — Database Models."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import Base as SharedBase, BaseModelMixin

class Base(SharedBase):
    pass

class VendorModel(Base, BaseModelMixin):
    """Vendor (restaurant/brand) entity."""
    __tablename__ = "vendors"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    brand_name = Column(String(200), nullable=True)
    email = Column(String(200), nullable=False, unique=True)
    phone = Column(String(20), nullable=False)
    gstin = Column(String(20), nullable=True)
    fssai_license = Column(String(50), nullable=True)
    pan = Column(String(12), nullable=True)
    bank_account = Column(String(50), nullable=True)
    ifsc_code = Column(String(15), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(50), nullable=True)
    pincode = Column(String(10), nullable=True)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    onboarding_status = Column(String(20), default="PENDING")  # PENDING/DOCUMENTS_SUBMITTED/VERIFIED/REJECTED
    commission_rate = Column(Float, default=15.0)  # percentage
    tier = Column(String(20), default="STANDARD")  # STANDARD/PREMIUM/ENTERPRISE
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class VendorDocumentModel(Base, BaseModelMixin):
    __tablename__ = "vendor_documents"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    doc_type = Column(String(30), nullable=False)  # GST/FSSAI/PAN/BANK_PROOF/ADDRESS
    doc_url = Column(String(500), nullable=False)
    verification_status = Column(String(20), default="PENDING")  # PENDING/VERIFIED/REJECTED
    verified_by = Column(PG_UUID(as_uuid=True), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    verified_at = Column(DateTime(timezone=True), nullable=True)
