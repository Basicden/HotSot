"""HotSot ETA Service — Database Models."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import Base as SharedBase, BaseModelMixin


class Base(SharedBase):
    pass


class ETAPredictionModel(Base, BaseModelMixin):
    """ETA prediction records for audit and model improvement."""
    __tablename__ = "eta_predictions"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    eta_seconds = Column(Integer, nullable=False)
    confidence_interval_low = Column(Integer, nullable=True)
    confidence_interval_high = Column(Integer, nullable=True)
    confidence_score = Column(Float, default=0.5)
    risk_level = Column(String(10), default="LOW")
    delay_probability = Column(Float, default=0.0)
    features_used = Column(JSONB, default=dict)
    model_version = Column(String(20), default="v2_weighted")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("idx_eta_tenant_order", "tenant_id", "order_id"),
        Index("idx_eta_tenant_kitchen", "tenant_id", "kitchen_id"),
    )


class KitchenETABaselineModel(Base, BaseModelMixin):
    """Kitchen-specific ETA baselines calibrated from historical data."""
    __tablename__ = "kitchen_eta_baselines"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), nullable=False, unique=True)
    base_prep_seconds = Column(Integer, default=300)
    peak_prep_seconds = Column(Integer, default=420)
    average_delay_seconds = Column(Integer, default=60)
    last_updated = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
