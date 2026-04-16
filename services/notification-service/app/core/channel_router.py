"""HotSot Notification Service — Channel Router.

Routes notifications to the appropriate delivery channel provider.
Each channel has its own send() implementation with error handling.

Supported Channels:
  - WEBSOCKET : Real-time push to connected browser/app clients
  - PUSH      : FCM (Android) / APNs (iOS) push notifications
  - SMS       : Twilio / MSG91 (India) SMS gateway
  - IN_APP    : In-app notification center (stored in DB)

India Considerations:
  - MSG91 preferred for SMS (DLT-compliant, domestic routes)
  - FCM data messages for Android (high market share in India)
  - WebSocket as primary for urban users on reliable 4G/WiFi
"""

import logging
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class Channel(str, Enum):
    WEBSOCKET = "WEBSOCKET"
    PUSH = "PUSH"
    SMS = "SMS"
    EMAIL = "EMAIL"
    IN_APP = "IN_APP"


class ChannelRouter:
    """Routes notifications to channel-specific providers.

    Each channel has an async send() method. In production, these would
    integrate with actual providers (FCM, MSG91, etc.). Currently uses
    mock implementations that log and return success.
    """

    def __init__(self):
        self._ws_clients: Dict[str, list] = {}  # user_id → [websocket refs]
        self._push_tokens: Dict[str, str] = {}   # user_id → FCM/APNs token
        self._phone_numbers: Dict[str, str] = {}  # user_id → phone number
        self._email_addresses: Dict[str, str] = {}  # user_id → email address
        self._in_app_store: Dict[str, list] = {}  # user_id → [notifications]

    async def send(
        self,
        channel: Channel,
        user_id: str,
        title: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send notification through the specified channel.

        Args:
            channel: Target delivery channel
            user_id: Recipient user identifier
            title: Notification title/heading
            message: Notification body text
            data: Additional structured data payload

        Returns:
            Delivery result dict with channel, status, and metadata

        Raises:
            ChannelDeliveryError: If delivery fails after retries
        """
        data = data or {}

        if channel == Channel.WEBSOCKET:
            return await self._send_websocket(user_id, title, message, data)
        elif channel == Channel.PUSH:
            return await self._send_push(user_id, title, message, data)
        elif channel == Channel.SMS:
            return await self._send_sms(user_id, title, message, data)
        elif channel == Channel.EMAIL:
            return await self._send_email(user_id, title, message, data)
        elif channel == Channel.IN_APP:
            return await self._send_in_app(user_id, title, message, data)
        else:
            raise ChannelDeliveryError(f"Unknown channel: {channel}")

    async def _send_websocket(
        self, user_id: str, title: str, message: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send via WebSocket to connected browser/app clients.

        In production, this would:
          1. Look up user's active WebSocket connections from Redis
          2. Push JSON message to each connection
          3. Handle disconnections gracefully
          4. Fall back to HTTP long-polling if WS not available
        """
        connections = self._ws_clients.get(user_id, [])
        if not connections:
            # No active WS connection — trigger fallback
            logger.info("WS: No active connection for user %s, will fallback", user_id)
            raise ChannelDeliveryError(
                f"No active WebSocket connection for user {user_id}"
            )

        payload = {
            "type": "NOTIFICATION",
            "title": title,
            "message": message,
            "data": data,
        }
        # Mock: in production, iterate and send to each WS connection
        logger.info("WS → user=%s: %s", user_id, title)
        return {
            "channel": Channel.WEBSOCKET.value,
            "status": "DELIVERED_WS",
            "connections_reached": len(connections),
        }

    async def _send_push(
        self, user_id: str, title: str, message: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send via FCM/APNs push notification.

        In production, this would:
          1. Look up user's FCM token from Redis/DB
          2. Build FCM data message (Android) or APNs payload (iOS)
          3. Handle token invalidation and refresh
          4. Respect user notification preferences
        """
        token = self._push_tokens.get(user_id)
        if not token:
            logger.info("Push: No FCM token for user %s, will fallback", user_id)
            raise ChannelDeliveryError(f"No push token for user {user_id}")

        # Mock FCM send
        payload = {
            "to": token,
            "notification": {"title": title, "body": message},
            "data": data,
        }
        logger.info("Push → user=%s token=***: %s", user_id, title)
        return {
            "channel": Channel.PUSH.value,
            "status": "DELIVERED_PUSH",
            "fcm_message_id": f"msg_{hash(token) % 100000}",
        }

    async def _send_sms(
        self, user_id: str, title: str, message: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send via SMS gateway (MSG91 / Twilio for India).

        In production, this would:
          1. Look up user's phone number
          2. Check DND registry (TRAI regulation)
          3. Format message within 160 chars (GSM-7) or use Unicode
          4. Send via MSG91 API (DLT template ID required)
          5. Track delivery receipt

        India-specific:
          - DLT (Distributed Ledger Technology) compliance mandatory
          - Template must be pre-registered on DLT platform
          - MSG91 supports domestic promotional + transactional routes
        """
        phone = self._phone_numbers.get(user_id)
        if not phone:
            logger.info("SMS: No phone number for user %s, will fallback", user_id)
            raise ChannelDeliveryError(f"No phone number for user {user_id}")

        # Truncate message for SMS (160 chars GSM-7)
        sms_body = f"{title}: {message}"[:155] + "..." if len(f"{title}: {message}") > 160 else f"{title}: {message}"

        # Mock MSG91 send
        logger.info("SMS → user=%s phone=***%s: %s", user_id, phone[-4:], sms_body)
        return {
            "channel": Channel.SMS.value,
            "status": "DELIVERED_SMS",
            "msg91_message_id": f"sms_{hash(phone) % 100000}",
            "dlt_template_id": data.get("dlt_template_id", "DLT_DEFAULT"),
        }

    async def _send_in_app(
        self, user_id: str, title: str, message: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Store in in-app notification center.

        Always succeeds — this is the final fallback. Notifications are
        persisted to DB and shown when the user opens the app next.

        In production, this would:
          1. Insert into notifications table (PostgreSQL)
          2. Set unread_count in Redis for badge updates
          3. Publish to user's notification stream (Redis Sorted Set)
        """
        if user_id not in self._in_app_store:
            self._in_app_store[user_id] = []

        notification = {
            "title": title,
            "message": message,
            "data": data,
            "read": False,
        }
        self._in_app_store[user_id].append(notification)

        logger.info("InApp → user=%s: %s (stored)", user_id, title)
        return {
            "channel": Channel.IN_APP.value,
            "status": "DELIVERED_IN_APP",
            "unread_count": len([n for n in self._in_app_store[user_id] if not n["read"]]),
        }

    def register_ws_client(self, user_id: str, ws_ref) -> None:
        """Register an active WebSocket connection for a user."""
        if user_id not in self._ws_clients:
            self._ws_clients[user_id] = []
        self._ws_clients[user_id].append(ws_ref)

    def unregister_ws_client(self, user_id: str, ws_ref) -> None:
        """Unregister a WebSocket connection (on disconnect)."""
        if user_id in self._ws_clients:
            self._ws_clients[user_id] = [
                c for c in self._ws_clients[user_id] if c != ws_ref
            ]

    def register_push_token(self, user_id: str, token: str) -> None:
        """Register or update a user's FCM/APNs push token."""
        self._push_tokens[user_id] = token

    def register_phone(self, user_id: str, phone: str) -> None:
        """Register a user's phone number for SMS."""
        self._phone_numbers[user_id] = phone

    def register_email(self, user_id: str, email: str) -> None:
        """Register a user's email address for EMAIL channel."""
        self._email_addresses[user_id] = email

    async def _send_email(
        self, user_id: str, title: str, message: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Send via email (mock SMTP/SES).

        In production, this would:
          1. Look up user's email address from Redis/DB
          2. Send via AWS SES or SMTP relay
          3. Track delivery and bounce status
        """
        email = self._email_addresses.get(user_id)
        if not email:
            logger.info("Email: No email address for user %s, will fallback", user_id)
            raise ChannelDeliveryError(f"No email address for user {user_id}")

        # Mock SMTP/SES send
        logger.info("Email → user=%s email=%s: %s", user_id, email[:3] + "****", title)
        return {
            "channel": Channel.EMAIL.value,
            "status": "DELIVERED_EMAIL",
            "smtp_message_id": f"email_{hash(email) % 100000}",
        }


class ChannelDeliveryError(Exception):
    """Raised when a notification cannot be delivered through a channel."""
    pass
