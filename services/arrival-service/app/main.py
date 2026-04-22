"""HotSot Arrival Service — V2 Production-Grade."""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from shared.utils.config import get_settings
from shared.utils.database import init_service_db, get_session_factory
from shared.utils.redis_client import RedisClient
from shared.auth.jwt import setup_token_revocation
from shared.utils.kafka_client import KafkaProducer
from shared.utils.observability import setup_tracing, setup_logging, HealthChecker, setup_metrics
from shared.utils.middleware import (
    ErrorHandlingMiddleware, CorrelationIDMiddleware,
    RequestLoggingMiddleware, RateLimitMiddleware, TenantMiddleware,
)

from app.core.database import Base
from app.routes.arrival import router as arrival_router, set_dependencies

settings = get_settings("arrival")
logger = setup_logging("arrival-service")
redis_client = RedisClient()
kafka_producer = KafkaProducer("arrival-service")
health_checker = HealthChecker("arrival-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_service_db("arrival", Base.metadata)
    await redis_client.connect()
    setup_token_revocation(redis_client)
    session_factory = get_session_factory("arrival")
    set_dependencies(session_factory, redis_client, kafka_producer)
    await kafka_producer.start()
    health_checker.mark_ready(True)
    logger.info("arrival_service_started")
    yield
    await kafka_producer.stop()
    await redis_client.disconnect()
    health_checker.mark_ready(False)
    logger.info("arrival_service_stopped")


app = FastAPI(title="HotSot Arrival Service", version="2.0.0", lifespan=lifespan)

app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client, requests_per_minute=120)
app.add_middleware(TenantMiddleware)
setup_tracing("arrival-service", app)
setup_metrics(app, "arrival-service")

app.include_router(arrival_router, prefix="/arrival", tags=["arrival"])


@app.get("/health/live")
async def liveness():
    return health_checker.liveness()


@app.get("/health/ready")
async def readiness():
    db_ok = await health_checker.check_db("arrival")
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(db_ok=db_ok, redis_ok=redis_ok)


@app.get("/health/startup")
async def startup():
    return health_checker.startup()
