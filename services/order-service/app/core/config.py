"""HotSot Order Service — Core Configuration."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    """Service configuration loaded from environment variables."""

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://hotsot:hotsot_dev_pass@localhost:5432/hotsot"
    )

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Kafka
    KAFKA_BOOTSTRAP: str = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    KAFKA_GROUP_ID: str = "order-service-group"

    # Service
    SERVICE_PORT: int = int(os.getenv("SERVICE_PORT", "8001"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Order defaults
    DEFAULT_TTL_SECONDS: int = 600
    MAX_ORDERS_PER_KITCHEN: int = 200

    # Payment
    PAYMENT_TIMEOUT_SECONDS: int = 120
    UPI_CALLBACK_URL: str = os.getenv("UPI_CALLBACK_URL", "http://localhost:8001/orders/payment-callback")


config = Config()
