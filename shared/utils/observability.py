"""
HotSot Observability — OpenTelemetry tracing, logging, metrics, health checks.

Features:
    - OpenTelemetry auto-instrumentation for FastAPI
    - Structured JSON logging with correlation IDs
    - Prometheus-compatible metrics (counters, histograms)
    - Health check endpoints for all services
    - Manual span creation for business logic

Usage:
    from shared.utils.observability import setup_tracing, setup_logging, create_health_router

    # In your main.py:
    setup_logging(service_name="order")
    tracer = setup_tracing(service_name="order", app=app)

    # Health check endpoint
    app.include_router(create_health_router(service_name="order"))
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# STRUCTURED LOGGING
# ═══════════════════════════════════════════════════════════════

class JSONFormatter(logging.Formatter):
    """
    Structured JSON log formatter for production.

    Outputs logs as single-line JSON objects with:
        - timestamp, level, logger, message
        - service_name, environment
        - correlation_id (if set in LogFilter)
        - extra fields from log record
    """

    def __init__(self, service_name: str = "hotsot", **kwargs):
        super().__init__()
        self.service_name = service_name
        self.environment = os.getenv("ENVIRONMENT", "development")

    def format(self, record: logging.LogRecord) -> str:
        import json as json_lib

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service_name,
            "environment": self.environment,
        }

        # Add correlation_id if present
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id:
            log_entry["correlation_id"] = correlation_id

        # Add tenant_id if present
        tenant_id = getattr(record, "tenant_id", None)
        if tenant_id:
            log_entry["tenant_id"] = tenant_id

        # Add any extra fields
        extra = getattr(record, "extra_fields", None)
        if extra and isinstance(extra, dict):
            log_entry.update(extra)

        # Add exception info
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Unknown",
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        return json_lib.dumps(log_entry, default=str)


class LogFilter(logging.Filter):
    """Filter that adds correlation_id to log records."""

    def __init__(self, service_name: str = "hotsot"):
        super().__init__()
        self.service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.service_name = self.service_name  # type: ignore[attr-defined]
        return True


def setup_logging(
    service_name: str = "hotsot",
    log_level: Optional[str] = None,
    log_format: Optional[str] = None,
) -> None:
    """
    Configure structured logging for a service.

    Args:
        service_name: The microservice identifier.
        log_level: Log level (DEBUG, INFO, WARNING, ERROR). Defaults to env LOG_LEVEL.
        log_format: Format type ("json" or "text"). Defaults to env LOG_FORMAT.
    """
    level = log_level or os.getenv("LOG_LEVEL", "INFO")
    fmt = log_format or os.getenv("LOG_FORMAT", "json")

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)

    if fmt == "json":
        handler.setFormatter(JSONFormatter(service_name=service_name))
    else:
        handler.setFormatter(
            logging.Formatter(
                f"%(asctime)s [{service_name}] %(levelname)s %(name)s: %(message)s"
            )
        )

    handler.addFilter(LogFilter(service_name=service_name))
    root_logger.addHandler(handler)

    # Reduce noise from third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)

    logger.info(f"Logging configured: service={service_name} level={level} format={fmt}")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the given name.

    Args:
        name: Logger name (usually module name).

    Returns:
        Configured Logger instance.
    """
    return logging.getLogger(name)


# ═══════════════════════════════════════════════════════════════
# TRACING
# ═══════════════════════════════════════════════════════════════

_tracer = None


def setup_tracing(
    service_name: str = "hotsot",
    app: Any = None,
    endpoint: Optional[str] = None,
):
    """
    Configure OpenTelemetry tracing for a service.

    In demo mode (no OTEL endpoint), returns a no-op tracer.

    Args:
        service_name: The microservice identifier.
        app: Optional FastAPI app instance for auto-instrumentation.
        endpoint: OTEL collector endpoint.

    Returns:
        Tracer instance (real or no-op).
    """
    global _tracer

    otel_endpoint = endpoint or os.getenv("OTEL_EXPORTER_ENDPOINT", "")
    enable_tracing = os.getenv("ENABLE_TRACING", "false").lower() == "true"

    if not enable_tracing or not otel_endpoint:
        logger.info(f"Tracing disabled for service={service_name} (demo mode)")
        _tracer = NoOpTracer(service_name)
        return _tracer

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        resource = Resource.create({
            "service.name": f"hotsot-{service_name}",
            "service.version": os.getenv("SERVICE_VERSION", "2.0.0"),
            "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        })

        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=otel_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _tracer = trace.get_tracer(f"hotsot.{service_name}")

        # Auto-instrument FastAPI if app provided
        if app:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
                FastAPIInstrumentor.instrument_app(app)
                logger.info(f"FastAPI auto-instrumented for service={service_name}")
            except ImportError:
                logger.warning("FastAPI instrumentation not available")

        logger.info(f"Tracing configured for service={service_name} endpoint={otel_endpoint}")
        return _tracer

    except ImportError:
        logger.warning("OpenTelemetry not installed — tracing in demo mode")
        _tracer = NoOpTracer(service_name)
        return _tracer
    except Exception as e:
        logger.warning(f"Tracing setup failed: {e} — using no-op tracer")
        _tracer = NoOpTracer(service_name)
        return _tracer


def get_tracer(service_name: str = "hotsot"):
    """
    Get the current tracer instance.

    Args:
        service_name: Fallback service name for no-op tracer.

    Returns:
        Tracer instance.
    """
    global _tracer
    if _tracer is None:
        _tracer = NoOpTracer(service_name)
    return _tracer


class NoOpTracer:
    """No-op tracer for demo/development mode."""

    def __init__(self, service_name: str = "hotsot"):
        self.service_name = service_name

    def start_as_current_span(self, name: str, **kwargs):
        """No-op span context manager."""
        return NoOpSpan()

    def start_span(self, name: str, **kwargs):
        """No-op span."""
        return NoOpSpan()


class NoOpSpan:
    """No-op span for demo/development mode."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[Dict] = None) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def end(self) -> None:
        pass


def trace(name: str):
    """
    Decorator for tracing async functions.

    Usage:
        @trace("process_order")
        async def process_order(order_id: str):
            ...

    Args:
        name: Span name.
    """
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args, **kwargs):
            tracer = get_tracer()
            with tracer.start_as_current_span(name) as span:
                span.set_attribute("function", func.__name__)
                try:
                    result = await func(*args, **kwargs)
                    return result
                except Exception as e:
                    span.set_attribute("error", True)
                    span.set_attribute("error.message", str(e))
                    raise
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════

_metrics_registry: Dict[str, Any] = {}


def get_counter(name: str, description: str = "", unit: str = "1"):
    """
    Get or create a counter metric.

    Args:
        name: Metric name (e.g., "orders_created_total").
        description: Metric description.
        unit: Metric unit.

    Returns:
        Counter instance.
    """
    if name not in _metrics_registry:
        _metrics_registry[name] = SimpleCounter(name, description)
    return _metrics_registry[name]


def get_histogram(name: str, description: str = "", unit: str = "ms"):
    """
    Get or create a histogram metric.

    Args:
        name: Metric name (e.g., "order_duration_ms").
        description: Metric description.
        unit: Metric unit.

    Returns:
        Histogram instance.
    """
    if name not in _metrics_registry:
        _metrics_registry[name] = SimpleHistogram(name, description)
    return _metrics_registry[name]


def record_metric(name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
    """
    Record a metric value.

    Args:
        name: Metric name.
        value: Metric value.
        labels: Optional labels for dimensional metrics.
    """
    metric = _metrics_registry.get(name)
    if metric:
        metric.record(value, labels)
    else:
        logger.debug(f"Metric not found: {name}")


class SimpleCounter:
    """Simple counter for demo mode."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._value = 0

    def increment(self, amount: int = 1, labels: Optional[Dict[str, str]] = None) -> None:
        self._value += amount

    def record(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        self._value += int(value)

    @property
    def value(self) -> int:
        return self._value


class SimpleHistogram:
    """Simple histogram for demo mode."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._values: List[float] = []

    def observe(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        self._values.append(value)

    def record(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        self._values.append(value)

    @property
    def count(self) -> int:
        return len(self._values)

    @property
    def sum(self) -> float:
        return sum(self._values)


# ═══════════════════════════════════════════════════════════════
# HEALTH CHECKS
# ═══════════════════════════════════════════════════════════════

@dataclass
class HealthStatus:
    """Health check result."""
    status: str = "healthy"  # healthy, degraded, unhealthy
    service: str = "hotsot"
    version: str = "2.0.0"
    environment: str = "development"
    checks: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class HealthChecker:
    """
    Production health checker for Kubernetes probes.

    Provides three probe endpoints:
    - Liveness (/health/live): Is the process alive?
    - Readiness (/health/ready): Is it ready to accept traffic?
    - Startup (/health/startup): Has it finished initialization?

    Usage:
        health_checker = HealthChecker("order-service")
        health_checker.mark_ready(True)

        @app.get("/health/live")
        async def liveness():
            return health_checker.liveness()

        @app.get("/health/ready")
        async def readiness():
            db_ok = await health_checker.check_db("order")
            redis_ok = await health_checker.check_redis(redis_client)
            return health_checker.readiness(db_ok=db_ok, redis_ok=redis_ok)
    """

    def __init__(self, service_name: str = "hotsot"):
        self._service_name = service_name
        self._ready = False
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._version = os.getenv("SERVICE_VERSION", "2.0.0")
        self._environment = os.getenv("ENVIRONMENT", "development")

    def mark_ready(self, ready: bool) -> None:
        """Mark the service as ready (or not) to accept traffic."""
        self._ready = ready
        logger.info(f"HealthChecker: service={self._service_name} ready={ready}")

    def liveness(self) -> Dict[str, Any]:
        """Return liveness status — is the process alive?"""
        return {
            "status": "alive",
            "service": self._service_name,
            "version": self._version,
            "uptime_since": self._started_at,
        }

    def readiness(self, db_ok: bool = True, redis_ok: bool = True, **extra_checks: bool) -> Dict[str, Any]:
        """Return readiness status — can the service accept traffic?"""
        checks = {
            "database": "healthy" if db_ok else "unhealthy",
            "redis": "healthy" if redis_ok else "unhealthy",
        }
        checks.update({k: "healthy" if v else "unhealthy" for k, v in extra_checks.items()})

        all_healthy = all(v == "healthy" for v in checks.values()) and self._ready

        return {
            "status": "ready" if all_healthy else "not_ready",
            "service": self._service_name,
            "version": self._version,
            "environment": self._environment,
            "ready": self._ready,
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def startup(self) -> Dict[str, Any]:
        """Return startup status — has initialization completed?"""
        return {
            "status": "started" if self._ready else "starting",
            "service": self._service_name,
            "version": self._version,
            "started_at": self._started_at,
        }

    async def check_db(self, service_name: str) -> bool:
        """Check database connectivity."""
        try:
            from shared.utils.database import get_engine
            engine = get_engine(service_name)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.warning(f"DB health check failed for {service_name}: {e}")
            return False

    async def check_redis(self, redis_client: Any) -> bool:
        """Check Redis connectivity."""
        try:
            if hasattr(redis_client, 'health_check'):
                result = await redis_client.health_check()
                return result.get("status") == "healthy"
            elif hasattr(redis_client, 'ping'):
                return await redis_client.ping()
            return False
        except Exception as e:
            logger.warning(f"Redis health check failed: {e}")
            return False


def create_health_router(service_name: str = "hotsot") -> APIRouter:
    """
    Create a FastAPI router with health check endpoints.

    Provides:
        - GET /health  — Full health status with component checks
        - GET /ready   — Readiness probe (K8s)
        - GET /live    — Liveness probe (K8s)

    Args:
        service_name: The microservice identifier.

    Returns:
        FastAPI APIRouter with health endpoints.
    """
    router = APIRouter(tags=["health"])

    @router.get("/health")
    async def health_check():
        """Full health check with component status."""
        from shared.utils.config import get_settings
        settings = get_settings(service_name)

        status = HealthStatus(
            service=f"hotsot-{service_name}",
            version=settings.SERVICE_VERSION,
            environment=settings.ENVIRONMENT,
        )

        # Check database connectivity
        try:
            from shared.utils.database import get_engine
            engine = get_engine(service_name)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            status.checks["database"] = {"status": "healthy"}
        except Exception as e:
            status.checks["database"] = {"status": "unhealthy", "error": str(e)}
            status.status = "degraded"

        # Check Redis connectivity
        try:
            from shared.utils.redis_client import get_redis_client
            redis = get_redis_client(service_name)
            health = await redis.health_check()
            status.checks["redis"] = health
            if health.get("status") != "healthy":
                status.status = "degraded"
        except Exception as e:
            status.checks["redis"] = {"status": "unavailable", "error": str(e)}
            status.status = "degraded"

        # Check Kafka connectivity (basic)
        status.checks["kafka"] = {"status": "not_checked", "note": "Kafka health deferred to consumer metrics"}

        return status.model_dump() if hasattr(status, 'model_dump') else status.__dict__

    @router.get("/ready")
    async def readiness_probe():
        """Readiness probe for Kubernetes."""
        return {"status": "ready", "service": service_name}

    @router.get("/live")
    async def liveness_probe():
        """Liveness probe for Kubernetes."""
        return {"status": "alive", "service": service_name}

    return router


# Import for health check
from sqlalchemy import text
