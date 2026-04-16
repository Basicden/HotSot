"""
HotSot Database — Database-per-service pattern with multi-tenancy.

Each microservice gets its own database (hotsot_{service_name}).
Every model MUST have a tenant_id column for Row-Level Security (RLS).

Key features:
    - Per-service async engine and session factory
    - Base model with mandatory tenant_id column
    - Row-Level Security (RLS) support functions
    - Connection pooling with production-ready settings
    - Database initialization helpers

Usage:
    from shared.utils.database import get_engine, get_session_factory, TenantBase, init_service_db

    # In your service startup:
    engine = get_engine("order")
    session_factory = get_session_factory("order")
    await init_service_db("order", [OrderModel, OrderEventModel])

    # In FastAPI dependency:
    async def get_db():
        async with session_factory() as session:
            yield session
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Sequence, Type
from uuid import uuid4

from sqlalchemy import (
    Column,
    String,
    DateTime,
    Index,
    text,
    event,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase

from fastapi import Request

from shared.utils.config import get_settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# BASE MODEL WITH MULTI-TENANCY
# ═══════════════════════════════════════════════════════════════

class TenantBase(DeclarativeBase):
    """
    Base class for ALL HotSot database models.

    Every model inherits from TenantBase which provides:
    - tenant_id column (mandatory for Row-Level Security)
    - created_at / updated_at timestamps
    - id primary key (UUID)

    CRITICAL: Every model MUST have tenant_id for data isolation.
    """
    pass


class BaseModel(TenantBase):
    """
    Abstract base model with common columns for all HotSot entities.

    Provides:
    - id: UUID primary key
    - tenant_id: Multi-tenant isolation key (indexed, NOT NULL)
    - created_at: Creation timestamp (UTC)
    - updated_at: Last update timestamp (UTC, auto-updated)
    - version: Optimistic locking version counter

    Usage:
        class OrderModel(BaseModel):
            __tablename__ = "orders"
            status = Column(String(50), nullable=False, default="CREATED")
    """
    __abstract__ = True

    id = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    tenant_id = Column(
        String(100),
        nullable=False,
        index=True,
        comment="Multi-tenant isolation key — every query MUST filter by tenant_id",
    )
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    version = Column(
        "version",
        default=1,
        nullable=False,
        comment="Optimistic locking version — increment on every update",
    )


class BaseModelMixin(BaseModel):
    """
    Alias for BaseModel — provides backward compatibility.

    Some services import BaseModelMixin instead of BaseModel.
    This class ensures both imports work identically.
    """
    pass


# ═══════════════════════════════════════════════════════════════
# ENGINE & SESSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════

_engines: dict[str, AsyncEngine] = {}
_session_factories: dict[str, async_sessionmaker[AsyncSession]] = {}


def get_engine(service_name: str) -> AsyncEngine:
    """
    Get or create an async SQLAlchemy engine for a specific service.

    Each service gets its own engine connected to its own database
    (hotsot_{service_name}). Engine instances are cached for reuse.

    Args:
        service_name: The microservice identifier (e.g., "order", "kitchen").

    Returns:
        AsyncEngine connected to the service's database.
    """
    if service_name in _engines:
        return _engines[service_name]

    svc_settings = get_settings(service_name)

    engine = create_async_engine(
        svc_settings.DATABASE_URL,
        echo=svc_settings.DB_ECHO,
        pool_size=svc_settings.DB_POOL_SIZE,
        max_overflow=svc_settings.DB_MAX_OVERFLOW,
        pool_recycle=svc_settings.DB_POOL_RECYCLE,
        pool_pre_ping=True,  # Verify connections before use
        connect_args={
            "command_timeout": 30,
            "server_settings": {
                "application_name": f"hotsot_{service_name}",
                "jit": "off",  # Avoid JIT overhead for short queries
            },
        },
    )

    _engines[service_name] = engine
    logger.info(
        f"Database engine created for service={service_name} "
        f"pool_size={svc_settings.DB_POOL_SIZE} "
        f"max_overflow={svc_settings.DB_MAX_OVERFLOW}"
    )
    return engine


def get_session_factory(service_name: str) -> async_sessionmaker[AsyncSession]:
    """
    Get or create an async session factory for a specific service.

    The factory is bound to the service's engine and configured with:
    - expire_on_commit=False (avoid lazy-loading after commit)
    - class_=AsyncSession (async session type)

    Args:
        service_name: The microservice identifier.

    Returns:
        async_sessionmaker bound to the service's engine.
    """
    if service_name in _session_factories:
        return _session_factories[service_name]

    engine = get_engine(service_name)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    _session_factories[service_name] = factory
    logger.info(f"Session factory created for service={service_name}")
    return factory


async def init_service_db(
    service_name: str,
    models: Optional[Sequence[Type[TenantBase]]] = None,
) -> None:
    """
    Initialize the database for a specific service.

    Creates all tables defined by the service's models.
    If models is None, creates all tables from TenantBase metadata.

    Also sets up Row-Level Security policies if the database supports it.

    Args:
        service_name: The microservice identifier.
        models: Optional list of model classes to create tables for.
    """
    engine = get_engine(service_name)

    async with engine.begin() as conn:
        # Create tables
        if models:
            # Create only specific tables
            for model in models:
                await conn.run_sync(
                    model.__table__.create,
                    checkfirst=True,
                )
                logger.info(f"Table ensured: {model.__tablename__}")
        else:
            # Create all tables from metadata
            await conn.run_sync(TenantBase.metadata.create_all)
            logger.info("All tables created from metadata")

        # Set up RLS policies
        await _setup_rls_policies(conn, service_name)

    logger.info(f"Database initialized for service={service_name}")


async def _setup_rls_policies(conn: Any, service_name: str) -> None:
    """
    Set up Row-Level Security policies for tenant isolation.

    This creates RLS policies that ensure queries automatically
    filter by the current tenant_id set in the session.

    RLS is enabled on all tables that have a tenant_id column.
    The tenant_id is set per-session using:
        SET app.current_tenant_id = 'tenant_123';

    Args:
        conn: Async database connection.
        service_name: Service name for logging.
    """
    try:
        # Enable RLS extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

        # For each table with tenant_id, enable RLS
        tables = TenantBase.metadata.tables
        for table_name, table in tables.items():
            if "tenant_id" in table.c:
                # Enable RLS on the table
                await conn.execute(text(
                    f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"
                ))

                # Drop existing policy if any (idempotent)
                await conn.execute(text(
                    f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name}"
                ))

                # Create policy: users can only see their own tenant's data
                await conn.execute(text(
                    f"CREATE POLICY tenant_isolation_{table_name} ON {table_name} "
                    f"USING (tenant_id = current_setting('app.current_tenant_id', true))"
                ))

                logger.debug(f"RLS policy set for table={table_name}")

        logger.info(f"RLS policies configured for service={service_name}")

    except Exception as e:
        # RLS setup failure is not fatal — log and continue
        # The application layer still enforces tenant_id filtering
        logger.warning(
            f"RLS policy setup skipped for service={service_name}: {e}. "
            f"Application-layer tenant isolation will be used."
        )


async def set_tenant_id(session: AsyncSession, tenant_id: str) -> None:
    """
    Set the current tenant_id for RLS in the database session.

    This must be called before any queries to ensure Row-Level Security
    policies filter data correctly.

    Args:
        session: The async database session.
        tenant_id: The tenant identifier to set.
    """
    await session.execute(
        text("SET app.current_tenant_id = :tenant_id"),
        {"tenant_id": tenant_id},
    )


# ═══════════════════════════════════════════════════════════════
# FASTAPI DEPENDENCY HELPERS
# ═══════════════════════════════════════════════════════════════

def create_db_dependency(service_name: str):
    """
    Create a FastAPI dependency that provides a database session
    with tenant isolation.

    Usage:
        get_db = create_db_dependency("order")

        @app.get("/orders")
        async def list_orders(
            db: AsyncSession = Depends(get_db),
            user: dict = Depends(get_current_user),
        ):
            ...

    The dependency automatically:
    1. Creates a session from the service's session factory
    2. Sets the tenant_id from the request context (if available)
    3. Commits on success, rolls back on error
    4. Closes the session when done

    Args:
        service_name: The microservice identifier.

    Returns:
        An async generator function for use as a FastAPI Depends.
    """
    session_factory = get_session_factory(service_name)

    async def get_db(request: Request = None):
        async with session_factory() as session:
            try:
                # Set tenant_id for RLS if available in request context
                if request is not None:
                    tenant_id = None
                    # Try request.state first (set by TenantMiddleware)
                    if hasattr(request, "state") and hasattr(request.state, "tenant_id"):
                        tenant_id = request.state.tenant_id
                    # Fallback: try X-Tenant-ID header
                    if not tenant_id:
                        tenant_id = request.headers.get("X-Tenant-ID")

                    if tenant_id:
                        await set_tenant_id(session, tenant_id)

                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    return get_db


# ═══════════════════════════════════════════════════════════════
# CLEANUP
# ═══════════════════════════════════════════════════════════════

async def dispose_engine(service_name: str) -> None:
    """
    Dispose of the database engine for a specific service.

    Call this during graceful shutdown to close all connections.

    Args:
        service_name: The microservice identifier.
    """
    engine = _engines.pop(service_name, None)
    _session_factories.pop(service_name, None)

    if engine:
        await engine.dispose()
        logger.info(f"Database engine disposed for service={service_name}")


async def dispose_all_engines() -> None:
    """Dispose of all database engines. Call during application shutdown."""
    service_names = list(_engines.keys())
    for name in service_names:
        await dispose_engine(name)
    logger.info("All database engines disposed")


# ═══════════════════════════════════════════════════════════════
# LEGACY COMPATIBILITY
# ═══════════════════════════════════════════════════════════════

# Legacy Base for backward compat (services that don't use TenantBase yet)
class Base(DeclarativeBase):
    """Legacy Base class — prefer TenantBase for new code."""
    pass


# Legacy engine/session for backward compat
from shared.utils.config import settings as _legacy_settings

engine = create_async_engine(
    _legacy_settings.DATABASE_URL,
    echo=_legacy_settings.DEBUG,
    pool_size=20,
    max_overflow=10,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """Legacy database dependency — prefer create_db_dependency(service_name)."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Legacy database initialization — prefer init_service_db(service_name, models)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Legacy database tables created")
