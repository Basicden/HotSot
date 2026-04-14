"""HotSot Order Service — Async PostgreSQL Database Layer."""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, JSON, Text
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timezone

from app.core.config import config


engine = None
SessionLocal = None


class Base(DeclarativeBase):
    pass


class OrderModel(Base):
    """SQLAlchemy ORM model for orders table."""
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    kitchen_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String(30), nullable=False, default="CREATED")
    eta_seconds = Column(Integer, nullable=True)
    eta_confidence = Column(Float, nullable=True)
    eta_risk = Column(String(10), default="LOW")
    items = Column(JSON, default=[])
    total_amount = Column(Float, nullable=True)
    payment_method = Column(String(20), default="UPI")
    payment_ref = Column(String(100), nullable=True)
    shelf_id = Column(String(10), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    prep_started_at = Column(DateTime(timezone=True), nullable=True)
    ready_at = Column(DateTime(timezone=True), nullable=True)
    picked_at = Column(DateTime(timezone=True), nullable=True)


class OrderEventModel(Base):
    """SQLAlchemy ORM model for order_events table."""
    __tablename__ = "order_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), nullable=False)
    event_type = Column(String(50), nullable=False)
    payload = Column(JSON, default={})
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


async def init_db():
    """Initialize database engine and session factory."""
    global engine, SessionLocal
    engine = create_async_engine(config.DATABASE_URL, echo=False, pool_size=20, max_overflow=10)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def close_db():
    """Close database engine."""
    global engine
    if engine:
        await engine.dispose()


async def get_session() -> AsyncSession:
    """Get a database session."""
    if SessionLocal is None:
        await init_db()
    async with SessionLocal() as session:
        yield session
