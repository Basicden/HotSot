"""HotSot Analytics Service — V2 Production-Grade."""

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
from app.routes.analytics import router as analytics_router

settings = get_settings("analytics")
logger = setup_logging("analytics-service")
redis_client = RedisClient()
kafka_producer = KafkaProducer("analytics-service")
health_checker = HealthChecker("analytics-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_service_db("analytics", Base.metadata)
    await redis_client.connect()
    await kafka_producer.start()
    health_checker.mark_ready(True)
    logger.info("analytics_service_started")
    yield
    await kafka_producer.stop()
    await redis_client.disconnect()
    health_checker.mark_ready(False)


app = FastAPI(title="HotSot Analytics Service", version="2.0.0", lifespan=lifespan)

app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client, requests_per_minute=120)
app.add_middleware(TenantMiddleware)
setup_tracing("analytics-service", app)

app.include_router(analytics_router, prefix="/analytics", tags=["analytics"])


@app.get("/health/live")
async def liveness():
    return health_checker.liveness()

@app.get("/health/ready")
async def readiness():
    db_ok = await health_checker.check_db("analytics")
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(db_ok=db_ok, redis_ok=redis_ok)

@app.get("/health/startup")
async def startup():
    return health_checker.startup()
