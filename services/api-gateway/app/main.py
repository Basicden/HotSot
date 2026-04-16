"""HotSot API Gateway — V2 Production-Grade Gateway for 18 Microservices."""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from shared.utils.observability import setup_tracing, setup_logging, HealthChecker
from shared.utils.middleware import (
    ErrorHandlingMiddleware, CorrelationIDMiddleware,
    RequestLoggingMiddleware, RateLimitMiddleware, TenantMiddleware,
)
from shared.utils.redis_client import RedisClient
from shared.auth.jwt import setup_token_revocation

from app.routes.proxy import router as proxy_router

logger = setup_logging("api-gateway")
redis_client = RedisClient()
health_checker = HealthChecker("api-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.connect()
    setup_token_revocation(redis_client)
    health_checker.mark_ready(True)
    logger.info("api_gateway_started")
    yield
    await redis_client.disconnect()
    health_checker.mark_ready(False)
    logger.info("api_gateway_stopped")


app = FastAPI(
    title="HotSot API Gateway",
    version="2.0.0",
    description="Production-grade gateway for 18 microservices",
    lifespan=lifespan,
)

# Middleware stack
app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RateLimitMiddleware, redis_client=redis_client, requests_per_minute=200)
app.add_middleware(TenantMiddleware)

setup_tracing("api-gateway", app)

app.include_router(proxy_router, tags=["gateway"])


@app.get("/health/live")
async def liveness():
    return health_checker.liveness()


@app.get("/health/ready")
async def readiness():
    redis_ok = await health_checker.check_redis(redis_client)
    return health_checker.readiness(redis_ok=redis_ok)


@app.get("/")
async def root():
    """Gateway info — lists all available services."""
    return {
        "service": "hotsot-api-gateway",
        "version": "2.0.0",
        "services": {
            "order": "Order lifecycle, payments, saga orchestration",
            "kitchen": "Kitchen queues, priority scoring, batch cooking",
            "shelf": "Shelf assignment, TTL management, expiry",
            "eta": "ETA prediction, kitchen baselines",
            "notification": "Multi-channel notifications (SMS/Push/WhatsApp/Email)",
            "realtime": "WebSocket (vendors) + SSE (customers)",
            "ml": "ML pipeline for ETA model training",
            "arrival": "GPS/QR arrival detection, geofencing",
            "compensation": "Refund/compensation engine",
            "vendor": "Vendor onboarding, verification, management",
            "menu": "Menu items, categories, availability",
            "search": "Full-text search across vendors/menu items",
            "pricing": "Dynamic pricing, surge, discounts",
            "compliance": "FSSAI, GST, DPDP, RBI compliance",
            "billing": "Invoicing, payouts, reconciliation",
            "analytics": "Event tracking, dashboards, metrics",
            "config": "Dynamic service configuration",
        },
        "total_services": 18,
    }
