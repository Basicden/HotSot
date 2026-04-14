"""HotSot Notification Service — Notification Dispatcher.

Orchestrates the full notification lifecycle:
  1. Resolve template for event type
  2. Select primary channel (user preference / event urgency)
  3. Attempt delivery on primary channel
  4. Fallback chain: WS → Push → SMS → In-App
  5. Record delivery status & audit trail

India-specific:
  - SMS is high-priority fallback (low smartphone penetration in Tier-2/3)
  - WhatsApp Business API integration hook (future)
  - DND (Do Not Disturb) registry check before SMS/Push
"""

import time
import uuid
import logging
from typing import Optional, Dict, Any, List
from enum import Enum

from app.core.channel_router import ChannelRouter, Channel
from app.core.templates import NotificationTemplates

logger = logging.getLogger(__name__)


class NotificationPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class NotificationStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


# India-optimized fallback chain: cheapest/most-reliable first
FALLBACK_CHAIN: List[Channel] = [
    Channel.WEBSOCKET,
    Channel.PUSH,
    Channel.SMS,
    Channel.IN_APP,
]


class NotificationDispatcher:
    """Central dispatcher that manages the notification lifecycle with fallback.

    Attributes:
        channel_router: ChannelRouter instance for actual delivery
        templates: NotificationTemplates for message generation
        audit_log: In-memory audit trail (production: use DB)
        dnd_registry: Set of user_ids on Do-Not-Disturb (placeholder)
    """

    def __init__(self, channel_router: Optional[ChannelRouter] = None,
                 templates: Optional[NotificationTemplates] = None):
        self.channel_router = channel_router or ChannelRouter()
        self.templates = templates or NotificationTemplates()
        self.audit_log: List[Dict[str, Any]] = []
        self.dnd_registry: set = set()  # user_ids on DND

    async def dispatch(
        self,
        user_id: str,
        order_id: str,
        event_type: str,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        preferred_channel: Optional[Channel] = None,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dispatch a notification with automatic fallback.

        Args:
            user_id: Target user identifier
            order_id: Related order identifier
            event_type: Event type (e.g. 'order.created', 'order.on_shelf')
            priority: Notification urgency level
            preferred_channel: User's preferred channel (overrides auto-select)
            extra_data: Additional data to include in the notification payload

        Returns:
            Dispatch result dict with notification_id, status, channel, attempts
        """
        notification_id = f"notif_{uuid.uuid4().hex[:12]}"
        extra_data = extra_data or {}

        # Step 1: Resolve template
        title, message = self.templates.render(event_type, extra_data)

        # Step 2: Determine primary channel
        if preferred_channel:
            primary_channel = preferred_channel
        else:
            primary_channel = self._auto_select_channel(event_type, priority)

        # Step 3: Build fallback chain starting from primary
        chain = self._build_fallback_chain(primary_channel)

        # Step 4: DND check for SMS/Push channels
        if user_id in self.dnd_registry and priority != NotificationPriority.URGENT:
            chain = [c for c in chain if c not in (Channel.SMS, Channel.PUSH)]
            if not chain:
                chain = [Channel.IN_APP]

        # Step 5: Attempt delivery with fallback
        attempts = []
        final_status = NotificationStatus.FAILED
        delivered_channel = None

        for channel in chain:
            attempt = {
                "channel": channel.value,
                "timestamp": time.time(),
                "status": None,
                "error": None,
            }
            try:
                result = await self.channel_router.send(
                    channel=channel,
                    user_id=user_id,
                    title=title,
                    message=message,
                    data={
                        "notification_id": notification_id,
                        "order_id": order_id,
                        "event_type": event_type,
                        "priority": priority.value,
                        **extra_data,
                    },
                )
                attempt["status"] = "DELIVERED"
                final_status = NotificationStatus.DELIVERED
                delivered_channel = channel
                break
            except Exception as exc:
                attempt["status"] = "FAILED"
                attempt["error"] = str(exc)
                logger.warning(
                    "Channel %s failed for user %s: %s", channel.value, user_id, exc
                )
            attempts.append(attempt)

        # Step 6: Record audit
        notification_record = {
            "notification_id": notification_id,
            "user_id": user_id,
            "order_id": order_id,
            "event_type": event_type,
            "priority": priority.value,
            "title": title,
            "message": message,
            "status": final_status.value,
            "channel": delivered_channel.value if delivered_channel else None,
            "attempts": attempts,
            "created_at": time.time(),
        }
        self.audit_log.append(notification_record)

        return notification_record

    def _auto_select_channel(
        self, event_type: str, priority: NotificationPriority
    ) -> Channel:
        """Auto-select the best channel based on event type and priority.

        Rules:
          - URGENT + cancellation/refund → SMS (guaranteed delivery)
          - HIGH + shelf/ready → WebSocket (real-time for active users)
          - NORMAL status updates → WebSocket
          - LOW/promotional → In-App only
        """
        if priority == NotificationPriority.URGENT:
            if "cancel" in event_type or "refund" in event_type:
                return Channel.SMS
            return Channel.PUSH

        if priority == NotificationPriority.HIGH:
            if "shelf" in event_type or "ready" in event_type:
                return Channel.WEBSOCKET
            return Channel.PUSH

        if priority == NotificationPriority.NORMAL:
            return Channel.WEBSOCKET

        # LOW priority — in-app only, no push/SMS cost
        return Channel.IN_APP

    def _build_fallback_chain(self, primary: Channel) -> List[Channel]:
        """Build a fallback chain starting from the primary channel.

        The chain starts with the primary channel, then appends any channels
        from FALLBACK_CHAIN that were not already included, preserving the
        India-optimized order (WS → Push → SMS → In-App).
        """
        chain = [primary]
        for ch in FALLBACK_CHAIN:
            if ch not in chain:
                chain.append(ch)
        return chain

    async def dispatch_order_update(
        self,
        order_id: str,
        user_id: str,
        status: str,
        eta_seconds: Optional[int] = None,
        shelf_id: Optional[str] = None,
        arrival_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Convenience method for order status change notifications.

        Maps order V2 states to appropriate event types, priorities, and channels.
        Supports all 16 V2 states with India-specific channel routing.
        """
        # V2 state → (event_type, priority)
        STATE_CONFIG: Dict[str, tuple] = {
            "CREATED": ("order.created", NotificationPriority.NORMAL),
            "SLOT_RESERVED": ("order.slot_reserved", NotificationPriority.NORMAL),
            "PAYMENT_CONFIRMED": ("order.payment_confirmed", NotificationPriority.NORMAL),
            "IN_PREP": ("order.in_prep", NotificationPriority.NORMAL),
            "PREP_DONE": ("order.prep_done", NotificationPriority.HIGH),
            "QUALITY_CHECK": ("order.quality_check", NotificationPriority.NORMAL),
            "READY": ("order.ready", NotificationPriority.HIGH),
            "ON_SHELF": ("order.on_shelf", NotificationPriority.HIGH),
            "ARRIVED": ("order.arrived", NotificationPriority.HIGH),
            "PICKED": ("order.picked", NotificationPriority.NORMAL),
            "CANCELLED": ("order.cancelled", NotificationPriority.URGENT),
            "EXPIRED": ("order.expired", NotificationPriority.HIGH),
            "REFUNDED": ("order.refunded", NotificationPriority.URGENT),
            "COMPENSATED": ("order.compensated", NotificationPriority.HIGH),
            "NO_SHOW": ("order.no_show", NotificationPriority.HIGH),
            "FAILED_QC": ("order.failed_qc", NotificationPriority.URGENT),
        }

        config = STATE_CONFIG.get(status, (f"order.{status.lower()}", NotificationPriority.NORMAL))
        event_type, priority = config

        extra_data: Dict[str, Any] = {"status": status}
        if eta_seconds is not None:
            extra_data["eta_seconds"] = eta_seconds
        if shelf_id is not None:
            extra_data["shelf_id"] = shelf_id
        if arrival_type is not None:
            extra_data["arrival_type"] = arrival_type

        return await self.dispatch(
            user_id=user_id,
            order_id=order_id,
            event_type=event_type,
            priority=priority,
            extra_data=extra_data,
        )

    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return recent audit log entries."""
        return self.audit_log[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Return dispatcher statistics."""
        total = len(self.audit_log)
        delivered = sum(1 for n in self.audit_log if n["status"] == "DELIVERED")
        failed = sum(1 for n in self.audit_log if n["status"] == "FAILED")
        by_channel: Dict[str, int] = {}
        for n in self.audit_log:
            ch = n.get("channel", "UNKNOWN")
            by_channel[ch] = by_channel.get(ch, 0) + 1
        return {
            "total_notifications": total,
            "delivered": delivered,
            "failed": failed,
            "delivery_rate": round(delivered / total, 3) if total else 0.0,
            "by_channel": by_channel,
        }
