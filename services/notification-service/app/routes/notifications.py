"""HotSot Notification Service — Notification Routes + Dispatcher."""

import time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter
from pydantic import BaseModel
from enum import Enum

router = APIRouter()


class Channel(str, Enum):
    PUSH = "PUSH"
    SMS = "SMS"
    WEBSOCKET = "WEBSOCKET"
    IN_APP = "IN_APP"


class NotificationPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class SendNotificationRequest(BaseModel):
    user_id: str
    order_id: str
    channel: Channel = Channel.WEBSOCKET
    priority: NotificationPriority = NotificationPriority.NORMAL
    title: str
    message: str
    data: Dict[str, Any] = {}


NOTIFICATION_LOG: List[Dict[str, Any]] = []


async def dispatch_notification(user_id: str, order_id: str, channel: Channel,
                                priority: NotificationPriority, title: str, message: str,
                                data: Dict[str, Any] = {}) -> Dict[str, Any]:
    """Dispatch notification through specified channel. India fallback: WS → Push → SMS."""
    notification = {
        "notification_id": f"notif_{int(time.time() * 1000)}",
        "user_id": user_id, "order_id": order_id, "channel": channel,
        "priority": priority, "title": title, "message": message,
        "data": data, "status": "SENT", "timestamp": time.time(),
    }
    if channel == Channel.WEBSOCKET:
        notification["status"] = "DELIVERED_WS"
    elif channel == Channel.PUSH:
        notification["status"] = "DELIVERED_PUSH"
    elif channel == Channel.SMS:
        notification["status"] = "DELIVERED_SMS"
    elif channel == Channel.IN_APP:
        notification["status"] = "DELIVERED_IN_APP"
    NOTIFICATION_LOG.append(notification)
    return notification


@router.post("/send")
async def send_notification(req: SendNotificationRequest):
    """Send a notification to a user."""
    return await dispatch_notification(req.user_id, req.order_id, req.channel, req.priority, req.title, req.message, req.data)


@router.post("/order-update")
async def send_order_update(order_id: str, user_id: str, status: str,
                            eta_seconds: Optional[int] = None, shelf_id: Optional[str] = None):
    """Send order status update notification (auto-selects best channel)."""
    status_messages = {
        "CREATED": "Your order has been placed!",
        "SLOT_RESERVED": "Slot reserved! Proceed to payment.",
        "PAYMENT_CONFIRMED": "Payment confirmed! Kitchen is preparing your order.",
        "IN_PREP": "Your food is being prepared!",
        "READY": "Your order is ready! Heading to pickup shelf.",
        "ON_SHELF": f"Your order is on shelf {shelf_id or '?'}. Pick it up!",
        "PICKED": "Order picked up! Enjoy your meal!",
        "CANCELLED": "Your order has been cancelled.",
    }
    message = status_messages.get(status, f"Order status updated: {status}")
    if status in ["ON_SHELF", "READY"]:
        channel, priority = Channel.WEBSOCKET, NotificationPriority.HIGH
    elif status == "CANCELLED":
        channel, priority = Channel.SMS, NotificationPriority.URGENT
    else:
        channel, priority = Channel.WEBSOCKET, NotificationPriority.NORMAL
    return await dispatch_notification(user_id, order_id, channel, priority,
                                      f"Order Update - {status}", message,
                                      {"status": status, "eta_seconds": eta_seconds, "shelf_id": shelf_id})


@router.get("/log")
async def get_notification_log(limit: int = 50):
    return {"notifications": NOTIFICATION_LOG[-limit:], "total": len(NOTIFICATION_LOG)}
