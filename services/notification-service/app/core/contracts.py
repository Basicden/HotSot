"""
HotSot Notification Service — Service Contracts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class NotificationServiceContract(ABC):
    """Contract that the Notification Service must fulfill."""

    @abstractmethod
    async def send_notification(
        self, user_id: str, notification_type: str, title: str,
        message: str, tenant_id: str, data: Optional[Dict] = None,
        channels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Send a notification to a user across specified channels.

        Args:
            user_id: Target user.
            notification_type: Type (ORDER_UPDATE, PROMO, PAYMENT, SYSTEM).
            title: Notification title.
            message: Notification body.
            tenant_id: Tenant identifier.
            data: Additional data payload.
            channels: List of channels (push, sms, email, whatsapp, in_app).
        """
        raise NotImplementedError("send_notification not implemented")

    @abstractmethod
    async def send_order_update(
        self, order_id: str, user_id: str, status: str, tenant_id: str,
        eta_minutes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send an order status update notification."""
        raise NotImplementedError("send_order_update not implemented")

    @abstractmethod
    async def get_user_notifications(
        self, user_id: str, tenant_id: str, limit: int = 20, offset: int = 0,
    ) -> Dict[str, Any]:
        """Get notification history for a user."""
        raise NotImplementedError("get_user_notifications not implemented")

    @abstractmethod
    async def mark_read(self, notification_id: str, user_id: str, tenant_id: str) -> Dict[str, Any]:
        """Mark a notification as read."""
        raise NotImplementedError("mark_read not implemented")
