"""
HotSot Circuit Breaker — Production-grade resilience pattern.

Prevents cascade failures when Redis or external services are down.
Implements the four-state circuit breaker pattern:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Fail-fast, all requests are rejected immediately
    - HALF_OPEN: Limited requests allowed through to test recovery
    - STRESSED: Oscillation detected, adaptive backoff applied

Usage:
    from shared.circuit_breaker import CircuitBreaker, circuit_breaker

    # Decorator pattern
    @circuit_breaker("redis", failure_threshold=5, recovery_timeout=30)
    async def get_from_cache(key):
        return await redis.get(key)

    # Direct usage
    cb = CircuitBreaker("payment_gateway", failure_threshold=3, recovery_timeout=60)
    async with cb:
        result = await call_payment_api(...)

    # Granular per-service, per-dependency breakers
    from shared.circuit_breaker import ServiceCircuitBreakerManager
    mgr = ServiceCircuitBreakerManager()
    redis_cb = mgr.get_breaker("order-service", "redis")
    es_cb = mgr.get_breaker("order-service", "elasticsearch")
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Coroutine, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ═══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER STATES
# ═══════════════════════════════════════════════════════════════

class CircuitState(str, Enum):
    CLOSED = "CLOSED"          # Normal: requests flow through
    OPEN = "OPEN"              # Failing: requests are rejected immediately
    HALF_OPEN = "HALF_OPEN"    # Testing: limited requests allowed to probe
    STRESSED = "STRESSED"      # Oscillating: adaptive backoff applied


class CircuitOpenError(Exception):
    """
    Raised when the circuit breaker is OPEN and rejects a request.

    This is a fail-fast response — the caller should NOT retry
    but should use a fallback or return an error to the client.
    """

    def __init__(self, service_name: str, state: CircuitState, last_failure: Optional[float] = None):
        self.service_name = service_name
        self.state = state
        self.last_failure = last_failure
        recovery_in = ""
        if last_failure:
            recovery_in = f" Recovery attempt possible after cooldown."
        super().__init__(
            f"Circuit breaker OPEN for '{service_name}' — requests rejected (fail-fast).{recovery_in}"
        )


# ═══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER CONFIG
# ═══════════════════════════════════════════════════════════════

@dataclass
class CircuitBreakerConfig:
    """Per-dependency circuit breaker configuration.

    Allows granular configuration of circuit breaker behavior per
    (service, dependency) pair, so that e.g. Redis failures in the
    order-service don't degrade the entire system.

    Attributes:
        service_name: Name of the owning service (e.g., "order-service").
        dependency_name: Name of the dependency (e.g., "redis", "elasticsearch").
        failure_threshold: Consecutive failures before opening the circuit.
        recovery_timeout: Seconds to wait before HALF_OPEN probe.
        success_threshold: Consecutive successes in HALF_OPEN to close.
        half_open_max_requests: Max concurrent probes in HALF_OPEN state.
    """
    service_name: str
    dependency_name: str  # "redis", "elasticsearch", "payment_gateway", "kafka"
    failure_threshold: int = 5
    recovery_timeout: int = 30
    success_threshold: int = 2
    half_open_max_requests: int = 1  # Max concurrent probes in HALF_OPEN


# Default configurations per dependency type
DEPENDENCY_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "redis": {
        "failure_threshold": 5,
        "recovery_timeout": 30,
    },
    "elasticsearch": {
        "failure_threshold": 3,
        "recovery_timeout": 60,
    },
    "payment_gateway": {
        "failure_threshold": 3,
        "recovery_timeout": 120,
    },
    "kafka": {
        "failure_threshold": 5,
        "recovery_timeout": 30,
    },
}


# ═══════════════════════════════════════════════════════════════
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════

class CircuitBreaker:
    """
    Thread-safe async circuit breaker with four states.

    State transitions:
        CLOSED → OPEN: When failure_count >= failure_threshold
        OPEN → HALF_OPEN: After recovery_timeout seconds
        HALF_OPEN → CLOSED: When probe request succeeds (success_threshold times)
        HALF_OPEN → OPEN: When probe request fails
        OPEN/STRESSED → HALF_OPEN: After (effective) recovery_timeout seconds
        Any → STRESSED: When >5 transitions within 2*recovery_timeout seconds (oscillation)

    Key design decisions:
        - Fail-FAST: When OPEN, rejects immediately (no waiting)
        - Fail-CLOSED: Security-critical contexts reject on Redis down
        - Automatic recovery: Transitions to HALF_OPEN after timeout
        - Oscillation detection: STRESSED state doubles recovery_timeout
        - Half-open limiting: Only half_open_max_requests concurrent probes
        - Metrics: Tracks success/failure counts and timing

    Args:
        service_name: Name of the protected service (for logging/metrics).
        failure_threshold: Consecutive failures before opening (default: 5).
        recovery_timeout: Seconds to wait before HALF_OPEN probe (default: 30).
        success_threshold: Consecutive successes in HALF_OPEN to close (default: 2).
        half_open_max_requests: Max concurrent probes in HALF_OPEN (default: 1).
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        success_threshold: int = 2,
        half_open_max_requests: int = 1,
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.half_open_max_requests = half_open_max_requests

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change_time: float = time.monotonic()
        self._lock = asyncio.Lock()

        # HALF_OPEN concurrent probe tracking
        self._half_open_request_count = 0

        # Oscillation detection
        self._transition_count = 0
        self._transition_times: list[float] = []
        self._effective_recovery_timeout: float = float(recovery_timeout)
        self._oscillation_window: float = 2.0 * recovery_timeout  # 2× recovery_timeout

        # Metrics
        self._total_requests = 0
        self._total_failures = 0
        self._total_successes = 0
        self._total_rejected = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit breaker state (checks for automatic HALF_OPEN transition)."""
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            if time.monotonic() - self._last_failure_time >= self._effective_recovery_timeout:
                # Auto-transition to HALF_OPEN (will be confirmed on next request)
                return CircuitState.HALF_OPEN
        if self._state == CircuitState.STRESSED and self._last_failure_time is not None:
            if time.monotonic() - self._last_failure_time >= self._effective_recovery_timeout:
                # STRESSED also allows probe after (doubled) timeout
                return CircuitState.HALF_OPEN
        return self._state

    @property
    def metrics(self) -> Dict[str, Any]:
        """Get circuit breaker metrics for observability."""
        return {
            "service_name": self.service_name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure_time": self._last_failure_time,
            "total_requests": self._total_requests,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "total_rejected": self._total_rejected,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "effective_recovery_timeout": self._effective_recovery_timeout,
            "half_open_max_requests": self.half_open_max_requests,
            "half_open_request_count": self._half_open_request_count,
            "transition_count": self._transition_count,
            "is_stressed": self._state == CircuitState.STRESSED,
        }

    async def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state with logging and oscillation detection."""
        old_state = self._state
        self._state = new_state
        now = time.monotonic()
        self._last_state_change_time = now

        if old_state != new_state:
            # Track transition for oscillation detection
            self._transition_count += 1
            self._transition_times.append(now)

            # Prune transitions outside the oscillation window
            cutoff = now - self._oscillation_window
            self._transition_times = [t for t in self._transition_times if t >= cutoff]

            # Check for oscillation: >5 transitions within the window
            if len(self._transition_times) > 5 and self._state != CircuitState.STRESSED:
                self._state = CircuitState.STRESSED
                self._effective_recovery_timeout = self.recovery_timeout * 2.0
                logger.warning(
                    f"Circuit breaker OSCILLATION detected: service={self.service_name} "
                    f"— {len(self._transition_times)} transitions in "
                    f"{self._oscillation_window:.0f}s. Entering STRESSED state with "
                    f"doubled recovery_timeout={self._effective_recovery_timeout:.0f}s"
                )

            logger.warning(
                f"Circuit breaker state change: service={self.service_name} "
                f"{old_state.value} → {self._state.value}"
            )

    async def allow_request(self) -> bool:
        """
        Check if a request should be allowed through.

        Returns:
            True if request is allowed, False if circuit is OPEN.

        State transitions:
            - CLOSED: Always allows
            - OPEN: Rejects unless recovery_timeout has elapsed → HALF_OPEN
            - STRESSED: Rejects unless (doubled) recovery_timeout has elapsed → HALF_OPEN
            - HALF_OPEN: Allows up to half_open_max_requests concurrent probes
        """
        async with self._lock:
            current = self.state

            if current == CircuitState.CLOSED:
                return True

            if current in (CircuitState.OPEN, CircuitState.STRESSED):
                # Check if recovery timeout has elapsed (uses effective timeout for STRESSED)
                if self._last_failure_time and time.monotonic() - self._last_failure_time >= self._effective_recovery_timeout:
                    await self._transition_to(CircuitState.HALF_OPEN)
                    self._success_count = 0
                    self._half_open_request_count = 0
                    self._half_open_request_count += 1
                    return True  # Allow probe request
                return False  # Still in OPEN/STRESSED state, reject

            if current == CircuitState.HALF_OPEN:
                # Allow limited requests through for probing
                if self._half_open_request_count < self.half_open_max_requests:
                    self._half_open_request_count += 1
                    return True
                return False  # Max concurrent probes reached

            return False

    async def record_success(self) -> None:
        """Record a successful request."""
        async with self._lock:
            self._total_requests += 1
            self._total_successes += 1

            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    await self._transition_to(CircuitState.CLOSED)
                    self._failure_count = 0
                    self._success_count = 0
                    self._half_open_request_count = 0
                    # Reset adaptive backoff on recovery
                    self._effective_recovery_timeout = float(self.recovery_timeout)
                    self._transition_times.clear()
                    logger.info(
                        f"Circuit breaker CLOSED: service={self.service_name} "
                        f"(recovered after {self.success_threshold} successful probes)"
                    )
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0  # Reset failure count on success

    async def record_failure(self, error: Optional[Exception] = None) -> None:
        """Record a failed request."""
        async with self._lock:
            self._total_requests += 1
            self._total_failures += 1
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed — go back to OPEN (or STRESSED if oscillating)
                target = CircuitState.OPEN
                await self._transition_to(target)
                self._half_open_request_count = 0
                logger.warning(
                    f"Circuit breaker probe FAILED: service={self.service_name} "
                    f"→ back to {self._state.value}. Error: {error}"
                )
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    await self._transition_to(CircuitState.OPEN)
                    logger.error(
                        f"Circuit breaker OPENED: service={self.service_name} "
                        f"after {self._failure_count} consecutive failures. "
                        f"Error: {error}"
                    )

    async def call(self, func: Callable[..., Coroutine], *args: Any, **kwargs: Any) -> Any:
        """
        Execute a function through the circuit breaker.

        Args:
            func: Async function to execute.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            The function's return value.

        Raises:
            CircuitOpenError: If the circuit is OPEN and rejects the request.
        """
        if not await self.allow_request():
            self._total_rejected += 1
            raise CircuitOpenError(
                self.service_name, self.state, self._last_failure_time
            )

        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception as e:
            await self.record_failure(e)
            raise

    @asynccontextmanager
    async def protect(self):
        """
        Context manager for circuit breaker protection.

        Usage:
            async with breaker.protect():
                result = await redis.get("key")
        """
        if not await self.allow_request():
            self._total_rejected += 1
            raise CircuitOpenError(
                self.service_name, self.state, self._last_failure_time
            )

        try:
            yield
            await self.record_success()
        except Exception as e:
            await self.record_failure(e)
            raise

    async def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        async with self._lock:
            await self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0
            self._success_count = 0
            self._half_open_request_count = 0
            # Reset adaptive backoff
            self._effective_recovery_timeout = float(self.recovery_timeout)
            self._transition_times.clear()
            logger.info(f"Circuit breaker manually reset: service={self.service_name}")

    async def force_open(self) -> None:
        """Manually force the circuit breaker to OPEN state (maintenance mode)."""
        async with self._lock:
            await self._transition_to(CircuitState.OPEN)
            self._last_failure_time = time.monotonic()
            logger.info(f"Circuit breaker manually forced OPEN: service={self.service_name}")


# ═══════════════════════════════════════════════════════════════
# REGISTRY — Global circuit breaker instances
# ═══════════════════════════════════════════════════════════════

_breakers: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    service_name: str,
    failure_threshold: int = 5,
    recovery_timeout: int = 30,
) -> CircuitBreaker:
    """
    Get or create a circuit breaker for a service.

    Args:
        service_name: The service to protect.
        failure_threshold: Failures before opening.
        recovery_timeout: Seconds before recovery probe.

    Returns:
        CircuitBreaker instance.
    """
    if service_name not in _breakers:
        _breakers[service_name] = CircuitBreaker(
            service_name=service_name,
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )
    return _breakers[service_name]


def get_all_circuit_breakers() -> Dict[str, Dict[str, Any]]:
    """Get metrics for all registered circuit breakers."""
    return {name: cb.metrics for name, cb in _breakers.items()}


# ═══════════════════════════════════════════════════════════════
# DECORATOR
# ═══════════════════════════════════════════════════════════════

def circuit_breaker(
    service_name: str,
    failure_threshold: int = 5,
    recovery_timeout: int = 30,
):
    """
    Decorator to protect an async function with a circuit breaker.

    Usage:
        @circuit_breaker("redis", failure_threshold=5, recovery_timeout=30)
        async def get_from_cache(key):
            return await redis.get(key)

    When the circuit is OPEN, the decorated function raises CircuitOpenError
    instead of attempting the call.
    """
    def decorator(func: Callable[..., Coroutine]) -> Callable[..., Coroutine]:
        cb = get_circuit_breaker(service_name, failure_threshold, recovery_timeout)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await cb.call(func, *args, **kwargs)

        # Attach circuit breaker for inspection
        wrapper.circuit_breaker = cb  # type: ignore
        return wrapper

    return decorator


# ═══════════════════════════════════════════════════════════════
# SERVICE CIRCUIT BREAKER MANAGER
# ═══════════════════════════════════════════════════════════════

class ServiceCircuitBreakerManager:
    """
    Manages circuit breakers per (service, dependency) pair.

    Instead of sharing a single global "redis" breaker across all services,
    this manager creates separate breakers keyed by (service_name, dependency_name).
    For example, order-service has distinct breakers for "redis", "elasticsearch",
    and "payment_gateway" — so a Redis failure in order-service only degrades that
    specific connection, not the entire system.

    Dependency-type defaults are applied automatically:
        - redis:              failure_threshold=5,  recovery_timeout=30
        - elasticsearch:      failure_threshold=3,  recovery_timeout=60
        - payment_gateway:    failure_threshold=3,  recovery_timeout=120
        - kafka:              failure_threshold=5,  recovery_timeout=30

    Usage:
        mgr = ServiceCircuitBreakerManager()
        redis_cb = mgr.get_breaker("order-service", "redis")
        es_cb    = mgr.get_breaker("order-service", "elasticsearch")
        pay_cb   = mgr.get_breaker("order-service", "payment_gateway")

        # Custom config override
        from shared.circuit_breaker import CircuitBreakerConfig
        custom = CircuitBreakerConfig(
            service_name="order-service",
            dependency_name="redis",
            failure_threshold=10,
            recovery_timeout=60,
        )
        cb = mgr.get_breaker("order-service", "redis", config=custom)
    """

    def __init__(self) -> None:
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _make_key(service_name: str, dependency_name: str) -> str:
        """Create a unique key for the (service, dependency) pair."""
        return f"{service_name}:{dependency_name}"

    def _resolve_defaults(self, dependency_name: str) -> Dict[str, Any]:
        """Look up dependency-type defaults, falling back to sensible generic defaults."""
        return DEPENDENCY_DEFAULTS.get(
            dependency_name,
            {"failure_threshold": 5, "recovery_timeout": 30},
        )

    def get_breaker(
        self,
        service_name: str,
        dependency_name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ) -> CircuitBreaker:
        """
        Get or create a circuit breaker for a (service, dependency) pair.

        If a config is provided, its values take precedence. Otherwise, the
        manager auto-creates a breaker with sensible defaults per dependency type.

        Args:
            service_name: The owning service (e.g., "order-service").
            dependency_name: The dependency (e.g., "redis", "elasticsearch").
            config: Optional CircuitBreakerConfig to override defaults.

        Returns:
            CircuitBreaker instance for the given (service, dependency).
        """
        key = self._make_key(service_name, dependency_name)
        if key in self._breakers:
            return self._breakers[key]

        if config is not None:
            breaker = CircuitBreaker(
                service_name=f"{service_name}:{dependency_name}",
                failure_threshold=config.failure_threshold,
                recovery_timeout=config.recovery_timeout,
                success_threshold=config.success_threshold,
                half_open_max_requests=config.half_open_max_requests,
            )
        else:
            defaults = self._resolve_defaults(dependency_name)
            breaker = CircuitBreaker(
                service_name=f"{service_name}:{dependency_name}",
                failure_threshold=defaults.get("failure_threshold", 5),
                recovery_timeout=defaults.get("recovery_timeout", 30),
            )

        self._breakers[key] = breaker
        return breaker

    def get_all_metrics(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """
        Get metrics for all managed circuit breakers, grouped by service.

        Returns:
            Dict mapping service_name → {dependency_name → metrics_dict}.
            Example:
                {
                    "order-service": {
                        "redis": {"state": "CLOSED", ...},
                        "elasticsearch": {"state": "OPEN", ...},
                    },
                    "kitchen-service": {
                        "redis": {"state": "CLOSED", ...},
                    },
                }
        """
        result: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for key, breaker in self._breakers.items():
            # key format is "service_name:dependency_name"
            parts = key.split(":", 1)
            svc = parts[0] if len(parts) > 0 else key
            dep = parts[1] if len(parts) > 1 else "unknown"
            result.setdefault(svc, {})[dep] = breaker.metrics
        return result

    async def reset_all_for_service(self, service_name: str) -> None:
        """
        Reset all circuit breakers for a given service.

        Args:
            service_name: The service whose breakers should be reset.
        """
        for key, breaker in self._breakers.items():
            if key.startswith(f"{service_name}:"):
                await breaker.reset()
                logger.info(
                    f"Reset circuit breaker for service={service_name} "
                    f"key={key}"
                )
