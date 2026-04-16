"""HotSot Realtime Service — V2 SSE + WebSocket."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from shared.utils.config import get_settings
from shared.utils.redis_client import RedisClient
from shared.auth.jwt import setup_token_revocation
from shared.utils.observability import setup_tracing, setup_logging, HealthChecker
from shared.utils.middleware import (
    ErrorHandlingMiddleware, CorrelationIDMiddleware,
    RequestLoggingMiddleware, TenantMiddleware,
)
from app.routes.websocket import router as ws_router
from app.routes.sse import router as sse_router

settings = get_settings("realtime")
logger = setup_logging("realtime-service")
redis_client = RedisClient()
health_checker = HealthChecker("realtime-service")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.connect()
    setup_token_revocation(redis_client)
    health_checker.mark_ready(True)
    logger.info("realtime_service_started")
    yield
    await redis_client.disconnect()
    health_checker.mark_ready(False)

app = FastAPI(title="HotSot Realtime Service", version="2.0.0", lifespan=lifespan)
app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(TenantMiddleware)
setup_tracing("realtime-service", app)

app.include_router(ws_router, prefix="/ws", tags=["websocket"])
app.include_router(sse_router, prefix="/sse", tags=["sse"])

@app.get("/health/live")
async def liveness():
    return health_checker.liveness()
@app.get("/health/ready")
async def readiness():
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(redis_ok=redis_ok)
