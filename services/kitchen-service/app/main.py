"""HotSot Kitchen Service — V2 Production-Grade Kitchen Orchestration.

Manages kitchen queues, priority scoring, batch cooking, staff assignment,
and throughput monitoring for 10,000+ vendor scale.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from shared.utils.config import get_settings
from shared.utils.database import init_service_db, get_session_factory
from shared.utils.redis_client import RedisClient
from shared.utils.kafka_client import KafkaProducer, KafkaConsumerManager
from shared.utils.observability import setup_tracing, setup_logging, HealthChecker
from shared.utils.middleware import (
    ErrorHandlingMiddleware,
    CorrelationIDMiddleware,
    RequestLoggingMiddleware,
    RateLimitMiddleware,
    TenantMiddleware,
)

from app.core.database import Base
from app.routes.kitchen import router as kitchen_router, set_dependencies
from app.routes.queue import router as queue_router
from app.routes.batch import router as batch_router
from app.core.kafka_handlers import start_consumers, stop_consumers

settings = get_settings("kitchen")
logger = setup_logging("kitchen-service")
redis_client = RedisClient()
kafka_producer = KafkaProducer("kitchen-service")
consumer_manager = KafkaConsumerManager("kitchen-service")
health_checker = HealthChecker("kitchen-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    # Startup
    await init_service_db("kitchen", Base.metadata)
    await redis_client.connect()
    session_factory = get_session_factory("kitchen")
    set_dependencies(session_factory, redis_client)
    await kafka_producer.start()
    await start_consumers()
    health_checker.mark_ready(True)
    logger.info("kitchen_service_started", database="hotsot_kitchen")

    yield

    # Shutdown
    await stop_consumers()
    await kafka_producer.stop()
    await redis_client.disconnect()
    health_checker.mark_ready(False)
    logger.info("kitchen_service_stopped")


app = FastAPI(
    title="HotSot Kitchen Service",
    version="2.0.0",
    lifespan=lifespan,
)

# Middleware stack (outermost first)
app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client, requests_per_minute=120)
app.add_middleware(TenantMiddleware)

# Tracing
setup_tracing("kitchen-service", app)

# Routes
app.include_router(kitchen_router, prefix="/kitchen", tags=["kitchen"])
app.include_router(queue_router, prefix="/queue", tags=["queue"])
app.include_router(batch_router, prefix="/batch", tags=["batch"])


@app.get("/health/live")
async def liveness():
    return health_checker.liveness()


@app.get("/health/ready")
async def readiness():
    db_ok = await health_checker.check_db("kitchen")
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(db_ok=db_ok, redis_ok=redis_ok)


@app.get("/health/startup")
async def startup():
    return health_checker.startup()
