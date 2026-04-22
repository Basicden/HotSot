"""HotSot Analytics Service — V2 Production-Grade."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI

from shared.utils.config import get_settings
from shared.utils.database import init_service_db, get_session_factory, dispose_engine
from shared.utils.redis_client import get_redis_client
from shared.utils.kafka_client import KafkaProducer
from shared.auth.jwt import setup_token_revocation
from shared.utils.observability import setup_tracing, setup_logging, create_health_router, setup_metrics
from shared.utils.middleware import setup_middleware

from app.core.database import ALL_MODELS
from app.routes.analytics import router as analytics_router, set_dependencies

SERVICE_NAME = "analytics"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup observability
    setup_logging(SERVICE_NAME)
    setup_tracing(SERVICE_NAME)
    logger.info(f"Starting {SERVICE_NAME} service...")

    # Initialize database
    try:
        await init_service_db(SERVICE_NAME, ALL_MODELS)
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

    # Initialize Redis
    redis_client = get_redis_client(SERVICE_NAME)
    try:
        await redis_client.connect()
        setup_token_revocation(redis_client)
        logger.info("Redis connected")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")

    # Initialize Kafka producer
    kafka_producer = KafkaProducer(service_name=SERVICE_NAME)
    try:
        await kafka_producer.start()
        logger.info("Kafka producer started")
    except Exception as e:
        logger.error(f"Kafka producer start failed: {e}")

    # Wire dependencies into routes
    session_factory = get_session_factory(SERVICE_NAME)
    set_dependencies(session_factory, redis_client, kafka_producer)
    logger.info("Dependencies wired")

    # Store in app state
    app.state.redis_client = redis_client
    app.state.kafka_producer = kafka_producer

    logger.info(f"{SERVICE_NAME} service started successfully")
    yield

    # Shutdown
    logger.info("Shutting down...")
    if kafka_producer:
        try:
            await kafka_producer.stop()
        except Exception as e:
            logger.warning(f"Error during shutdown: {e}")
    try:
        await redis_client.disconnect()
    except Exception as e:
        logger.warning(f"Error during shutdown: {e}")
    try:
        await dispose_engine(SERVICE_NAME)
    except Exception as e:
        logger.warning(f"Error during shutdown: {e}")
    logger.info("Service shut down cleanly")


app = FastAPI(title="HotSot Analytics Service", version="2.0.0", lifespan=lifespan)

# Middleware
setup_middleware(app, SERVICE_NAME)
setup_metrics(app, SERVICE_NAME)

# Routes
app.include_router(analytics_router, prefix="/analytics", tags=["analytics"])

# Health
health_router = create_health_router(SERVICE_NAME)
app.include_router(health_router)
