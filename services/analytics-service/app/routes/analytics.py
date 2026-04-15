"""HotSot Analytics Service — Routes."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from app.core.database import AnalyticsEventModel, DailyMetricModel

router = APIRouter()
_session_factory = None

def set_dependencies(session_factory, redis_client=None, kafka_producer=None):
    global _session_factory
    _session_factory = session_factory

async def get_session():
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session

@router.post("/event")
async def track_event(event_name: str, properties: dict = None,
                      entity_type: str = None, entity_id: str = None,
                      user: dict = Depends(get_current_user),
                      session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    event = AnalyticsEventModel(
        tenant_id=uuid.UUID(tenant_id), event_name=event_name,
        properties=properties or {}, entity_type=entity_type,
        entity_id=uuid.UUID(entity_id) if entity_id else None,
    )
    session.add(event)
    await session.commit()
    return {"tracked": True, "event": event_name}

@router.get("/dashboard")
async def get_dashboard(vendor_id: str = None, days: int = 7,
                        user: dict = Depends(get_current_user),
                        session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(DailyMetricModel).where(
        DailyMetricModel.tenant_id == uuid.UUID(tenant_id),
        DailyMetricModel.metric_date >= since,
    )
    if vendor_id:
        query = query.where(DailyMetricModel.vendor_id == uuid.UUID(vendor_id))
    query = query.order_by(DailyMetricModel.metric_date.desc())
    result = await session.execute(query)
    metrics = result.scalars().all()
    return {"metrics": [
        {"date": m.metric_date.isoformat() if m.metric_date else None,
         "total_orders": m.total_orders, "revenue": m.total_revenue,
         "avg_prep_time": m.avg_prep_time_seconds, "satisfaction": m.customer_satisfaction}
        for m in metrics
    ]}
