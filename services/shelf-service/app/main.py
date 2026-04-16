"""HotSot Shelf Service — V2 Production-Grade."""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from shared.utils.config import get_settings
from shared.utils.database import init_service_db
from shared.utils.redis_client import RedisClient
from shared.utils.kafka_client import KafkaProducer
from shared.utils.observability import setup_tracing, setup_logging, HealthChecker
from shared.utils.middleware import (
    ErrorHandlingMiddleware, CorrelationIDMiddleware,
    RequestLoggingMiddleware, RateLimitMiddleware, TenantMiddleware,
)

from app.core.database import Base
from app.core.ttl_worker import TTLWorker
from app.routes.shelf import router as shelf_router, set_dependencies

settings = get_settings("shelf")
logger = setup_logging("shelf-service")
redis_client = RedisClient()
kafka_producer = KafkaProducer("shelf-service")
health_checker = HealthChecker("shelf-service")
ttl_worker = TTLWorker(redis_client, kafka_producer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_service_db("shelf", Base.metadata)
    await redis_client.connect()
    await kafka_producer.start()

    # Wire session factory + redis client into route module
    from shared.utils.database import get_session_factory
    session_factory = get_session_factory("shelf")
    set_dependencies(session_factory, redis_client)

    # Start TTL background worker
    await ttl_worker.start()

    health_checker.mark_ready(True)
    logger.info("shelf_service_started")
    yield
    await ttl_worker.stop()
    await kafka_producer.stop()
    await redis_client.disconnect()
    health_checker.mark_ready(False)
    logger.info("shelf_service_stopped")


app = FastAPI(title="HotSot Shelf Service", version="2.0.0", lifespan=lifespan)

app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client, requests_per_minute=120)
app.add_middleware(TenantMiddleware)
setup_tracing("shelf-service", app)

app.include_router(shelf_router, prefix="/shelf", tags=["shelf"])


@app.get("/health/live")
async def liveness():
    return health_checker.liveness()


@app.get("/health/ready")
async def readiness():
    db_ok = await health_checker.check_db("shelf")
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(db_ok=db_ok, redis_ok=redis_ok)


@app.get("/health/startup")
async def startup():
    return health_checker.startup()
