"""
HotSot Circuit Breaker — Production-grade resilience pattern.

Prevents cascade failures when Redis or external services are down.
Implements the three-state circuit breaker pattern:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Fail-fast, all requests are rejected immediately
    - HALF_OPEN: One request allowed through to test recovery

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
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
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
    HALF_OPEN = "HALF_OPEN"    # Testing: one request allowed to probe


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
# CIRCUIT BREAKER
# ═══════════════════════════════════════════════════════════════

class CircuitBreaker:
    """
    Thread-safe async circuit breaker with three states.

    State transitions:
        CLOSED → OPEN: When failure_count >= failure_threshold
        OPEN → HALF_OPEN: After recovery_timeout seconds
        HALF_OPEN → CLOSED: When probe request succeeds
        HALF_OPEN → OPEN: When probe request fails

    Key design decisions:
        - Fail-FAST: When OPEN, rejects immediately (no waiting)
        - Fail-CLOSED: Security-critical contexts reject on Redis down
        - Automatic recovery: Transitions to HALF_OPEN after timeout
        - Metrics: Tracks success/failure counts and timing

    Args:
        service_name: Name of the protected service (for logging/metrics).
        failure_threshold: Consecutive failures before opening (default: 5).
        recovery_timeout: Seconds to wait before HALF_OPEN probe (default: 30).
        success_threshold: Consecutive successes in HALF_OPEN to close (default: 2).
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        success_threshold: int = 2,
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change_time: float = time.monotonic()
        self._lock = asyncio.Lock()

        # Metrics
        self._total_requests = 0
        self._total_failures = 0
        self._total_successes = 0
        self._total_rejected = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit breaker state (checks for automatic HALF_OPEN transition)."""
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                # Auto-transition to HALF_OPEN (will be confirmed on next request)
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
        }

    async def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state with logging."""
        old_state = self._state
        self._state = new_state
        self._last_state_change_time = time.monotonic()

        if old_state != new_state:
            logger.warning(
                f"Circuit breaker state change: service={self.service_name} "
                f"{old_state.value} → {new_state.value}"
            )

    async def allow_request(self) -> bool:
        """
        Check if a request should be allowed through.

        Returns:
            True if request is allowed, False if circuit is OPEN.

        State transitions:
            - CLOSED: Always allows
            - OPEN: Rejects unless recovery_timeout has elapsed → HALF_OPEN
            - HALF_OPEN: Allows one probe request
        """
        async with self._lock:
            current = self.state

            if current == CircuitState.CLOSED:
                return True

            if current == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                if self._last_failure_time and time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                    await self._transition_to(CircuitState.HALF_OPEN)
                    self._success_count = 0
                    return True  # Allow probe request
                return False  # Still in OPEN state, reject

            if current == CircuitState.HALF_OPEN:
                # Allow limited requests through for probing
                return True

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
                # Probe failed — go back to OPEN
                await self._transition_to(CircuitState.OPEN)
                logger.warning(
                    f"Circuit breaker probe FAILED: service={self.service_name} "
                    f"→ back to OPEN. Error: {error}"
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
