"""
HotSot Configuration — Environment-based settings per service.
"""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Base settings shared across all services."""

    # Service identity
    SERVICE_NAME: str = "hotsot"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    # PostgreSQL
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hotsot:hotsot_dev_password@localhost:5432/hotsot",
    )

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv(
        "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
    )
    KAFKA_CONSUMER_GROUP: str = "hotsot-consumer-group"

    # Shelf defaults
    SHELF_TTL_SECONDS: int = 600  # 10 minutes
    SHELF_LOCK_TIMEOUT: int = 30  # seconds for distributed lock

    # ETA defaults
    ETA_BASE_SECONDS: int = 300
    ETA_BUFFER_SECONDS: int = 45

    # Kitchen
    KITCHEN_OVERLOAD_THRESHOLD: float = 0.85  # 85% load = overload
    KITCHEN_MAX_QUEUE: int = 50

    # Observability
    ENABLE_METRICS: bool = True
    ENABLE_TRACING: bool = os.getenv("ENABLE_TRACING", "false").lower() == "true"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
