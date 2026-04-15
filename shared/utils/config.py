"""
HotSot Configuration — Per-service environment-based settings.

Supports the database-per-service pattern where each microservice
gets its own database: hotsot_{service_name}.

All secrets come from environment variables — no hardcoded values.

Usage:
    from shared.utils.config import get_settings

    settings = get_settings("order-service")
    print(settings.DATABASE_URL)  # postgresql+asyncpg://hotsot:pass@host:5432/hotsot_order
"""

from __future__ import annotations

import os
import logging
from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import Field

logger = logging.getLogger(__name__)


# ─── Global Database Settings ───

_DB_USER: str = os.getenv("DB_USER", "hotsot")
_DB_PASSWORD: str = os.getenv("DB_PASSWORD", "hotsot_dev_pass")
_DB_HOST: str = os.getenv("DB_HOST", "localhost")
_DB_PORT: int = int(os.getenv("DB_PORT", "5432"))


def _build_database_url(service_name: str) -> str:
    """
    Build a per-service PostgreSQL URL.

    Pattern: postgresql+asyncpg://{user}:{password}@{host}:{port}/hotsot_{service_name}

    This enforces the database-per-service pattern for data isolation.
    """
    db_name = f"hotsot_{service_name}"
    return (
        f"postgresql+asyncpg://{_DB_USER}:{_DB_PASSWORD}@{_DB_HOST}:{_DB_PORT}/{db_name}"
    )


class ServiceSettings(BaseSettings):
    """
    Per-service configuration with all infrastructure settings.

    Each microservice creates its own instance via get_settings(service_name).
    """

    # ─── Service Identity ───
    SERVICE_NAME: str = "hotsot"
    ENVIRONMENT: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    DEBUG: bool = Field(default_factory=lambda: os.getenv("DEBUG", "true").lower() == "true")
    SERVICE_VERSION: str = Field(default_factory=lambda: os.getenv("SERVICE_VERSION", "2.0.0"))

    # ─── Database (per-service) ───
    DATABASE_URL: str = ""
    DB_POOL_SIZE: int = Field(default_factory=lambda: int(os.getenv("DB_POOL_SIZE", "20")))
    DB_MAX_OVERFLOW: int = Field(default_factory=lambda: int(os.getenv("DB_MAX_OVERFLOW", "10")))
    DB_POOL_RECYCLE: int = Field(default_factory=lambda: int(os.getenv("DB_POOL_RECYCLE", "3600")))
    DB_ECHO: bool = Field(default_factory=lambda: os.getenv("DB_ECHO", "false").lower() == "true")

    # ─── Redis ───
    REDIS_URL: str = Field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    REDIS_PASSWORD: Optional[str] = Field(default_factory=lambda: os.getenv("REDIS_PASSWORD", None))
    REDIS_MAX_CONNECTIONS: int = Field(default_factory=lambda: int(os.getenv("REDIS_MAX_CONNECTIONS", "50")))
    REDIS_SOCKET_TIMEOUT: float = Field(default_factory=lambda: float(os.getenv("REDIS_SOCKET_TIMEOUT", "5.0")))
    REDIS_SOCKET_CONNECT_TIMEOUT: float = Field(default_factory=lambda: float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5.0")))

    # ─── Kafka ───
    KAFKA_BOOTSTRAP_SERVERS: str = Field(
        default_factory=lambda: os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    )
    KAFKA_CONSUMER_GROUP: str = "hotsot-consumer-group"
    KAFKA_SECURITY_PROTOCOL: str = Field(
        default_factory=lambda: os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    )
    KAFKA_SASL_MECHANISM: Optional[str] = Field(
        default_factory=lambda: os.getenv("KAFKA_SASL_MECHANISM", None)
    )
    KAFKA_SASL_USERNAME: Optional[str] = Field(
        default_factory=lambda: os.getenv("KAFKA_SASL_USERNAME", None)
    )
    KAFKA_SASL_PASSWORD: Optional[str] = Field(
        default_factory=lambda: os.getenv("KAFKA_SASL_PASSWORD", None)
    )
    KAFKA_SCHEMA_REGISTRY_URL: Optional[str] = Field(
        default_factory=lambda: os.getenv("KAFKA_SCHEMA_REGISTRY_URL", None)
    )
    KAFKA_MAX_POLL_RECORDS: int = Field(
        default_factory=lambda: int(os.getenv("KAFKA_MAX_POLL_RECORDS", "100"))
    )
    KAFKA_SESSION_TIMEOUT_MS: int = Field(
        default_factory=lambda: int(os.getenv("KAFKA_SESSION_TIMEOUT_MS", "30000"))
    )

    # ─── Auth ───
    JWT_SECRET_KEY: str = Field(
        default_factory=lambda: os.getenv("JWT_SECRET_KEY", "")
    )
    JWT_ALGORITHM: str = Field(
        default_factory=lambda: os.getenv("JWT_ALGORITHM", "HS256")
    )
    JWT_ACCESS_EXPIRE_MINUTES: int = Field(
        default_factory=lambda: int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "60"))
    )
    JWT_REFRESH_EXPIRE_DAYS: int = Field(
        default_factory=lambda: int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7"))
    )
    JWT_ISSUER: str = Field(
        default_factory=lambda: os.getenv("JWT_ISSUER", "hotsot")
    )
    API_KEY_SALT: str = Field(
        default_factory=lambda: os.getenv("API_KEY_SALT", "")
    )

    # ─── Observability ───
    ENABLE_TRACING: bool = Field(
        default_factory=lambda: os.getenv("ENABLE_TRACING", "false").lower() == "true"
    )
    ENABLE_METRICS: bool = Field(
        default_factory=lambda: os.getenv("ENABLE_METRICS", "true").lower() == "true"
    )
    OTEL_EXPORTER_ENDPOINT: str = Field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_ENDPOINT", "http://localhost:4317")
    )
    OTEL_EXPORTER_PROTOCOL: str = Field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER_PROTOCOL", "grpc")
    )
    JAEGER_AGENT_HOST: str = Field(
        default_factory=lambda: os.getenv("JAEGER_AGENT_HOST", "localhost")
    )
    JAEGER_AGENT_PORT: int = Field(
        default_factory=lambda: int(os.getenv("JAEGER_AGENT_PORT", "6831"))
    )
    LOG_LEVEL: str = Field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )
    LOG_FORMAT: str = Field(
        default_factory=lambda: os.getenv("LOG_FORMAT", "json")
    )

    # ─── Payment Gateway (Razorpay) ───
    RAZORPAY_KEY_ID: str = Field(
        default_factory=lambda: os.getenv("RAZORPAY_KEY_ID", "")
    )
    RAZORPAY_KEY_SECRET: str = Field(
        default_factory=lambda: os.getenv("RAZORPAY_KEY_SECRET", "")
    )
    RAZORPAY_WEBHOOK_SECRET: str = Field(
        default_factory=lambda: os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    )
    RAZORPAY_API_BASE_URL: str = Field(
        default_factory=lambda: os.getenv(
            "RAZORPAY_API_BASE_URL", "https://api.razorpay.com/v1"
        )
    )

    # ─── Multi-tenancy ───
    TENANT_HEADER: str = Field(
        default_factory=lambda: os.getenv("TENANT_HEADER", "X-Tenant-ID")
    )
    DEFAULT_TENANT_ID: str = Field(
        default_factory=lambda: os.getenv("DEFAULT_TENANT_ID", "default")
    )
    ENFORCE_TENANT_ISOLATION: bool = Field(
        default_factory=lambda: os.getenv("ENFORCE_TENANT_ISOLATION", "true").lower() == "true"
    )

    # ─── Shelf Defaults ───
    SHELF_TTL_SECONDS: int = Field(
        default_factory=lambda: int(os.getenv("SHELF_TTL_SECONDS", "600"))
    )
    SHELF_LOCK_TIMEOUT: int = Field(
        default_factory=lambda: int(os.getenv("SHELF_LOCK_TIMEOUT", "30"))
    )

    # ─── ETA Defaults ───
    ETA_BASE_SECONDS: int = Field(
        default_factory=lambda: int(os.getenv("ETA_BASE_SECONDS", "300"))
    )
    ETA_BUFFER_SECONDS: int = Field(
        default_factory=lambda: int(os.getenv("ETA_BUFFER_SECONDS", "45"))
    )

    # ─── Kitchen ───
    KITCHEN_OVERLOAD_THRESHOLD: float = Field(
        default_factory=lambda: float(os.getenv("KITCHEN_OVERLOAD_THRESHOLD", "0.85"))
    )
    KITCHEN_MAX_QUEUE: int = Field(
        default_factory=lambda: int(os.getenv("KITCHEN_MAX_QUEUE", "50"))
    )

    # ─── Rate Limiting ───
    RATE_LIMIT_PER_MINUTE: int = Field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    )
    RATE_LIMIT_BURST: int = Field(
        default_factory=lambda: int(os.getenv("RATE_LIMIT_BURST", "10"))
    )

    # ─── Correlation ID ───
    CORRELATION_ID_HEADER: str = Field(
        default_factory=lambda: os.getenv("CORRELATION_ID_HEADER", "X-Correlation-ID")
    )

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
        "env_prefix": "",  # No prefix — direct env var names
    }


# ─── Settings Registry ───

_settings_cache: dict[str, ServiceSettings] = {}


def get_settings(service_name: str) -> ServiceSettings:
    """
    Get or create per-service settings.

    The database URL is automatically constructed using the
    database-per-service pattern: hotsot_{service_name}

    Args:
        service_name: The microservice identifier (e.g., "order", "kitchen", "shelf").

    Returns:
        ServiceSettings instance configured for the given service.
    """
    if service_name in _settings_cache:
        return _settings_cache[service_name]

    # Build the per-service database URL
    database_url = os.getenv("DATABASE_URL") or _build_database_url(service_name)

    # Build consumer group name from service name
    consumer_group = f"hotsot-{service_name}-group"

    settings = ServiceSettings(
        SERVICE_NAME=service_name,
        DATABASE_URL=database_url,
        KAFKA_CONSUMER_GROUP=consumer_group,
    )

    _settings_cache[service_name] = settings

    logger.info(
        f"Settings loaded for service={service_name} "
        f"env={settings.ENVIRONMENT} "
        f"db={database_url.split('@')[-1] if '@' in database_url else 'N/A'}"
    )

    return settings


# ─── Legacy Compatibility ───

class Settings(ServiceSettings):
    """
    Legacy settings class for backward compatibility.

    Prefer using get_settings(service_name) for new code.
    """
    def __init__(self, **kwargs):
        if "SERVICE_NAME" not in kwargs:
            kwargs["SERVICE_NAME"] = "hotsot"
        if "DATABASE_URL" not in kwargs:
            kwargs["DATABASE_URL"] = os.getenv(
                "DATABASE_URL",
                _build_database_url(kwargs.get("SERVICE_NAME", "hotsot")),
            )
        if "KAFKA_CONSUMER_GROUP" not in kwargs:
            kwargs["KAFKA_CONSUMER_GROUP"] = "hotsot-consumer-group"
        super().__init__(**kwargs)


# Legacy singleton (backward compat — prefer get_settings)
settings = Settings()
