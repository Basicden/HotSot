"""
HotSot Circuit Breaker + Redis Integration Tests.

Tests the circuit breaker protection layer inside RedisClient:
    - Circuit breaker opens after consecutive Redis failures
    - Fail-fast behavior when circuit is OPEN
    - Health check includes circuit breaker metrics
    - All Redis methods (cache_set, cache_get, publish, etc.) are protected
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal


class TestCircuitBreakerInRedis:
    """Test circuit breaker integration in RedisClient."""

    def test_redis_client_imports_circuit_breaker(self):
        """Verify redis_client.py imports circuit_breaker module."""
        from shared.utils.redis_client import _redis_circuit_breaker
        assert _redis_circuit_breaker is not None
        assert _redis_circuit_breaker.service_name == "redis"

    def test_circuit_breaker_initial_state(self):
        """Circuit breaker starts in CLOSED state."""
        from shared.circuit_breaker import CircuitState
        from shared.utils.redis_client import _redis_circuit_breaker
        assert _redis_circuit_breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_cache_get_returns_none_when_circuit_open(self):
        """When circuit is OPEN, cache_get returns None immediately."""
        from shared.utils.redis_client import RedisClient, _redis_circuit_breaker
        from shared.circuit_breaker import CircuitState

        # Force circuit open
        _redis_circuit_breaker._state = CircuitState.OPEN
        _redis_circuit_breaker._last_failure_time = asyncio.get_event_loop().time()

        client = RedisClient("test")
        result = await client.cache_get("any_key")
        assert result is None

        # Reset for other tests
        await _redis_circuit_breaker.reset()

    @pytest.mark.asyncio
    async def test_cache_set_returns_false_when_circuit_open(self):
        """When circuit is OPEN, cache_set returns False immediately."""
        from shared.utils.redis_client import RedisClient, _redis_circuit_breaker
        from shared.circuit_breaker import CircuitState

        _redis_circuit_breaker._state = CircuitState.OPEN
        _redis_circuit_breaker._last_failure_time = asyncio.get_event_loop().time()

        client = RedisClient("test")
        result = await client.cache_set("key", "value")
        assert result is False

        await _redis_circuit_breaker.reset()

    @pytest.mark.asyncio
    async def test_publish_returns_false_when_circuit_open(self):
        """When circuit is OPEN, publish returns False immediately."""
        from shared.utils.redis_client import RedisClient, _redis_circuit_breaker
        from shared.circuit_breaker import CircuitState

        _redis_circuit_breaker._state = CircuitState.OPEN
        _redis_circuit_breaker._last_failure_time = asyncio.get_event_loop().time()

        client = RedisClient("test")
        result = await client.publish("channel", {"data": "test"})
        assert result is False

        await _redis_circuit_breaker.reset()

    @pytest.mark.asyncio
    async def test_rate_limit_denied_when_circuit_open(self):
        """When circuit is OPEN, rate limit is DENIED (fail-closed)."""
        from shared.utils.redis_client import RedisClient, _redis_circuit_breaker
        from shared.circuit_breaker import CircuitState

        _redis_circuit_breaker._state = CircuitState.OPEN
        _redis_circuit_breaker._last_failure_time = asyncio.get_event_loop().time()

        client = RedisClient("test")
        allowed, remaining = await client.check_rate_limit("tenant_123")
        assert allowed is False
        assert remaining == 0

        await _redis_circuit_breaker.reset()

    @pytest.mark.asyncio
    async def test_lock_denied_when_circuit_open(self):
        """When circuit is OPEN, lock acquisition is DENIED (fail-closed)."""
        from shared.utils.redis_client import RedisClient, _redis_circuit_breaker
        from shared.circuit_breaker import CircuitState

        _redis_circuit_breaker._state = CircuitState.OPEN
        _redis_circuit_breaker._last_failure_time = asyncio.get_event_loop().time()

        client = RedisClient("test")
        result = await client.acquire_distributed_lock("resource_1")
        assert result is False

        await _redis_circuit_breaker.reset()

    @pytest.mark.asyncio
    async def test_health_check_includes_circuit_breaker(self):
        """Health check response includes circuit breaker metrics."""
        from shared.utils.redis_client import RedisClient, _redis_circuit_breaker

        client = RedisClient("test")
        health = await client.health_check()
        assert "circuit_breaker" in health
        assert health["circuit_breaker"]["service_name"] == "redis"

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_on_failures(self):
        """Circuit opens after consecutive failures."""
        from shared.utils.redis_client import _redis_circuit_breaker
        from shared.circuit_breaker import CircuitState

        await _redis_circuit_breaker.reset()

        for _ in range(5):
            await _redis_circuit_breaker.record_failure(Exception("Redis down"))

        assert _redis_circuit_breaker.state == CircuitState.OPEN
        await _redis_circuit_breaker.reset()

    @pytest.mark.asyncio
    async def test_circuit_breaker_closes_after_recovery(self):
        """Circuit closes after successful recovery probes."""
        from shared.utils.redis_client import _redis_circuit_breaker
        from shared.circuit_breaker import CircuitState

        await _redis_circuit_breaker.reset()

        # Open the circuit
        for _ in range(5):
            await _redis_circuit_breaker.record_failure(Exception("Redis down"))
        assert _redis_circuit_breaker.state == CircuitState.OPEN

        # Simulate HALF_OPEN by forcing state
        await _redis_circuit_breaker._transition_to(CircuitState.HALF_OPEN)

        # Successful probes close the circuit
        await _redis_circuit_breaker.record_success()
        await _redis_circuit_breaker.record_success()
        assert _redis_circuit_breaker.state == CircuitState.CLOSED


class TestOptimisticLockingModule:
    """Test shared.optimistic_locking module."""

    def test_concurrent_update_error(self):
        from shared.optimistic_locking import ConcurrentUpdateError
        with pytest.raises(ConcurrentUpdateError):
            raise ConcurrentUpdateError("OrderModel", "order_123", 3)

    def test_imports(self):
        from shared.optimistic_locking import (
            optimistic_update,
            optimistic_update_with_retry,
            ConcurrentUpdateError,
        )
        assert callable(optimistic_update)
        assert callable(optimistic_update_with_retry)


class TestCircuitBreakerModule:
    """Test shared.circuit_breaker module independently."""

    @pytest.mark.asyncio
    async def test_decorator_pattern(self):
        """@circuit_breaker decorator wraps async functions."""
        from shared.circuit_breaker import circuit_breaker, CircuitOpenError

        @circuit_breaker("test_decorator_svc", failure_threshold=2, recovery_timeout=60)
        async def failing_function():
            raise RuntimeError("Service down")

        # Should raise RuntimeError initially (circuit closed)
        with pytest.raises(RuntimeError):
            await failing_function()

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """circuit_breaker.protect() as context manager."""
        from shared.circuit_breaker import CircuitBreaker, CircuitOpenError

        cb = CircuitBreaker("test_ctx", failure_threshold=2)

        # Should work normally when closed
        async with cb.protect():
            pass  # No error

    @pytest.mark.asyncio
    async def test_call_method(self):
        """CircuitBreaker.call() wraps async function calls."""
        from shared.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker("test_call")

        async def success_fn():
            return "ok"

        result = await cb.call(success_fn)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_get_circuit_breaker_singleton(self):
        """get_circuit_breaker returns same instance for same name."""
        from shared.circuit_breaker import get_circuit_breaker

        cb1 = get_circuit_breaker("test_singleton")
        cb2 = get_circuit_breaker("test_singleton")
        assert cb1 is cb2

    @pytest.mark.asyncio
    async def test_all_circuit_breakers_metrics(self):
        """get_all_circuit_breakers returns metrics dict."""
        from shared.circuit_breaker import get_all_circuit_breakers, get_circuit_breaker

        get_circuit_breaker("metrics_test")
        metrics = get_all_circuit_breakers()
        assert isinstance(metrics, dict)
        assert "metrics_test" in metrics


class TestMoneyEdgeCases:
    """Additional Money class edge case tests."""

    def test_zero_money(self):
        from shared.money import Money
        m = Money("0.00")
        assert m.is_zero
        assert not m.is_positive

    def test_large_amount(self):
        from shared.money import Money
        m = Money("999999999.99")
        assert m.to_paise() == 99999999999

    def test_one_paise(self):
        from shared.money import Money
        m = Money("0.01")
        assert m.to_paise() == 1

    def test_from_paise_zero(self):
        from shared.money import Money
        m = Money.from_paise(0)
        assert m.is_zero

    def test_hash_consistency(self):
        from shared.money import Money
        m1 = Money("100.00")
        m2 = Money("100.00")
        assert hash(m1) == hash(m2)
        assert len({m1, m2}) == 1  # Same in set

    def test_to_dict(self):
        from shared.money import Money
        m = Money("500.00")
        d = m.to_dict()
        assert d["amount"] == "500.00"
        assert d["currency"] == "INR"
        assert d["paise"] == 50000


class TestPaymentGatewayEdgeCases:
    """Additional payment gateway edge case tests."""

    def test_webhook_verification_no_secret(self):
        """Without webhook secret, verification passes (dev mode)."""
        from shared.payment_gateway import RazorpayGateway
        gw = RazorpayGateway(test_mode=True)
        # No webhook secret set, should return True
        assert gw.verify_webhook_signature("body", "sig") is True

    def test_gateway_not_configured_in_test_mode(self):
        """Test mode means gateway is not configured with real keys."""
        from shared.payment_gateway import RazorpayGateway
        gw = RazorpayGateway(test_mode=True)
        assert not gw.is_configured


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
