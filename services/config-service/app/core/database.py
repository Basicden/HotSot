"""HotSot Config Service — Database Models."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import Base as SharedBase, BaseModelMixin

class Base(SharedBase):
    pass

class ConfigModel(Base, BaseModelMixin):
    __tablename__ = "service_configs"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    service_name = Column(String(50), nullable=False, index=True)
    config_key = Column(String(100), nullable=False)
    config_value = Column(JSONB, nullable=False)
    is_encrypted = Column(Boolean, default=False)
    updated_by = Column(PG_UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_config_tenant_service_key", "tenant_id", "service_name", "config_key", unique=True),
    )
