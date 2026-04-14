"""
Compensation Service Configuration
-----------------------------------
Environment-driven settings for the HotSot Compensation microservice.
"""

from pydantic_settings import BaseSettings
from enum import Enum


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Service identity ───────────────────────────────────────
    SERVICE_NAME: str = "compensation-service"
    VERSION: str = "1.0.0"
    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    DEBUG: bool = True

    # ── Server ─────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8004

    # ── Redis ──────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_COMPENSATION_PREFIX: str = "hotsot:compensation:"
    REDIS_INCIDENT_PREFIX: str = "hotsot:incident:"
    REDIS_PENALTY_PREFIX: str = "hotsot:penalty:"
    REDIS_IDEMPOTENCY_PREFIX: str = "hotsot:idempotency:"

    # ── Kafka ──────────────────────────────────────────────────
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_COMPENSATION_TOPIC: str = "hotsot.compensation.events.v1"
    KAFKA_INCIDENT_TOPIC: str = "hotsot.incident.events.v1"
    KAFKA_CONSUMER_GROUP: str = "compensation-service-group"
    KAFKA_CLIENT_ID: str = "compensation-service"

    # ── Compensation Engine ────────────────────────────────────
    DELAY_THRESHOLD_MINUTES: int = 15
    CREDIT_NOTE_MIN_AMOUNT: float = 50.0
    CREDIT_NOTE_MAX_AMOUNT: float = 100.0
    SHELF_EXPIRATION_PENALTY_THRESHOLD: int = 3  # per kitchen per day
    REFUND_PROCESSING_TIMEOUT_SECONDS: int = 30

    # ── Incident Tracker ──────────────────────────────────────
    INCIDENT_ACK_TIMEOUT_SECONDS: int = 300  # 5 minutes
    INCIDENT_AUTO_ESCALATION_ENABLED: bool = True

    # ── UPI Refund (simulated) ────────────────────────────────
    UPI_REFUND_API_URL: str = "https://api.upi.simulated/refund"
    UPI_MERCHANT_ID: str = "HOTSOT_MERCHANT_001"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


settings = Settings()
