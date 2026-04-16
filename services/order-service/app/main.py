"""
HotSot Order Service — Main Application.

Production-grade FastAPI application with:
- Lifespan: init_db, init_redis, init_kafka_producer, start_kafka_consumers
- Middleware: Tenant, RequestLogging, CorrelationID, RateLimit, ErrorHandling
- Observability: OpenTelemetry tracing + structured logging
- Routes: orders, payments, admin
- Health: /health, /ready, /live
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
from shared.utils.kafka_client import KafkaProducer
from shared.utils.observability import (
    setup_tracing,
    setup_logging,
    create_health_router,
)
from shared.utils.middleware import setup_middleware

from app.core.database import ALL_MODELS
from app.core.kafka_handlers import CONSUMER_TOPICS
from app.routes.orders import router as order_router
from app.routes.payments import router as payment_router
from app.routes.admin import router as admin_router

logger = logging.getLogger(__name__)

SERVICE_NAME = "order"


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
    # ── STARTUP ──

    # 1. Setup observability
    setup_logging(SERVICE_NAME)
    setup_tracing(SERVICE_NAME)
    logger.info(f"Starting {SERVICE_NAME} service...")

    # 2. Initialize database
    try:
        await init_service_db(SERVICE_NAME, ALL_MODELS)
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        try:
            await init_service_db(SERVICE_NAME)
            logger.info("Database initialized (all models)")
        except Exception as e2:
            logger.error(f"Database initialization completely failed: {e2}")

    # 3. Initialize Redis
    redis_client = get_redis_client(SERVICE_NAME)
    try:
        await redis_client.connect()
        logger.info("Redis connected")
    except Exception as e:
        logger.error(f"Redis connection failed: {e} (service will continue with degraded caching)")

    # 4. Initialize Kafka producer
    kafka_producer = KafkaProducer(service_name=SERVICE_NAME)
    try:
        await kafka_producer.start()
        logger.info("Kafka producer started")
    except Exception as e:
        logger.error(f"Kafka producer start failed: {e} (events will be logged to fallback)")

    # Store in app state for route dependencies
    app.state.redis_client = redis_client
    app.state.kafka_producer = kafka_producer

    # 5. Start Kafka consumers (non-blocking)
    # KafkaConsumer is imported here to avoid circular imports
    try:
        from shared.utils.kafka_client import KafkaConsumer
        from app.core.kafka_handlers import handle_kafka_event

        settings = get_settings(SERVICE_NAME)
        consumer = KafkaConsumer(
            service_name=SERVICE_NAME,
            topics=CONSUMER_TOPICS,
            handler=handle_kafka_event,
            max_retries=3,
        )
        await consumer.start()
        app.state.kafka_consumer = consumer
        logger.info(f"Kafka consumers started: topics={CONSUMER_TOPICS}")
    except Exception as e:
        logger.error(f"Kafka consumer start failed: {e}")
        app.state.kafka_consumer = None

    logger.info(f"Order service started successfully")

    yield

    # ── SHUTDOWN ──

    logger.info("Shutting down order service...")

    # Stop Kafka consumer
    consumer = getattr(app.state, "kafka_consumer", None)
    if consumer:
        try:
            await consumer.stop()
        except Exception as e:
            logger.error(f"Error stopping Kafka consumer: {e}")

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

setup_middleware(app, SERVICE_NAME)


# ═══════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════

app.include_router(order_router, prefix="/orders", tags=["orders"])
app.include_router(payment_router, prefix="/payments", tags=["payments"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])


# ═══════════════════════════════════════════════════════════════
# HEALTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════

health_router = create_health_router(SERVICE_NAME)
app.include_router(health_router)
