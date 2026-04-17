"""HotSot Compliance Service — Database Models."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import Numeric, UUID as PG_UUID, JSONB
from shared.utils.database import BaseModel


class VendorComplianceModel(BaseModel):
    """Vendor onboarding compliance tracking.

    Stores vendor compliance documents and their verification status.
    A vendor must have all required documents verified before being
    allowed to operate on the platform.
    """
    __tablename__ = "vendor_compliance"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    onboarding_status = Column(
        String(20), default="PENDING",
        comment="PENDING/IN_REVIEW/APPROVED/REJECTED/SUSPENDED",
    )
    fssai_license = Column(String(20), nullable=True, comment="FSSAI license number")
    fssai_verified = Column(Boolean, default=False, comment="Whether FSSAI license passed verification")
    fssai_verified_at = Column(DateTime(timezone=True), nullable=True)
    gst_number = Column(String(20), nullable=True, comment="GSTIN")
    gst_verified = Column(Boolean, default=False, comment="Whether GSTIN passed verification")
    gst_verified_at = Column(DateTime(timezone=True), nullable=True)
    dpdp_consent_obtained = Column(Boolean, default=False, comment="Whether vendor has consented to DPDP")
    dpdp_policy_published = Column(Boolean, default=False, comment="Whether privacy policy is published")
    dpdp_data_retention_days = Column(Integer, nullable=True, comment="Data retention period in days")
    dpdp_verified = Column(Boolean, default=False)
    rbi_payment_aggregator = Column(Boolean, default=False, comment="Whether registered as payment aggregator")
    rbi_escrow_maintained = Column(Boolean, default=False, comment="Whether escrow account is maintained")
    rbi_refund_compliance = Column(Boolean, default=False, comment="Whether T+5 refund timeline is met")
    rbi_verified = Column(Boolean, default=False)
    overall_status = Column(String(20), default="PENDING", comment="Overall compliance: PENDING/PASSED/FAILED")
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_vc_tenant_vendor", "tenant_id", "vendor_id", unique=True),
        Index("idx_vc_tenant_status", "tenant_id", "overall_status"),
    )


class ComplianceCheckModel(BaseModel):
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

class GSTRecordModel(BaseModel):
    __tablename__ = "gst_records"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False)
    taxable_amount = Column(Numeric(10, 2), nullable=False)
    cgst = Column(Numeric(10, 2), default=0.0)
    sgst = Column(Numeric(10, 2), default=0.0)
    igst = Column(Numeric(10, 2), default=Decimal("0.00"))
    total_gst = Column(Numeric(10, 2), nullable=False)
    gstin = Column(String(20), nullable=True)
    hsn_code = Column(String(10), default="9963")  # Food services
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# Model list for DB init
ALL_MODELS = [VendorComplianceModel, ComplianceCheckModel, GSTRecordModel]
