"""
HotSot Order Service — Main Application.

Production-grade FastAPI application with:
- Lifespan: init_db, init_redis, init_kafka_producer, start_kafka_consumers
- Middleware: Tenant, RequestLogging, CorrelationID, RateLimit, ErrorHandling
- Observability: OpenTelemetry tracing + structured logging
- Routes: orders, payments, admin
- Health: /health/live, /health/ready, /health/startup
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI

from shared.utils.config import get_settings
from shared.utils.database import init_service_db, get_session_factory, dispose_engine
from shared.utils.redis_client import get_redis_client
from shared.utils.kafka_client import KafkaProducer, KafkaConsumer
from shared.utils.observability import (
    setup_tracing,
    setup_logging,
    HealthStatus,
    create_health_router,
)
from shared.utils.middleware import setup_middleware

from app.core.database import ALL_MODELS
from app.core.kafka_handlers import handle_kafka_event, CONSUMER_TOPICS
from app.routes.orders import router as order_router
from app.routes.payments import router as payment_router
from app.routes.admin import router as admin_router

logger = logging.getLogger(__name__)

SERVICE_NAME = "order"


# ═══════════════════════════════════════════════════════════════
# HEALTH STATUS
# ═══════════════════════════════════════════════════════════════

health = HealthStatus(SERVICE_NAME)

# Track component readiness
_db_ready = False
_redis_ready = False
_kafka_producer_ready = False


async def check_db_health() -> bool:
    """Check if database is accessible."""
    try:
        session_factory = get_session_factory(SERVICE_NAME)
        async with session_factory() as session:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return False


async def check_redis_health() -> bool:
    """Check if Redis is accessible."""
    try:
        redis_client = get_redis_client(SERVICE_NAME)
        return (await redis_client.health_check())["status"] == "healthy"
    except Exception:
        return False


# Register health checks
health.add_check("database", check_db_health)
health.add_check("redis", check_redis_health)


# ═══════════════════════════════════════════════════════════════
# KAFKA CONSUMER TASKS
# ═══════════════════════════════════════════════════════════════

_consumer_tasks: list = []
_consumers: list = []


async def start_kafka_consumers() -> None:
    """Start Kafka consumer tasks for cross-service events."""
    settings = get_settings(SERVICE_NAME)

    try:
        consumer = KafkaConsumer(
            service_name=SERVICE_NAME,
            topics=CONSUMER_TOPICS,
            handler=handle_kafka_event,
            max_retries=3,
            auto_commit=False,  # Manual commit for at-least-once
        )
        await consumer.start()
        _consumers.append(consumer)

        # Start consumption loop as background task
        task = asyncio.create_task(consumer.consume())
        _consumer_tasks.append(task)

        logger.info(
            f"Kafka consumers started: topics={CONSUMER_TOPICS} "
            f"group={settings.KAFKA_CONSUMER_GROUP}"
        )
    except Exception as e:
        logger.error(f"Failed to start Kafka consumers: {e}")
        # Non-fatal: service can still handle API requests


async def stop_kafka_consumers() -> None:
    """Stop all Kafka consumers."""
    for task in _consumer_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    for consumer in _consumers:
        try:
            await consumer.stop()
        except Exception as e:
            logger.error(f"Error stopping Kafka consumer: {e}")

    _consumer_tasks.clear()
    _consumers.clear()
    logger.info("Kafka consumers stopped")


# ═══════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle manager.

    Startup:
    1. Setup observability (tracing + logging)
    2. Initialize database
    3. Initialize Redis
    4. Initialize Kafka producer
    5. Start Kafka consumers
    6. Mark service as ready

    Shutdown:
    1. Stop Kafka consumers
    2. Stop Kafka producer
    3. Disconnect Redis
    4. Dispose database engine
    """
    global _db_ready, _redis_ready, _kafka_producer_ready

    # ── STARTUP ──

    # 1. Setup observability
    setup_tracing(SERVICE_NAME)
    setup_logging(SERVICE_NAME)
    logger.info(f"Starting {SERVICE_NAME} service...")

    # 2. Initialize database
    try:
        await init_service_db(SERVICE_NAME, ALL_MODELS)
        _db_ready = True
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        # Try without specific models (create all)
        try:
            await init_service_db(SERVICE_NAME)
            _db_ready = True
            logger.info("Database initialized (all models)")
        except Exception as e2:
            logger.error(f"Database initialization completely failed: {e2}")

    # 3. Initialize Redis
    redis_client = get_redis_client(SERVICE_NAME)
    try:
        await redis_client.connect()
        _redis_ready = True
        logger.info("Redis connected")
    except Exception as e:
        logger.error(f"Redis connection failed: {e} (service will continue with degraded caching)")

    # 4. Initialize Kafka producer
    kafka_producer = KafkaProducer(service_name=SERVICE_NAME)
    try:
        await kafka_producer.start()
        _kafka_producer_ready = True
        logger.info("Kafka producer started")
    except Exception as e:
        logger.error(f"Kafka producer start failed: {e} (events will be logged to fallback)")
        kafka_producer = None

    # Store in app state for route dependencies
    app.state.redis_client = redis_client
    app.state.kafka_producer = kafka_producer

    # 5. Start Kafka consumers
    try:
        await start_kafka_consumers()
    except Exception as e:
        logger.error(f"Kafka consumer start failed: {e}")

    # 6. Mark service as ready
    health.mark_started()
    health.mark_ready()

    logger.info(
        f"Order service started: "
        f"db={'OK' if _db_ready else 'FAILED'} "
        f"redis={'OK' if _redis_ready else 'FAILED'} "
        f"kafka={'OK' if _kafka_producer_ready else 'FAILED'}"
    )

    yield

    # ── SHUTDOWN ──

    logger.info("Shutting down order service...")

    # Stop Kafka consumers
    await stop_kafka_consumers()

    # Stop Kafka producer
    if kafka_producer:
        try:
            await kafka_producer.stop()
        except Exception as e:
            logger.error(f"Error stopping Kafka producer: {e}")

    # Disconnect Redis
    if redis_client:
        try:
            await redis_client.disconnect()
        except Exception as e:
            logger.error(f"Error disconnecting Redis: {e}")

    # Dispose database engine
    try:
        await dispose_engine(SERVICE_NAME)
    except Exception as e:
        logger.error(f"Error disposing database engine: {e}")

    health.mark_not_ready()
    logger.info("Order service shut down cleanly")


# ═══════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="HotSot Order Service",
    version="2.0.0",
    description=(
        "Production-grade order lifecycle management for HotSot cloud kitchen platform. "
        "16-state order status, Saga pattern with choreography, Kafka event streaming, "
        "Razorpay payment integration with escrow, and multi-tenant isolation."
    ),
    lifespan=lifespan,
)


# ═══════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

# Get Redis client for rate limiting middleware
# (Note: at module level, Redis may not be connected yet — we pass the client reference)
redis_for_middleware = get_redis_client(SERVICE_NAME)
setup_middleware(app, SERVICE_NAME, redis_client=redis_for_middleware)


# ═══════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════

app.include_router(order_router, prefix="/orders", tags=["orders"])
app.include_router(payment_router, prefix="/payments", tags=["payments"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])


# ═══════════════════════════════════════════════════════════════
# HEALTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════

health_router = create_health_router(SERVICE_NAME, health)
app.include_router(health_router)
