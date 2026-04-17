"""HotSot Notification Service — Database Models."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from shared.utils.database import Base as SharedBase, BaseModelMixin

class Base(SharedBase):
    pass

class NotificationTemplateModel(Base, BaseModelMixin):
    __tablename__ = "notification_templates"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    template_key = Column(String(100), nullable=False)
    channel = Column(String(20), nullable=False)  # SMS/PUSH/WHATSAPP/EMAIL
    language = Column(String(5), default="en")
    title_template = Column(String(500), nullable=True)
    body_template = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_nt_tenant_key", "tenant_id", "template_key"),
    )

class NotificationPreferenceModel(Base, BaseModelMixin):
    """User notification preferences — controls which channels are enabled."""
    __tablename__ = "notification_preferences"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    push_enabled = Column(Boolean, default=True)
    sms_enabled = Column(Boolean, default=True)
    email_enabled = Column(Boolean, default=True)
    in_app_enabled = Column(Boolean, default=True)
    whatsapp_enabled = Column(Boolean, default=False)
    quiet_hours_start = Column(Integer, default=21, comment="IST hour when quiet period starts (9 PM)")
    quiet_hours_end = Column(Integer, default=9, comment="IST hour when quiet period ends (9 AM)")
    language = Column(String(5), default="en")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_np_tenant_user", "tenant_id", "user_id", unique=True),
    )


class InAppNotificationModel(Base, BaseModelMixin):
    """In-app notifications stored in DB for retrieval on app open."""
    __tablename__ = "in_app_notifications"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=True)
    title = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    data = Column(JSONB, default=dict)
    is_read = Column(Boolean, default=False)
    priority = Column(String(10), default="NORMAL")  # LOW/NORMAL/HIGH/URGENT
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_ian_tenant_user_read", "tenant_id", "user_id", "is_read"),
    )


class NotificationLogModel(Base, BaseModelMixin):
    __tablename__ = "notification_logs"
    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    order_id = Column(PG_UUID(as_uuid=True), nullable=True)
    channel = Column(String(20), nullable=False)
    template_key = Column(String(100), nullable=True)
    title = Column(String(500), nullable=True)
    body = Column(Text, nullable=True)
    status = Column(String(20), default="PENDING")  # PENDING/SENT/FAILED/DELIVERED/BLOCKED/RATE_LIMITED
    provider_ref = Column(String(200), nullable=True)
    provider_response = Column(JSONB, default=dict)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_nl_tenant_user", "tenant_id", "user_id"),
        Index("idx_nl_tenant_status", "tenant_id", "status"),
    )
