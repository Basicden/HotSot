"""
HotSot Database Setup — Async SQLAlchemy with PostgreSQL.
"""

import logging
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    ForeignKey,
    Text,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship

from shared.utils.config import settings

logger = logging.getLogger(__name__)


# ─── Base ───

class Base(DeclarativeBase):
    pass


# ─── Engine & Session ───

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=20,
    max_overflow=10,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """Dependency for FastAPI endpoints."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")


# ─── Models ───

class OrderModel(Base):
    __tablename__ = "orders"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    kitchen_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="CREATED", index=True)
    eta_seconds = Column(Integer, nullable=True)
    eta_confidence = Column(Float, nullable=True)
    shelf_id = Column(String(50), nullable=True)
    items = Column(JSONB, default=list)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    events = relationship("OrderEventModel", back_populates="order", lazy="selectin")

    __table_args__ = (
        Index("idx_orders_kitchen_status", "kitchen_id", "status"),
        Index("idx_orders_created_at", "created_at"),
    )


class OrderEventModel(Base):
    __tablename__ = "order_events"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    order_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True
    )
    event_type = Column(String(100), nullable=False, index=True)
    payload = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    order = relationship("OrderModel", back_populates="events")

    __table_args__ = (
        Index("idx_events_order_type", "order_id", "event_type"),
    )


class KitchenModel(Base):
    __tablename__ = "kitchens"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(200), nullable=False)
    location = Column(String(500), nullable=True)
    max_concurrent_orders = Column(Integer, default=20)
    staff_count = Column(Integer, default=3)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)


class ShelfSlotModel(Base):
    __tablename__ = "shelf_slots"

    id = Column(String(50), primary_key=True)
    kitchen_id = Column(
        PG_UUID(as_uuid=True), ForeignKey("kitchens.id"), nullable=False
    )
    order_id = Column(PG_UUID(as_uuid=True), nullable=True)
    status = Column(String(50), default="AVAILABLE")
    ttl_seconds = Column(Integer, default=600)
    assigned_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_shelf_kitchen_status", "kitchen_id", "status"),
    )
