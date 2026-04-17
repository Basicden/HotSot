"""HotSot Analytics Service — Database Models."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import Numeric, UUID as PG_UUID, JSONB
from shared.utils.database import BaseModel

class Base(SharedBase):
    pass

class AnalyticsEventModel(BaseModel):
    __tablename__ = "analytics_events"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    event_name = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(30), nullable=True)
    entity_id = Column(PG_UUID(as_uuid=True), nullable=True)
    properties = Column(JSONB, default=dict)
    user_id = Column(PG_UUID(as_uuid=True), nullable=True)
    session_id = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

class DailyMetricModel(BaseModel):
    __tablename__ = "daily_metrics"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=True, index=True)
    metric_date = Column(DateTime(timezone=True), nullable=False, index=True)
    total_orders = Column(Integer, default=0)
    completed_orders = Column(Integer, default=0)
    cancelled_orders = Column(Integer, default=0)
    expired_orders = Column(Integer, default=0)
    total_revenue = Column(Numeric(12, 2), default=0.0)
    avg_prep_time_seconds = Column(Integer, default=0)
    avg_pickup_time_seconds = Column(Integer, default=0)
    avg_shelf_ttl_remaining = Column(Integer, default=0)
    customer_satisfaction = Column(Float, default=0.0)


# Model list for DB init
ALL_MODELS = [AnalyticsEventModel, DailyMetricModel]
