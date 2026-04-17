"""HotSot Vendor Service — Database Models."""
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import Numeric, UUID as PG_UUID, JSONB
from shared.utils.database import BaseModel


class VendorModel(BaseModel):
    """Vendor (restaurant/brand) entity."""
    __tablename__ = "vendors"
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
    commission_rate = Column(Numeric(5, 2), default=15.0)  # percentage
    tier = Column(String(20), default="STANDARD")  # STANDARD/PREMIUM/ENTERPRISE
    version = Column(Integer, default=1, nullable=False, comment="Optimistic locking version")


class VendorDocumentModel(BaseModel):
    __tablename__ = "vendor_documents"
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    doc_type = Column(String(30), nullable=False)  # GST/FSSAI/PAN/BANK_PROOF/ADDRESS
    doc_url = Column(String(500), nullable=False)
    verification_status = Column(String(20), default="PENDING")  # PENDING/VERIFIED/REJECTED
    verified_by = Column(PG_UUID(as_uuid=True), nullable=True)
    uploaded_at = Column(DateTime(timezone=True))
    verified_at = Column(DateTime(timezone=True), nullable=True)
