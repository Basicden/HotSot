"""HotSot Notification Service — Unit Tests.

Tests cover:
1. Notification dispatcher: channel routing, rate limiting, dedup
2. Template engine: rendering, i18n, event types
3. Channel router: WebSocket, Push, SMS, Email, In-App
4. TRAI compliance: promo block hours, transactional detection
"""
import hashlib
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.dispatcher import (
    NotificationDispatcher,
    MockSMSGateway,
    MockFirebasePush,
    MockSMTPGateway,
    PROMO_BLOCK_START,
    PROMO_BLOCK_END,
    DEFAULT_RATE_LIMIT_PER_HOUR,
)
from app.core.templates import NotificationTemplates, DEFAULT_LANG
from app.core.channel_router import ChannelRouter, Channel, ChannelDeliveryError


# ═══════════════════════════════════════════════════════════════
# MOCK GATEWAY TESTS
# ═══════════════════════════════════════════════════════════════

class TestMockSMSGateway:
    """Test MockSMSGateway interface."""

    @pytest.mark.asyncio
    async def test_send_returns_delivered_status(self):
        result = await MockSMSGateway().send(to="+919876543210", message="Test")
        assert result["status"] == "delivered"
        assert "message_id" in result
        assert result["provider"] == "MOCK_SMS"


class TestMockFirebasePush:
    """Test MockFirebasePush interface."""

    @pytest.mark.asyncio
    async def test_send_returns_delivered_status(self):
        result = await MockFirebasePush().send(token="test_token", title="Hi", body="Test")
        assert result["status"] == "delivered"
        assert result["provider"] == "MOCK_FCM"


class TestMockSMTPGateway:
    """Test MockSMTPGateway interface."""

    @pytest.mark.asyncio
    async def test_send_returns_delivered_status(self):
        result = await MockSMTPGateway().send(to="test@example.com", subject="Hi", body="Test")
        assert result["status"] == "delivered"
        assert result["provider"] == "MOCK_SMTP"


# ═══════════════════════════════════════════════════════════════
# NOTIFICATION DISPATCHER — RATE LIMITING
# ═══════════════════════════════════════════════════════════════

class TestDispatcherRateLimiting:
    """Test rate limiting in NotificationDispatcher."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.client = AsyncMock()
        return redis

    @pytest.mark.asyncio
    async def test_rate_limit_allows_under_limit(self, mock_redis):
        """Under-limit NORMAL priority should be allowed."""
        mock_redis.client.incr.return_value = 5
        dispatcher = NotificationDispatcher(redis_client=mock_redis)
        result = await dispatcher._check_rate_limit("user-1", "SMS", "t1", "NORMAL")
        assert result is True

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_over_limit(self, mock_redis):
        """Over-limit NORMAL priority should be blocked."""
        mock_redis.client.incr.return_value = DEFAULT_RATE_LIMIT_PER_HOUR + 1
        dispatcher = NotificationDispatcher(redis_client=mock_redis)
        result = await dispatcher._check_rate_limit("user-1", "SMS", "t1", "NORMAL")
        assert result is False

    @pytest.mark.asyncio
    async def test_high_priority_exempt_from_rate_limit(self, mock_redis):
        """HIGH priority should bypass rate limiting."""
        dispatcher = NotificationDispatcher(redis_client=mock_redis)
        result = await dispatcher._check_rate_limit("user-1", "SMS", "t1", "HIGH")
        assert result is True

    @pytest.mark.asyncio
    async def test_urgent_priority_exempt_from_rate_limit(self, mock_redis):
        """URGENT priority should bypass rate limiting."""
        dispatcher = NotificationDispatcher(redis_client=mock_redis)
        result = await dispatcher._check_rate_limit("user-1", "SMS", "t1", "URGENT")
        assert result is True

    @pytest.mark.asyncio
    async def test_rate_limit_fail_open_on_redis_error(self, mock_redis):
        """Redis errors should fail-open (allow notification)."""
        from redis.exceptions import RedisError
        mock_redis.client.incr.side_effect = RedisError("Down")
        dispatcher = NotificationDispatcher(redis_client=mock_redis)
        result = await dispatcher._check_rate_limit("user-1", "SMS", "t1", "NORMAL")
        assert result is True


# ═══════════════════════════════════════════════════════════════
# NOTIFICATION DISPATCHER — TRAI COMPLIANCE
# ═══════════════════════════════════════════════════════════════

class TestTRAICompliance:
    """Test TRAI DND compliance for SMS."""

    def test_transactional_order_keyword(self):
        """Order-related notifications should be transactional."""
        assert NotificationDispatcher._is_transactional("Order Ready", "Your order is ready") is True

    def test_transactional_payment_keyword(self):
        """Payment-related notifications should be transactional."""
        assert NotificationDispatcher._is_transactional("Payment", "Payment confirmed") is True

    def test_transactional_otp_keyword(self):
        """OTP notifications should be transactional."""
        assert NotificationDispatcher._is_transactional("OTP", "Your OTP is 1234") is True

    def test_promotional_is_not_transactional(self):
        """Promotional messages should not be transactional."""
        assert NotificationDispatcher._is_transactional("Deal!", "50% off today") is False

    @pytest.mark.asyncio
    async def test_dispatch_blocks_promo_during_trai_hours(self):
        """Promotional SMS during 9PM-9AM IST should be blocked."""
        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.exists.return_value = False
        # Mock session that returns no preferences (all enabled)
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result
        dispatcher = NotificationDispatcher(redis_client=mock_redis)
        with patch.object(dispatcher, '_check_trai_compliance', return_value=False), \
             patch.object(dispatcher, '_is_transactional', return_value=False):
            result = await dispatcher.dispatch(
                user_id="u1", channel="SMS",
                title="Deal!", body="50% off",
                tenant_id="t1", priority="NORMAL",
                phone="+919876543210", session=mock_session,
            )
            assert result["status"] == "BLOCKED"
            assert "TRAI" in result["reason"]


# ═══════════════════════════════════════════════════════════════
# NOTIFICATION DISPATCHER — DEDUP
# ═══════════════════════════════════════════════════════════════

class TestDispatcherDedup:
    """Test deduplication in NotificationDispatcher."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.client = AsyncMock()
        redis.client.exists.return_value = False
        redis.client.incr.return_value = 1
        redis.client.expire.return_value = True
        return redis

    @pytest.mark.asyncio
    async def test_duplicate_notification_blocked(self, mock_redis):
        """Duplicate notification body should be blocked."""
        mock_redis.client.exists.return_value = True
        dispatcher = NotificationDispatcher(redis_client=mock_redis)
        result = await dispatcher.dispatch(
            user_id="u1", channel="SMS", title="Test",
            body="Same body", tenant_id="t1", priority="HIGH",
            phone="+919876543210",
        )
        assert result["status"] == "DUPLICATE"

    @pytest.mark.asyncio
    async def test_non_duplicate_notification_dispatched(self, mock_redis):
        """Non-duplicate notification should proceed normally."""
        mock_redis.client.exists.return_value = False
        dispatcher = NotificationDispatcher(redis_client=mock_redis)
        result = await dispatcher.dispatch(
            user_id="u1", channel="IN_APP", title="Test",
            body="New body", tenant_id="t1", priority="HIGH",
        )
        assert result["status"] == "SENT"


# ═══════════════════════════════════════════════════════════════
# NOTIFICATION TEMPLATES
# ═══════════════════════════════════════════════════════════════

class TestNotificationTemplates:
    """Test template rendering and event types."""

    def test_render_order_created_english(self):
        """order.created should render in English."""
        engine = NotificationTemplates()
        title, message = engine.render("order.created", {"order_id": "123"})
        assert "123" in message
        assert title  # Non-empty title

    def test_render_order_created_hindi(self):
        """order.created should render in Hindi."""
        engine = NotificationTemplates()
        title, message = engine.render("order.created", {"order_id": "123"}, lang="hi")
        assert "123" in message
        # Hindi text check
        assert title != "Order Placed!"  # Should be Hindi

    def test_render_unknown_event_falls_back(self):
        """Unknown event type should fallback gracefully."""
        engine = NotificationTemplates()
        title, message = engine.render("unknown.event", {"order_id": "456"})
        assert "456" in message

    def test_render_with_placeholder_data(self):
        """Template placeholders should be filled from extra_data."""
        engine = NotificationTemplates()
        title, message = engine.render(
            "order.on_shelf",
            {"order_id": "789", "shelf_id": "S3", "ttl_seconds": 600},
        )
        assert "789" in message
        assert "S3" in message

    def test_list_event_types_english(self):
        """Should list all English event types."""
        engine = NotificationTemplates()
        types = engine.list_event_types()
        assert "order.created" in types
        assert "order.picked" in types

    def test_add_custom_template(self):
        """Custom templates can be added at runtime."""
        engine = NotificationTemplates()
        engine.add_template("custom.event", "Custom", "Hello {name}")
        title, message = engine.render("custom.event", {"name": "World"})
        assert title == "Custom"
        assert "World" in message


# ═══════════════════════════════════════════════════════════════
# CHANNEL ROUTER
# ═══════════════════════════════════════════════════════════════

class TestChannelRouter:
    """Test ChannelRouter channel delivery."""

    @pytest.mark.asyncio
    async def test_in_app_always_succeeds(self):
        """In-app channel should always succeed (stored)."""
        router = ChannelRouter()
        result = await router.send(Channel.IN_APP, "user-1", "Test", "Body")
        assert result["status"] == "DELIVERED_IN_APP"
        assert result["unread_count"] == 1

    @pytest.mark.asyncio
    async def test_websocket_no_connection_raises_error(self):
        """WebSocket with no active connection should raise error."""
        router = ChannelRouter()
        with pytest.raises(ChannelDeliveryError):
            await router.send(Channel.WEBSOCKET, "user-1", "Test", "Body")

    @pytest.mark.asyncio
    async def test_push_no_token_raises_error(self):
        """Push with no registered token should raise error."""
        router = ChannelRouter()
        with pytest.raises(ChannelDeliveryError):
            await router.send(Channel.PUSH, "user-1", "Test", "Body")

    @pytest.mark.asyncio
    async def test_sms_no_phone_raises_error(self):
        """SMS with no phone number should raise error."""
        router = ChannelRouter()
        with pytest.raises(ChannelDeliveryError):
            await router.send(Channel.SMS, "user-1", "Test", "Body")

    @pytest.mark.asyncio
    async def test_email_no_address_raises_error(self):
        """Email with no email address should raise error."""
        router = ChannelRouter()
        with pytest.raises(ChannelDeliveryError):
            await router.send(Channel.EMAIL, "user-1", "Test", "Body")

    @pytest.mark.asyncio
    async def test_push_with_registered_token_succeeds(self):
        """Push with registered token should succeed."""
        router = ChannelRouter()
        router.register_push_token("user-1", "fcm_token_123")
        result = await router.send(Channel.PUSH, "user-1", "Test", "Body")
        assert result["status"] == "DELIVERED_PUSH"

    @pytest.mark.asyncio
    async def test_sms_with_registered_phone_succeeds(self):
        """SMS with registered phone should succeed."""
        router = ChannelRouter()
        router.register_phone("user-1", "+919876543210")
        result = await router.send(Channel.SMS, "user-1", "Test", "Body")
        assert result["status"] == "DELIVERED_SMS"

    @pytest.mark.asyncio
    async def test_email_with_registered_email_succeeds(self):
        """Email with registered email should succeed."""
        router = ChannelRouter()
        router.register_email("user-1", "test@example.com")
        result = await router.send(Channel.EMAIL, "user-1", "Test", "Body")
        assert result["status"] == "DELIVERED_EMAIL"

    @pytest.mark.asyncio
    async def test_in_app_unread_count_increments(self):
        """Multiple in-app notifications should increment unread count."""
        router = ChannelRouter()
        await router.send(Channel.IN_APP, "user-1", "Test 1", "Body")
        result = await router.send(Channel.IN_APP, "user-1", "Test 2", "Body")
        assert result["unread_count"] == 2
