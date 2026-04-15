"""HotSot Arrival Service — Database Models."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from shared.utils.database import Base as SharedBase, BaseModelMixin


class Base(SharedBase):
    pass


class ArrivalEventModel(Base, BaseModelMixin):
    """Arrival detection events for audit trail."""
    __tablename__ = "arrival_events"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    signal_type = Column(String(20), default="GPS")  # GPS/MANUAL/APP_FOREGROUND/QR_SCAN
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    distance_meters = Column(Float, nullable=True)
    is_valid = Column(Boolean, default=False)
    idempotency_key = Column(String(64), nullable=True, unique=True)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_arrival_tenant_order", "tenant_id", "order_id"),
        Index("idx_arrival_tenant_kitchen", "tenant_id", "kitchen_id"),
    )
