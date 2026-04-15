"""HotSot Config Service — V2 Production-Grade."""

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
from app.routes.config import router as config_router

settings = get_settings("config")
logger = setup_logging("config-service")
redis_client = RedisClient()
kafka_producer = KafkaProducer("config-service")
health_checker = HealthChecker("config-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_service_db("config", Base.metadata)
    await redis_client.connect()
    await kafka_producer.start()
    health_checker.mark_ready(True)
    logger.info("config_service_started")
    yield
    await kafka_producer.stop()
    await redis_client.disconnect()
    health_checker.mark_ready(False)


app = FastAPI(title="HotSot Config Service", version="2.0.0", lifespan=lifespan)

app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client, requests_per_minute=120)
app.add_middleware(TenantMiddleware)
setup_tracing("config-service", app)

app.include_router(config_router, prefix="/config", tags=["config"])


@app.get("/health/live")
async def liveness():
    return health_checker.liveness()

@app.get("/health/ready")
async def readiness():
    db_ok = await health_checker.check_db("config")
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(db_ok=db_ok, redis_ok=redis_ok)

@app.get("/health/startup")
async def startup():
    return health_checker.startup()
