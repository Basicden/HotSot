"""
HotSot Shared Library — Production-Grade Foundation for 18+ Microservices.

This package provides the shared infrastructure layer for the HotSot
pickup-first restaurant/food ordering system for Indian cloud kitchens
at 10,000+ vendor scale.

Modules:
    auth        — JWT authentication, API key management
    types       — Domain models (V1) and schemas (V2)
    utils       — Database, Redis, Kafka, config, observability, middleware, helpers
"""

__version__ = "2.0.0"
__author__ = "HotSot Engineering"
__status__ = "production"

# Semantic version components
VERSION_MAJOR = 2
VERSION_MINOR = 0
VERSION_PATCH = 0

# Feature flags for gradual rollout
FEATURES = {
    "v2_order_states": True,
    "tenant_isolation": True,
    "distributed_locking": True,
    "kafka_dlq": True,
    "otel_tracing": True,
    "api_key_auth": True,
}
