"""HotSot Kitchen Service — Database Models (Database-per-Service)."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, Text, Index, ForeignKey
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import relationship

from shared.utils.database import Base as SharedBase, BaseModelMixin


class Base(SharedBase):
    pass


class KitchenModel(Base, BaseModelMixin):
    """Kitchen / cloud kitchen entity."""
    __tablename__ = "kitchens"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    vendor_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    location = Column(String(500), nullable=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    max_concurrent_orders = Column(Integer, default=20)
    staff_count = Column(Integer, default=3)
    is_active = Column(Boolean, default=True)
    size = Column(String(20), default="medium")  # small/medium/large
    throughput_per_minute = Column(Integer, default=15)
    fssai_license = Column(String(50), nullable=True)
    gstin = Column(String(20), nullable=True)
    version = Column(Integer, default=1, nullable=False, comment="Optimistic locking version")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_kitchen_tenant_vendor", "tenant_id", "vendor_id"),
        Index("idx_kitchen_tenant_active", "tenant_id", "is_active"),
    )


class KitchenQueueModel(Base, BaseModelMixin):
    """Kitchen queue — one entry per order waiting for or in prep."""
    __tablename__ = "kitchen_queue"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), ForeignKey("kitchens.id"), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    queue_type = Column(String(20), default="NORMAL")  # IMMEDIATE/NORMAL/BATCH
    priority_score = Column(Float, default=50.0)
    queue_position = Column(Integer, default=0)
    batch_category = Column(String(20), nullable=True)
    batch_id = Column(PG_UUID(as_uuid=True), nullable=True)
    status = Column(String(20), default="QUEUED")  # QUEUED/IN_PREP/COMPLETED/CANCELLED
    version = Column(Integer, default=1, nullable=False, comment="Optimistic locking version")
    arrival_boost = Column(Float, default=0.0)
    staff_id = Column(PG_UUID(as_uuid=True), nullable=True)
    enqueued_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_kq_tenant_kitchen_status", "tenant_id", "kitchen_id", "status"),
        Index("idx_kq_tenant_order", "tenant_id", "order_id"),
        Index("idx_kq_priority", "kitchen_id", "priority_score"),
    )


class KitchenStaffModel(Base, BaseModelMixin):
    """Kitchen staff members."""
    __tablename__ = "kitchen_staff"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), ForeignKey("kitchens.id"), nullable=False, index=True)
    staff_id = Column(PG_UUID(as_uuid=True), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    role = Column(String(20), default="CHEF")  # CHEF/PACKER/RUNNER
    is_available = Column(Boolean, default=True)
    current_order_id = Column(PG_UUID(as_uuid=True), nullable=True)
    last_heartbeat = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_ks_tenant_kitchen", "tenant_id", "kitchen_id"),
    )


class KitchenThroughputModel(Base, BaseModelMixin):
    """Time-series throughput measurements for a kitchen."""
    __tablename__ = "kitchen_throughput"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), ForeignKey("kitchens.id"), nullable=False, index=True)
    orders_per_minute = Column(Float, default=0.0)
    active_orders = Column(Integer, default=0)
    queue_length = Column(Integer, default=0)
    load_percentage = Column(Float, default=0.0)
    is_overloaded = Column(Boolean, default=False)
    measured_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("idx_kt_tenant_kitchen_time", "tenant_id", "kitchen_id", "measured_at"),
    )


class BatchGroupModel(Base, BaseModelMixin):
    """Batch cooking groups — orders with same category cooked together."""
    __tablename__ = "batch_groups"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), ForeignKey("kitchens.id"), nullable=False, index=True)
    batch_id = Column(PG_UUID(as_uuid=True), nullable=False, unique=True)
    category = Column(String(20), nullable=False)  # GRILL/FRYER/COLD/RICE_BOWL
    order_ids = Column(JSONB, default=list)
    status = Column(String(20), default="FORMING")  # FORMING/IN_PROGRESS/COMPLETED
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_bg_tenant_kitchen_status", "tenant_id", "kitchen_id", "status"),
    )
