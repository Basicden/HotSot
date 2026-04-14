"""HotSot Kitchen Service — Kitchen Routes."""

import time
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_session, KitchenModel
from app.core.engine import KitchenEngine, KitchenOrder, kitchen_engine
from app.core.redis_client import get_kitchen_load, set_kitchen_load

router = APIRouter()


@router.post("/{kitchen_id}/enqueue")
async def enqueue_order(
    kitchen_id: str,
    order_id: str,
    complexity: int = 1,
    station_type: str = "default",
    user_tier: int = 0,
):
    """Add an order to the kitchen queue with priority scoring."""
    if kitchen_engine.is_critical(kitchen_id):
        raise HTTPException(status_code=429, detail="Kitchen critically overloaded. Order throttled.")

    order = KitchenOrder(
        order_id=order_id,
        kitchen_id=kitchen_id,
        prep_complexity=complexity,
        station_type=station_type,
        user_tier=user_tier,
        delay_risk=0.0,
        eta_deadline=time.time() + 600,  # 10 min default
    )

    kitchen_engine.enqueue(order)

    # Update load in Redis
    load = kitchen_engine.get_queue_depth(kitchen_id)
    await set_kitchen_load(kitchen_id, load)

    return {
        "order_id": order_id,
        "kitchen_id": kitchen_id,
        "priority_score": kitchen_engine.priority_score(order),
        "queue_depth": load,
        "status": "QUEUED",
    }


@router.get("/{kitchen_id}/next")
async def get_next_order(kitchen_id: str):
    """Get the next highest-priority order for kitchen to prepare."""
    order = kitchen_engine.dequeue(kitchen_id)
    if not order:
        return {"order_id": None, "status": "QUEUE_EMPTY"}

    load = kitchen_engine.get_queue_depth(kitchen_id)
    await set_kitchen_load(kitchen_id, load)

    return {
        "order_id": order.order_id,
        "priority_score": kitchen_engine.priority_score(order),
        "complexity": order.prep_complexity,
        "station_type": order.station_type,
        "remaining_queue": load,
    }


@router.get("/{kitchen_id}/status")
async def get_kitchen_status(kitchen_id: str):
    """Get kitchen status including load, overload, and batch suggestions."""
    load = kitchen_engine.get_queue_depth(kitchen_id)
    redis_load = await get_kitchen_load(kitchen_id)

    return {
        "kitchen_id": kitchen_id,
        "queue_depth": load,
        "redis_load": redis_load,
        "is_overloaded": kitchen_engine.is_overloaded(kitchen_id),
        "is_critical": kitchen_engine.is_critical(kitchen_id),
        "suggested_batches": [
            [{"order_id": o.order_id, "complexity": o.prep_complexity} for o in batch]
            for batch in kitchen_engine.suggest_batches(kitchen_id)
        ],
    }


@router.get("/{kitchen_id}/batches")
async def get_batch_suggestions(kitchen_id: str):
    """Get smart batch suggestions for efficient cooking."""
    batches = kitchen_engine.suggest_batches(kitchen_id)
    return {
        "kitchen_id": kitchen_id,
        "batch_count": len(batches),
        "batches": [
            {
                "batch_id": i,
                "station_type": batch[0].station_type if batch else None,
                "orders": [
                    {"order_id": o.order_id, "complexity": o.prep_complexity}
                    for o in batch
                ],
            }
            for i, batch in enumerate(batches)
        ],
    }


@router.post("/")
async def create_kitchen(
    name: str,
    capacity: int = 20,
    session: AsyncSession = Depends(get_session),
):
    """Register a new kitchen in the system."""
    kitchen = KitchenModel(name=name, capacity=capacity)
    session.add(kitchen)
    await session.commit()
    await session.refresh(kitchen)

    return {
        "kitchen_id": str(kitchen.id),
        "name": kitchen.name,
        "capacity": kitchen.capacity,
    }
