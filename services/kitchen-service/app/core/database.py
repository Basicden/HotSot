"""HotSot Kitchen Service — Database Layer."""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timezone
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://hotsot:hotsot_dev_pass@localhost:5432/hotsot")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")

engine = None
SessionLocal = None


class Base(DeclarativeBase):
    pass


class KitchenModel(Base):
    __tablename__ = "kitchens"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    location = Column(JSON, nullable=True)
    capacity = Column(Integer, default=20)
    current_load = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


async def init_db():
    global engine, SessionLocal
    engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def close_db():
    global engine
    if engine:
        await engine.dispose()


async def get_session():
    if SessionLocal is None:
        await init_db()
    async with SessionLocal() as session:
        yield session
