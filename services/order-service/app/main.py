"""HotSot Order Service — Main Application."""

from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.core.database import init_db, close_db
from app.core.kafka_client import init_kafka, close_kafka
from app.core.redis_client import init_redis, close_redis
from app.routes.orders import router as order_router
from app.routes.admin import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    # Startup
    await init_db()
    await init_redis()
    await init_kafka()
    print("[OrderService] Started successfully")
    yield
    # Shutdown
    await close_kafka()
    await close_redis()
    await close_db()
    print("[OrderService] Shut down cleanly")


app = FastAPI(
    title="HotSot Order Service",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(order_router, prefix="/orders", tags=["orders"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])


@app.get("/health")
async def health():
    return {"service": "order-service", "status": "healthy"}
