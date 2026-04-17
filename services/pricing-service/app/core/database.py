"""HotSot Pricing Service — Database Models.

All monetary fields use Numeric (not Float) to avoid IEEE 754 rounding errors.
This is critical for INR calculations — float(199.99) = 199.98999999999998.
"""
from sqlalchemy import Column, String, Integer, Boolean, DateTime
from sqlalchemy.dialects.postgresql import Numeric, UUID as PG_UUID, JSONB
from shared.utils.database import BaseModel


class PricingRuleModel(BaseModel):
    __tablename__ = "pricing_rules"
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    rule_type = Column(String(30), nullable=False)  # SURGE/DISCOUNT/TIER_DISCOUNT/TIME_BASED/HAPPY_HOUR
    name = Column(String(200), nullable=False)
    multiplier = Column(Numeric(6, 3), default=1.0)       # Numeric — precise Decimal
    flat_amount = Column(Numeric(10, 2), default=0.0)     # Numeric — precise Decimal
    conditions = Column(JSONB, default=dict)
    priority = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    start_time = Column(DateTime(timezone=True), nullable=True)
    end_time = Column(DateTime(timezone=True), nullable=True)

# Model list for DB init
ALL_MODELS = [PricingRuleModel]
