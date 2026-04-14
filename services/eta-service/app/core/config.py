"""HotSot ETA Service — Configuration."""

import os
from dataclasses import dataclass


@dataclass
class Config:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/3")
    KAFKA_BOOTSTRAP: str = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    MODEL_PATH: str = os.getenv("MODEL_PATH", "/app/models/eta_model.txt")
    SERVICE_PORT: int = int(os.getenv("SERVICE_PORT", "8004"))
    BASE_PREP_SECONDS: int = 300
    PEAK_HOUR_MULTIPLIER: float = 1.5
    FESTIVAL_MULTIPLIER: float = 1.8
    MONSOON_MULTIPLIER: float = 1.3
    MIN_CONFIDENCE: float = 0.40
    MAX_CONFIDENCE: float = 0.99


config = Config()
