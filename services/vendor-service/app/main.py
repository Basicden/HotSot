"""HotSot Vendor Service — V2 Production-Grade."""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from shared.utils import (
    setup_logging,
    setup_tracing,
    setup_middleware,
    get_session_factory,
    init_service_db,
    dispose_engine,
    create_health_router,
    RedisClient,
    KafkaProducer,
)

from app.core.database import VendorModel, VendorDocumentModel
from app.routes.vendor import router as vendor_router, set_dependencies

SERVICE_NAME = "vendor"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup observability
    setup_logging(f"{SERVICE_NAME}-service")
    setup_tracing(f"{SERVICE_NAME}-service", app)

    # Initialize database
    session_factory = get_session_factory(SERVICE_NAME)
    await init_service_db(SERVICE_NAME, [VendorModel, VendorDocumentModel])

    # Connect Redis
    redis_client = RedisClient(service_name=SERVICE_NAME)
    await redis_client.connect()

    # Connect Kafka
    kafka_producer = KafkaProducer(service_name=f"{SERVICE_NAME}-service")
    await kafka_producer.start()

    # Wire dependencies into routes
    set_dependencies(session_factory, redis_client, kafka_producer)

    yield

    # Cleanup
    await kafka_producer.stop()
    await redis_client.disconnect()
    await dispose_engine(SERVICE_NAME)


app = FastAPI(title="HotSot Vendor Service", version="2.0.0", lifespan=lifespan)

# Middleware
setup_middleware(app, SERVICE_NAME)

# Health checks
app.include_router(create_health_router(SERVICE_NAME))

# Business routes
app.include_router(vendor_router, prefix="/vendor", tags=["vendor"])
