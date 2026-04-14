"""HotSot Notification Service — Core Modules.

Multi-channel notification dispatcher with India-optimized fallback chain:
  WebSocket → Push → SMS → In-App

Modules:
    dispatcher   — Orchestrates channel selection, retry & fallback
    channel_router — Routes notifications to the right channel provider
    templates    — Per-event-type notification templates with i18n support
"""

from app.core.dispatcher import NotificationDispatcher
from app.core.channel_router import ChannelRouter
from app.core.templates import NotificationTemplates

__all__ = ["NotificationDispatcher", "ChannelRouter", "NotificationTemplates"]
