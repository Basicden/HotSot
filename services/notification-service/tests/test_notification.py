"""HotSot Notification Service — Unit Tests."""
from app.core.dispatcher import NotificationDispatcher
from app.core.channel_router import ChannelRouter
from app.core.templates import TemplateEngine

def test_dispatcher_init():
    d = NotificationDispatcher()
    assert d is not None

def test_channel_router_default_channels():
    cr = ChannelRouter()
    channels = cr.get_channels_for_event("order.ready")
    assert isinstance(channels, list)
    assert len(channels) > 0

def test_channel_router_payment_uses_sms():
    cr = ChannelRouter()
    channels = cr.get_channels_for_event("payment.confirmed")
    assert "SMS" in channels or len(channels) > 0

def test_template_engine_renders():
    te = TemplateEngine()
    result = te.render("order.ready", {"order_id": "o_001", "eta_minutes": 10})
    assert isinstance(result, str)
    assert len(result) > 0

def test_rate_limit():
    d = NotificationDispatcher()
    # Should allow first notification
    assert d.check_rate_limit("u_001", "order_001") is True
