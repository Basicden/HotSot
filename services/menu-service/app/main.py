"""HotSot Menu Service — V2 Production-Grade."""

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
from app.routes.menu import router as menu_router

settings = get_settings("menu")
logger = setup_logging("menu-service")
redis_client = RedisClient()
kafka_producer = KafkaProducer("menu-service")
health_checker = HealthChecker("menu-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_service_db("menu", Base.metadata)
    await redis_client.connect()
    await kafka_producer.start()
    health_checker.mark_ready(True)
    logger.info("menu_service_started")
    yield
    await kafka_producer.stop()
    await redis_client.disconnect()
    health_checker.mark_ready(False)


app = FastAPI(title="HotSot Menu Service", version="2.0.0", lifespan=lifespan)

app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client, requests_per_minute=120)
app.add_middleware(TenantMiddleware)
setup_tracing("menu-service", app)

app.include_router(menu_router, prefix="/menu", tags=["menu"])


@app.get("/health/live")
async def liveness():
    return health_checker.liveness()

@app.get("/health/ready")
async def readiness():
    db_ok = await health_checker.check_db("menu")
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(db_ok=db_ok, redis_ok=redis_ok)

@app.get("/health/startup")
async def startup():
    return health_checker.startup()
