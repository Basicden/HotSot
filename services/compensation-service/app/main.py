"""HotSot Compensation Service — V2 Production-Grade."""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from shared.utils.config import get_settings
from shared.utils.database import init_service_db, get_session_factory
from shared.utils.redis_client import RedisClient
from shared.utils.kafka_client import KafkaProducer
from shared.utils.observability import setup_tracing, setup_logging, HealthChecker
from shared.utils.middleware import (
    ErrorHandlingMiddleware, CorrelationIDMiddleware,
    RequestLoggingMiddleware, RateLimitMiddleware, TenantMiddleware,
)

from app.core.database import Base
from app.routes.compensation import router as compensation_router, set_dependencies

settings = get_settings("compensation")
logger = setup_logging("compensation-service")
redis_client = RedisClient()
kafka_producer = KafkaProducer("compensation-service")
health_checker = HealthChecker("compensation-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_service_db("compensation", Base.metadata)
    await redis_client.connect()
    session_factory = get_session_factory("compensation")
    set_dependencies(session_factory, redis_client, kafka_producer)
    await kafka_producer.start()
    health_checker.mark_ready(True)
    logger.info("compensation_service_started")
    yield
    await kafka_producer.stop()
    await redis_client.disconnect()
    health_checker.mark_ready(False)


app = FastAPI(title="HotSot Compensation Service", version="2.0.0", lifespan=lifespan)

app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client, requests_per_minute=120)
app.add_middleware(TenantMiddleware)
setup_tracing("compensation-service", app)

app.include_router(compensation_router, prefix="/compensation", tags=["compensation"])


@app.get("/health/live")
async def liveness():
    return health_checker.liveness()

@app.get("/health/ready")
async def readiness():
    db_ok = await health_checker.check_db("compensation")
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(db_ok=db_ok, redis_ok=redis_ok)

@app.get("/health/startup")
async def startup():
    return health_checker.startup()
