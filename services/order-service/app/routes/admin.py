"""HotSot Order Service — Admin Routes."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_session, OrderModel, OrderEventModel

router = APIRouter()


@router.get("/stats")
async def get_stats(session: AsyncSession = Depends(get_session)):
    """Get system statistics."""
    # Count orders by status
    result = await session.execute(
        select(OrderModel.status, func.count(OrderModel.id))
        .group_by(OrderModel.status)
    )
    status_counts = {row[0]: row[1] for row in result.all()}

    # Total events
    event_count = await session.execute(select(func.count(OrderEventModel.id)))
    total_events = event_count.scalar()

    return {
        "orders_by_status": status_counts,
        "total_events": total_events,
    }


@router.get("/events/recent")
async def get_recent_events(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """Get recent events across all orders."""
    result = await session.execute(
        select(OrderEventModel)
        .order_by(OrderEventModel.created_at.desc())
        .limit(limit)
    )
    events = result.scalars().all()

    return [{
        "event_id": str(e.id),
        "order_id": str(e.order_id),
        "event_type": e.event_type,
        "payload": e.payload,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in events]
