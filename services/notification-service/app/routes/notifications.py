"""HotSot Notification Service — Notification Routes + Dispatcher.

Real implementations backed by NotificationDispatcher and DB storage.
Channels: PUSH, SMS, EMAIL, IN_APP, WEBSOCKET.
Includes rate limiting and preference checking.
"""

import logging
import time
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from enum import Enum

from shared.auth.jwt import get_current_user
from app.core.database import (
    NotificationLogModel, InAppNotificationModel, NotificationPreferenceModel,
)
from app.core.dispatcher import NotificationDispatcher
from app.core.templates import NotificationTemplates

logger = logging.getLogger(__name__)

router = APIRouter()

_session_factory = None
_redis_client = None

# Template engine
_template_engine = NotificationTemplates()


def set_dependencies(session_factory, redis_client):
    """Wire shared dependencies."""
    global _session_factory, _redis_client
    _session_factory = session_factory
    _redis_client = redis_client


async def get_session():
    """Get async database session."""
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session


class Channel(str, Enum):
    PUSH = "PUSH"
    SMS = "SMS"
    EMAIL = "EMAIL"
    WEBSOCKET = "WEBSOCKET"
    IN_APP = "IN_APP"


class NotificationPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class SendNotificationRequest(BaseModel):
    user_id: str
    order_id: str = ""
    channel: Channel = Channel.IN_APP
    priority: NotificationPriority = NotificationPriority.NORMAL
    title: str
    message: str
    data: Dict[str, Any] = {}
    phone: Optional[str] = None
    email: Optional[str] = None
    fcm_token: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# DISPATCH
# ═══════════════════════════════════════════════════════════════

async def dispatch_notification(
    user_id: str,
    order_id: str,
    channel: Channel,
    priority: NotificationPriority,
    title: str,
    message: str,
    data: Dict[str, Any] = {},
    phone: str = None,
    email: str = None,
    fcm_token: str = None,
    tenant_id: str = "default",
    session: AsyncSession = None,
) -> Dict[str, Any]:
    """Dispatch notification through specified channel with real provider integration.

    India fallback: IN_APP → PUSH → SMS
    """
    # Map WEBSOCKET to IN_APP (WS is handled by realtime-service)
    dispatch_channel = "IN_APP" if channel == Channel.WEBSOCKET else channel.value

    dispatcher = NotificationDispatcher(_redis_client)
    result = await dispatcher.dispatch(
        user_id=user_id,
        channel=dispatch_channel,
        title=title,
        body=message,
        order_id=order_id or None,
        tenant_id=tenant_id,
        priority=priority.value,
        phone=phone,
        email=email,
        fcm_token=fcm_token,
        data=data,
        session=session,
    )

    # Commit the session (dispatcher already created/updated the log entry)
    if session:
        try:
            await session.commit()
        except Exception as exc:
            logger.warning("Failed to commit notification session: %s", exc)

    return result


# ═══════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════

@router.post("/send")
async def send_notification(
    req: SendNotificationRequest,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Send a notification to a user via the specified channel."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id", "default"))
    return await dispatch_notification(
        user_id=req.user_id,
        order_id=req.order_id,
        channel=req.channel,
        priority=req.priority,
        title=req.title,
        message=req.message,
        data=req.data,
        phone=req.phone,
        email=req.email,
        fcm_token=req.fcm_token,
        tenant_id=tenant_id,
        session=session,
    )


@router.post("/order-update")
async def send_order_update(
    order_id: str,
    user_id: str,
    status: str,
    eta_seconds: Optional[int] = None,
    shelf_id: Optional[str] = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Send order status update notification (auto-selects best channel)."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id", "default"))

    # Use template engine for message
    event_type = f"order.{status.lower()}"
    extra_data = {
        "order_id": order_id,
        "eta_seconds": eta_seconds,
        "shelf_id": shelf_id,
    }
    title, message = _template_engine.render(event_type, extra_data)

    # Auto-select channel and priority based on status
    if status in ["ON_SHELF", "READY"]:
        channel, priority = Channel.PUSH, NotificationPriority.HIGH
    elif status == "CANCELLED":
        channel, priority = Channel.SMS, NotificationPriority.URGENT
    elif status in ["CREATED", "PAYMENT_CONFIRMED", "IN_PREP"]:
        channel, priority = Channel.IN_APP, NotificationPriority.NORMAL
    else:
        channel, priority = Channel.IN_APP, NotificationPriority.NORMAL

    return await dispatch_notification(
        user_id=user_id,
        order_id=order_id,
        channel=channel,
        priority=priority,
        title=title,
        message=message,
        data={"status": status, "eta_seconds": eta_seconds, "shelf_id": shelf_id},
        tenant_id=tenant_id,
        session=session,
    )


@router.get("/log")
async def get_notification_log(
    limit: int = 50,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get recent notification log entries."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id", "default"))

    result = await session.execute(
        select(NotificationLogModel)
        .where(NotificationLogModel.tenant_id == tenant_id)
        .order_by(NotificationLogModel.created_at.desc())
        .limit(limit)
    )
    notifications = result.scalars().all()

    return {
        "notifications": [
            {
                "id": str(n.id),
                "user_id": str(n.user_id),
                "channel": n.channel,
                "title": n.title,
                "status": n.status,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
        "total": len(notifications),
    }


@router.get("/in-app/{user_id}")
async def get_in_app_notifications(
    user_id: str,
    unread_only: bool = False,
    limit: int = 50,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get in-app notifications for a user."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id", "default"))

    query = select(InAppNotificationModel).where(
        InAppNotificationModel.user_id == user_id,
        InAppNotificationModel.tenant_id == tenant_id,
    )
    if unread_only:
        query = query.where(InAppNotificationModel.is_read == False)
    query = query.order_by(InAppNotificationModel.created_at.desc()).limit(limit)

    result = await session.execute(query)
    notifications = result.scalars().all()

    # Count unread
    count_result = await session.execute(
        select(func.count(InAppNotificationModel.id)).where(
            InAppNotificationModel.user_id == user_id,
            InAppNotificationModel.tenant_id == tenant_id,
            InAppNotificationModel.is_read == False,
        )
    )
    unread_count = count_result.scalar() or 0

    return {
        "user_id": user_id,
        "unread_count": unread_count,
        "notifications": [
            {
                "id": str(n.id),
                "title": n.title,
                "body": n.body,
                "is_read": n.is_read,
                "priority": n.priority,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
    }


@router.get("/preferences/{user_id}")
async def get_preferences(
    user_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get notification preferences for a user."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id", "default"))
    result = await session.execute(
        select(NotificationPreferenceModel).where(
            NotificationPreferenceModel.user_id == user_id,
            NotificationPreferenceModel.tenant_id == tenant_id,
        )
    )
    pref = result.scalar_one_or_none()
    if not pref:
        return {
            "user_id": user_id,
            "push_enabled": True, "sms_enabled": True,
            "email_enabled": True, "in_app_enabled": True,
            "whatsapp_enabled": False, "language": "en",
        }
    return {
        "user_id": user_id,
        "push_enabled": pref.push_enabled,
        "sms_enabled": pref.sms_enabled,
        "email_enabled": pref.email_enabled,
        "in_app_enabled": pref.in_app_enabled,
        "whatsapp_enabled": pref.whatsapp_enabled,
        "quiet_hours_start": pref.quiet_hours_start,
        "quiet_hours_end": pref.quiet_hours_end,
        "language": pref.language,
    }
