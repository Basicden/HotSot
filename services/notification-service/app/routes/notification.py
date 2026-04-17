"""HotSot Notification Service — Routes.

Production-grade notification dispatch with:
    - Multi-channel support: PUSH, SMS, EMAIL, IN_APP, WHATSAPP
    - Rate limiting per user per hour (20/hr for NORMAL, unlimited for HIGH/URGENT)
    - User preference checking before sending
    - TRAI DND compliance for India
    - Template-based notifications via event type
    - Deduplication via Redis
    - Persistent in-app notification storage
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from shared.auth.jwt import get_current_user, require_role
from app.core.database import (
    NotificationTemplateModel, NotificationLogModel,
    NotificationPreferenceModel, InAppNotificationModel,
)
from app.core.dispatcher import NotificationDispatcher
from app.core.templates import NotificationTemplates

router = APIRouter()
_session_factory = None
_redis_client = None

# Template engine instance
_template_engine = NotificationTemplates()


def set_dependencies(session_factory, redis_client):
    global _session_factory, _redis_client
    _session_factory = session_factory
    _redis_client = redis_client


async def get_session():
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session


# ═══════════════════════════════════════════════════════════════
# NOTIFICATION DISPATCH
# ═══════════════════════════════════════════════════════════════

@router.post("/send")
async def send_notification(
    user_id: str,
    channel: str,
    title: str,
    body: str,
    order_id: Optional[str] = None,
    priority: str = "NORMAL",
    phone: Optional[str] = None,
    email: Optional[str] = None,
    fcm_token: Optional[str] = None,
    wa_id: Optional[str] = None,
    template_id: Optional[str] = None,
    language: str = "en",
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Send a notification to a user.

    Channels: PUSH, SMS, EMAIL, IN_APP, WHATSAPP

    Rate limiting: 20 per hour per user per channel for NORMAL priority.
    HIGH/URGENT priority is exempt from rate limiting.

    User preferences are checked before sending — if the user has
    disabled a channel, the notification is blocked.
    """
    if channel not in ("PUSH", "SMS", "EMAIL", "IN_APP", "WHATSAPP"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown channel: {channel}. Supported: PUSH, SMS, EMAIL, IN_APP, WHATSAPP",
        )

    if priority not in ("LOW", "NORMAL", "HIGH", "URGENT"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown priority: {priority}. Supported: LOW, NORMAL, HIGH, URGENT",
        )

    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    dispatcher = NotificationDispatcher(_redis_client)
    result = await dispatcher.dispatch(
        user_id=user_id, channel=channel, title=title, body=body,
        order_id=order_id, tenant_id=tenant_id, priority=priority,
        phone=phone, email=email, fcm_token=fcm_token, wa_id=wa_id,
        template_id=template_id, language=language,
        session=session,
    )

    # Commit the session (dispatcher already created/updated the log entry)
    await session.commit()
    return result


# ═══════════════════════════════════════════════════════════════
# TEMPLATE-BASED NOTIFICATION
# ═══════════════════════════════════════════════════════════════

@router.post("/send-event")
async def send_event_notification(
    user_id: str,
    event_type: str,
    channel: str = "IN_APP",
    order_id: Optional[str] = None,
    priority: str = "NORMAL",
    extra_data: Optional[dict] = None,
    language: str = "en",
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Send a notification using an event type template.

    Event types: order.created, order.ready, order.on_shelf, order.picked,
    order.cancelled, kitchen.new_order, etc.

    The template engine renders title and message from the event type
    and extra_data, then dispatches through the specified channel.
    """
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    # Render template
    render_data = {
        "order_id": order_id or "N/A",
        **(extra_data or {}),
    }
    title, body = _template_engine.render(event_type, render_data, lang=language)

    # Auto-select channel for urgent events
    if event_type in ("order.cancelled", "order.expired"):
        channel = "SMS"
        priority = "URGENT"
    elif event_type in ("order.on_shelf", "order.ready"):
        channel = "PUSH"
        priority = "HIGH"

    dispatcher = NotificationDispatcher(_redis_client)
    result = await dispatcher.dispatch(
        user_id=user_id, channel=channel, title=title, body=body,
        order_id=order_id, tenant_id=tenant_id, priority=priority,
        template_id=event_type, language=language, session=session,
    )

    # Commit the session (dispatcher already created/updated the log entry)
    await session.commit()
    return result


# ═══════════════════════════════════════════════════════════════
# NOTIFICATION HISTORY
# ═══════════════════════════════════════════════════════════════

@router.get("/user/{user_id}")
async def get_user_notifications(
    user_id: str,
    limit: int = 20,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get notification history for a user."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(NotificationLogModel).where(
            NotificationLogModel.user_id == uuid.UUID(user_id),
            NotificationLogModel.tenant_id == uuid.UUID(tenant_id),
        ).order_by(NotificationLogModel.created_at.desc()).limit(limit)
    )
    notifications = result.scalars().all()
    return {"user_id": user_id, "notifications": [
        {"id": str(n.id), "channel": n.channel, "title": n.title, "status": n.status,
         "template_key": n.template_key,
         "created_at": n.created_at.isoformat() if n.created_at else None}
        for n in notifications
    ]}


# ═══════════════════════════════════════════════════════════════
# IN-APP NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

@router.get("/in-app/{user_id}")
async def get_in_app_notifications(
    user_id: str,
    unread_only: bool = False,
    limit: int = 50,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get in-app notifications for a user (read/unread)."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    query = select(InAppNotificationModel).where(
        InAppNotificationModel.user_id == uuid.UUID(user_id),
        InAppNotificationModel.tenant_id == uuid.UUID(tenant_id),
    )

    if unread_only:
        query = query.where(InAppNotificationModel.is_read == False)

    query = query.order_by(InAppNotificationModel.created_at.desc()).limit(limit)
    result = await session.execute(query)
    notifications = result.scalars().all()

    # Count unread
    count_result = await session.execute(
        select(func.count(InAppNotificationModel.id)).where(
            InAppNotificationModel.user_id == uuid.UUID(user_id),
            InAppNotificationModel.tenant_id == uuid.UUID(tenant_id),
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
                "data": n.data,
                "is_read": n.is_read,
                "priority": n.priority,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ],
    }


@router.put("/in-app/{notification_id}/read")
async def mark_in_app_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Mark an in-app notification as read."""
    result = await session.execute(
        select(InAppNotificationModel).where(
            InAppNotificationModel.id == uuid.UUID(notification_id),
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.is_read = True
    notification.read_at = datetime.now(timezone.utc)
    await session.commit()
    return {"notification_id": notification_id, "read": True}


# ═══════════════════════════════════════════════════════════════
# NOTIFICATION PREFERENCES
# ═══════════════════════════════════════════════════════════════

@router.get("/preferences/{user_id}")
async def get_notification_preferences(
    user_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get notification preferences for a user."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(NotificationPreferenceModel).where(
            NotificationPreferenceModel.user_id == uuid.UUID(user_id),
            NotificationPreferenceModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    pref = result.scalar_one_or_none()
    if not pref:
        # Return defaults
        return {
            "user_id": user_id,
            "push_enabled": True,
            "sms_enabled": True,
            "email_enabled": True,
            "in_app_enabled": True,
            "whatsapp_enabled": False,
            "quiet_hours_start": 21,
            "quiet_hours_end": 9,
            "language": "en",
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


@router.put("/preferences/{user_id}")
async def update_notification_preferences(
    user_id: str,
    push_enabled: Optional[bool] = None,
    sms_enabled: Optional[bool] = None,
    email_enabled: Optional[bool] = None,
    in_app_enabled: Optional[bool] = None,
    whatsapp_enabled: Optional[bool] = None,
    quiet_hours_start: Optional[int] = None,
    quiet_hours_end: Optional[int] = None,
    language: Optional[str] = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Update notification preferences for a user."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))

    result = await session.execute(
        select(NotificationPreferenceModel).where(
            NotificationPreferenceModel.user_id == uuid.UUID(user_id),
            NotificationPreferenceModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    pref = result.scalar_one_or_none()

    if not pref:
        # Create with defaults then update
        pref = NotificationPreferenceModel(
            tenant_id=uuid.UUID(tenant_id),
            user_id=uuid.UUID(user_id),
        )
        session.add(pref)
        await session.flush()

    if push_enabled is not None:
        pref.push_enabled = push_enabled
    if sms_enabled is not None:
        pref.sms_enabled = sms_enabled
    if email_enabled is not None:
        pref.email_enabled = email_enabled
    if in_app_enabled is not None:
        pref.in_app_enabled = in_app_enabled
    if whatsapp_enabled is not None:
        pref.whatsapp_enabled = whatsapp_enabled
    if quiet_hours_start is not None:
        pref.quiet_hours_start = quiet_hours_start
    if quiet_hours_end is not None:
        pref.quiet_hours_end = quiet_hours_end
    if language is not None:
        pref.language = language

    pref.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"user_id": user_id, "updated": True}


# ═══════════════════════════════════════════════════════════════
# TEMPLATES
# ═══════════════════════════════════════════════════════════════

@router.post("/template")
async def create_template(
    template_key: str, channel: str, body_template: str,
    title_template: Optional[str] = None, language: str = "en",
    user: dict = Depends(require_role("admin", "vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """Create a notification template."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    template = NotificationTemplateModel(
        tenant_id=uuid.UUID(tenant_id), template_key=template_key,
        channel=channel, language=language,
        title_template=title_template, body_template=body_template,
    )
    session.add(template)
    await session.commit()
    return {"template_key": template_key, "created": True}


@router.get("/templates/events")
async def list_event_types():
    """List all available event types for template-based notifications."""
    return {
        "event_types": _template_engine.list_event_types(),
        "languages": ["en", "hi"],
    }
