"""HotSot Search Service — V2 Production-Grade with Elasticsearch."""

from contextlib import asynccontextmanager
from fastapi import FastAPI

from shared.utils import (
    setup_logging,
    setup_tracing,
    setup_middleware,
    setup_metrics,
    get_session_factory,
    init_service_db,
    dispose_engine,
    create_health_router,
    RedisClient,
    KafkaProducer,
)
from shared.auth.jwt import setup_token_revocation

from app.core.database import SearchIndexModel
from app.core.elasticsearch_engine import ElasticSearchEngine
from app.routes.search import router as search_router, set_dependencies

SERVICE_NAME = "search"

# Global ES engine instance
_es_engine = ElasticSearchEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup observability
    setup_logging(f"{SERVICE_NAME}-service")
    setup_tracing(f"{SERVICE_NAME}-service", app)

    # Initialize database
    session_factory = get_session_factory(SERVICE_NAME)
    await init_service_db(SERVICE_NAME, [SearchIndexModel])

    # Connect Redis
    redis_client = RedisClient(service_name=SERVICE_NAME)
    await redis_client.connect()
    setup_token_revocation(redis_client)

    # Connect Kafka
    kafka_producer = KafkaProducer(service_name=f"{SERVICE_NAME}-service")
    await kafka_producer.start()

    # Start Elasticsearch engine (falls back to PostgreSQL if unavailable)
    await _es_engine.start()
    if _es_engine.is_available:
        app.state.es_engine = _es_engine
    else:
        app.state.es_engine = None

    # Wire dependencies into routes
    set_dependencies(session_factory, redis_client, kafka_producer, _es_engine)

    yield

    # Cleanup
    await _es_engine.stop()
    await kafka_producer.stop()
    await redis_client.disconnect()
    await dispose_engine(SERVICE_NAME)


app = FastAPI(title="HotSot Search Service", version="2.0.0", lifespan=lifespan)

# Middleware
setup_middleware(app, SERVICE_NAME)
setup_metrics(app, SERVICE_NAME)

# Health checks
app.include_router(create_health_router(SERVICE_NAME))

# Business routes
app.include_router(search_router, prefix="/search", tags=["search"])
