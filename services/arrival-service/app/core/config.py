"""
HotSot Arrival Service — Configuration
========================================
All tuneable knobs live here.  Every value can be overridden via an
environment variable of the same name (case-insensitive).
"""

from __future__ import annotations

import os
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Service ────────────────────────────────────────────────────────
    service_name: str = "arrival-service"
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ── Redis ──────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        alias="REDIS_URL",
    )
    # TTL for dedup keys (seconds) — slightly longer than the 30 s window
    redis_dedup_ttl: int = Field(default=60, alias="REDIS_DEDUP_TTL")
    # TTL for active-arrival tracking keys (seconds)
    redis_arrival_ttl: int = Field(default=1800, alias="REDIS_ARRIVAL_TTL")

    # ── Kafka ──────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        alias="KAFKA_BOOTSTRAP_SERVERS",
    )
    kafka_arrival_topic: str = Field(
        default="hotsot.arrival.events.v1",
        alias="KAFKA_ARRIVAL_TOPIC",
    )
    kafka_staff_notification_topic: str = Field(
        default="hotsot.staff.notifications.v1",
        alias="KAFKA_STAFF_NOTIFICATION_TOPIC",
    )
    kafka_priority_topic: str = Field(
        default="hotsot.priority.boosts.v1",
        alias="KAFKA_PRIORITY_TOPIC",
    )

    # ── Geo / GPS ─────────────────────────────────────────────────────
    # Maximum distance (metres) to consider a GPS arrival valid
    arrival_radius_m: float = Field(default=150.0, alias="ARRIVAL_RADIUS_M")

    # ── QR Token ──────────────────────────────────────────────────────
    # QR tokens rotate every N seconds
    qr_token_rotation_s: int = Field(default=30, alias="QR_TOKEN_ROTATION_S")
    qr_token_secret: str = Field(
        default="change-me-in-production",
        alias="QR_TOKEN_SECRET",
    )

    # ── Priority Boost ────────────────────────────────────────────────
    priority_boost_amount: int = Field(default=20, alias="PRIORITY_BOOST_AMOUNT")

    # ── Dedup ─────────────────────────────────────────────────────────
    # Bucket width for timestamp hashing (seconds) — must match QR rotation
    dedup_window_s: int = Field(default=30, alias="DEDUP_WINDOW_S")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "populate_by_name": True,
        "extra": "ignore",
    }


def get_settings() -> Settings:
    """Return a cached Settings singleton."""
    return Settings()
