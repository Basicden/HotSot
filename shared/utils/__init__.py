"""
HotSot Shared Utilities — Infrastructure layer for all microservices.

Provides:
    config          — Per-service configuration with environment-based settings
    database        — Database-per-service pattern with multi-tenancy (tenant_id on every model)
    redis_client    — Production Redis with distributed locking (fail-CLOSED), pub/sub, caching
    kafka_client    — Production Kafka with exactly-once producer, manual-commit consumer, DLQ
    observability   — OpenTelemetry tracing, structured logging, metrics, health checks
    middleware      — FastAPI middleware stack (tenant, logging, correlation, rate limit, error)
    helpers         — Utility functions (IDs, timestamps, type conversion, idempotency, timer)
"""

# Config
from shared.utils.config import get_settings, ServiceSettings

# Database
from shared.utils.database import (
    TenantBase,
    BaseModel,
    get_engine,
    get_session_factory,
    init_service_db,
    set_tenant_id,
    create_db_dependency,
    dispose_engine,
    dispose_all_engines,
    # Legacy
    Base,
    engine,
    async_session,
    get_db,
    init_db,
)

# Redis
from shared.utils.redis_client import (
    RedisClient,
    get_redis_client,
)

# Kafka
from shared.utils.kafka_client import (
    KafkaProducer,
    KafkaConsumer,
    KafkaConsumerManager,
    EventPublisher,
    event_publisher,
    serialize_event,
    deserialize_event,
    ensure_topics_exist,
)

# Observability
from shared.utils.observability import (
    setup_tracing,
    setup_logging,
    get_tracer,
    get_logger,
    trace,
    get_counter,
    get_histogram,
    record_metric,
    HealthStatus,
    create_health_router,
)

# Middleware
from shared.utils.middleware import (
    TenantMiddleware,
    RequestLoggingMiddleware,
    CorrelationIDMiddleware,
    RateLimitMiddleware,
    ErrorHandlingMiddleware,
    setup_middleware,
)

# Helpers
from shared.utils.helpers import (
    generate_id,
    generate_short_id,
    generate_compensation_id,
    now_ts,
    now_iso,
    now_iso_compact,
    parse_iso_timestamp,
    idempotency_key,
    arrival_dedup_key,
    items_hash,
    safe_float,
    safe_int,
    safe_str,
    clamp,
    safe_divide,
    truncate_dict,
    flatten_dict,
    Timer,
    timed,
    mask_sensitive,
    truncate_string,
    percentage,
    moving_average,
)

__all__ = [
    # Config
    "get_settings",
    "ServiceSettings",
    # Database
    "TenantBase",
    "BaseModel",
    "get_engine",
    "get_session_factory",
    "init_service_db",
    "set_tenant_id",
    "create_db_dependency",
    "dispose_engine",
    "dispose_all_engines",
    "Base",
    "engine",
    "async_session",
    "get_db",
    "init_db",
    # Redis
    "RedisClient",
    "get_redis_client",
    # Kafka
    "KafkaProducer",
    "KafkaConsumer",
    "KafkaConsumerManager",
    "EventPublisher",
    "event_publisher",
    "serialize_event",
    "deserialize_event",
    "ensure_topics_exist",
    # Observability
    "setup_tracing",
    "setup_logging",
    "get_tracer",
    "get_logger",
    "trace",
    "get_counter",
    "get_histogram",
    "record_metric",
    "HealthStatus",
    "create_health_router",
    # Middleware
    "TenantMiddleware",
    "RequestLoggingMiddleware",
    "CorrelationIDMiddleware",
    "RateLimitMiddleware",
    "ErrorHandlingMiddleware",
    "setup_middleware",
    # Helpers
    "generate_id",
    "generate_short_id",
    "generate_compensation_id",
    "now_ts",
    "now_iso",
    "now_iso_compact",
    "parse_iso_timestamp",
    "idempotency_key",
    "arrival_dedup_key",
    "items_hash",
    "safe_float",
    "safe_int",
    "safe_str",
    "clamp",
    "safe_divide",
    "truncate_dict",
    "flatten_dict",
    "Timer",
    "timed",
    "mask_sensitive",
    "truncate_string",
    "percentage",
    "moving_average",
]
