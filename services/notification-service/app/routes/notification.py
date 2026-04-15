"""HotSot Notification Service — Routes."""
import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user
from app.core.database import NotificationTemplateModel, NotificationLogModel
from app.core.dispatcher import NotificationDispatcher

router = APIRouter()
_session_factory = None
_redis_client = None

def set_dependencies(session_factory, redis_client):
    global _session_factory, _redis_client
    _session_factory = session_factory
    _redis_client = redis_client

async def get_session():
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session

@router.post("/send")
async def send_notification(
    user_id: str,
    channel: str,
    title: str,
    body: str,
    order_id: Optional[str] = None,
    priority: str = "NORMAL",
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Send a notification to a user."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    dispatcher = NotificationDispatcher(_redis_client)
    result = await dispatcher.dispatch(
        user_id=user_id, channel=channel, title=title, body=body,
        order_id=order_id, tenant_id=tenant_id, priority=priority,
    )
    # Log
    log = NotificationLogModel(
        tenant_id=uuid.UUID(tenant_id),
        user_id=uuid.UUID(user_id),
        order_id=uuid.UUID(order_id) if order_id else None,
        channel=channel, title=title, body=body,
        status=result.get("status", "UNKNOWN"),
        provider_ref=result.get("provider_ref"),
    )
    session.add(log)
    await session.commit()
    return result

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
         "created_at": n.created_at.isoformat() if n.created_at else None}
        for n in notifications
    ]}

@router.post("/template")
async def create_template(
    template_key: str, channel: str, body_template: str,
    title_template: Optional[str] = None, language: str = "en",
    user: dict = Depends(get_current_user),
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
