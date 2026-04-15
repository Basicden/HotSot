"""HotSot Shelf Service — Database Models."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import Base as SharedBase, BaseModelMixin


class Base(SharedBase):
    pass


class ShelfSlotModel(Base, BaseModelMixin):
    """Physical shelf slot in a kitchen."""
    __tablename__ = "shelf_slots"

    id = Column(String(50), primary_key=True)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=True)
    zone = Column(String(20), default="HOT")  # HOT/COLD/AMBIENT
    status = Column(String(20), default="AVAILABLE")  # AVAILABLE/OCCUPIED/LOCKED/MAINTENANCE
    ttl_seconds = Column(Integer, default=600)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    warm_station_eligible = Column(Boolean, default=True)

    __table_args__ = (
        Index("idx_shelf_tenant_kitchen", "tenant_id", "kitchen_id"),
        Index("idx_shelf_tenant_status", "tenant_id", "status"),
    )


class ShelfAssignmentModel(Base, BaseModelMixin):
    """Shelf assignment history for audit trail."""
    __tablename__ = "shelf_assignments"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    shelf_id = Column(String(50), nullable=False)
    zone = Column(String(20), default="HOT")
    ttl_seconds = Column(Integer, default=600)
    ttl_remaining = Column(Integer, default=600)
    status = Column(String(20), default="ACTIVE")  # ACTIVE/EXPIRED/RELEASED
    assigned_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expired_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_sa_tenant_order", "tenant_id", "order_id"),
        Index("idx_sa_tenant_kitchen_status", "tenant_id", "kitchen_id", "status"),
    )


class ShelfTTLLogModel(Base, BaseModelMixin):
    """TTL event log for monitoring and analytics."""
    __tablename__ = "shelf_ttl_logs"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    shelf_id = Column(String(50), nullable=False)
    order_id = Column(PG_UUID(as_uuid=True), nullable=False)
    event = Column(String(20), nullable=False)  # WARNING/EXPIRED/EXTENDED
    ttl_remaining = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
