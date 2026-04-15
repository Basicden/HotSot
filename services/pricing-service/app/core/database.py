"""HotSot Pricing Service — Database Models."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import Base as SharedBase, BaseModelMixin

class Base(SharedBase):
    pass

class PricingRuleModel(Base, BaseModelMixin):
    __tablename__ = "pricing_rules"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    rule_type = Column(String(30), nullable=False)  # SURGE/DISCOUNT/LOYALTY/COMBO
    name = Column(String(200), nullable=False)
    multiplier = Column(Float, default=1.0)
    flat_amount = Column(Float, default=0.0)
    conditions = Column(JSONB, default=dict)
    priority = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    start_time = Column(DateTime(timezone=True), nullable=True)
    end_time = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
