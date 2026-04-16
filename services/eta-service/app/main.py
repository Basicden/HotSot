"""HotSot ETA Service — V2 Production-Grade."""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from shared.utils.config import get_settings
from shared.utils.database import init_service_db
from shared.utils.redis_client import RedisClient
from shared.auth.jwt import setup_token_revocation
from shared.utils.kafka_client import KafkaProducer
from shared.utils.observability import setup_tracing, setup_logging, HealthChecker
from shared.utils.middleware import (
    ErrorHandlingMiddleware, CorrelationIDMiddleware,
    RequestLoggingMiddleware, RateLimitMiddleware, TenantMiddleware,
)

from app.core.database import Base
from app.routes.eta import router as eta_router

settings = get_settings("eta")
logger = setup_logging("eta-service")
redis_client = RedisClient()
kafka_producer = KafkaProducer("eta-service")
health_checker = HealthChecker("eta-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_service_db("eta", Base.metadata)
    await redis_client.connect()
    setup_token_revocation(redis_client)
    await kafka_producer.start()
    health_checker.mark_ready(True)
    logger.info("eta_service_started")
    yield
    await kafka_producer.stop()
    await redis_client.disconnect()
    health_checker.mark_ready(False)
    logger.info("eta_service_stopped")


app = FastAPI(title="HotSot ETA Service", version="2.0.0", lifespan=lifespan)

app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client, requests_per_minute=120)
app.add_middleware(TenantMiddleware)
setup_tracing("eta-service", app)

app.include_router(eta_router, prefix="/eta", tags=["eta"])


@app.get("/health/live")
async def liveness():
    return health_checker.liveness()


@app.get("/health/ready")
async def readiness():
    db_ok = await health_checker.check_db("eta")
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(db_ok=db_ok, redis_ok=redis_ok)


@app.get("/health/startup")
async def startup():
    return health_checker.startup()
