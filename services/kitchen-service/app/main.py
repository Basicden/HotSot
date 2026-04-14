"""HotSot Kitchen Service — Kitchen OS + Priority Scheduling."""

from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.core.database import init_db, close_db
from app.core.redis_client import init_redis, close_redis
from app.core.kafka_client import init_kafka, close_kafka
from app.routes.kitchen import router as kitchen_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_redis()
    await init_kafka()
    yield
    await close_kafka()
    await close_redis()
    await close_db()

app = FastAPI(title="HotSot Kitchen Service", version="1.0.0", lifespan=lifespan)

app.include_router(kitchen_router, prefix="/kitchen", tags=["kitchen"])


@app.get("/health")
async def health():
    return {"service": "kitchen-service", "status": "healthy"}
