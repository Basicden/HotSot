"""HotSot Shelf Service — Shelf Routes."""

import time
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.redis_client import acquire_shelf_lock, release_shelf_lock, get_shelf_status, set_shelf_status, get_shelf_ttl
from app.core.ttl_engine import ttl_engine

router = APIRouter()


class ShelfAssignRequest(BaseModel):
    order_id: str
    kitchen_id: str
    zone: str = "HOT"


class ShelfReleaseRequest(BaseModel):
    order_id: str


@router.post("/assign")
async def assign_shelf(req: ShelfAssignRequest):
    """Assign an order to an available shelf slot with distributed lock."""
    shelf_id = ttl_engine.find_available(req.kitchen_id, req.zone)
    if not shelf_id:
        raise HTTPException(status_code=503, detail="No shelves available. Redirect to wait zone.")

    lock_acquired = await acquire_shelf_lock(shelf_id, req.order_id)
    if not lock_acquired:
        raise HTTPException(status_code=409, detail=f"Shelf {shelf_id} is occupied. Race condition detected.")

    success = ttl_engine.assign_order(shelf_id, req.order_id)
    if not success:
        await release_shelf_lock(shelf_id, req.order_id)
        raise HTTPException(status_code=409, detail="Shelf assignment failed")

    await set_shelf_status(shelf_id, {
        "status": "OCCUPIED", "order_id": req.order_id,
        "kitchen_id": req.kitchen_id, "assigned_at": time.time(),
        "ttl_seconds": ttl_engine.shelves[shelf_id].ttl_seconds,
    })

    return {
        "shelf_id": shelf_id, "order_id": req.order_id,
        "zone": ttl_engine.shelves[shelf_id].temperature_zone,
        "ttl_seconds": ttl_engine.shelves[shelf_id].ttl_seconds, "status": "OCCUPIED",
    }


@router.post("/{shelf_id}/release")
async def release_shelf(shelf_id: str, req: ShelfReleaseRequest):
    """Release a shelf slot after order pickup."""
    released = await release_shelf_lock(shelf_id, req.order_id)
    if not released:
        raise HTTPException(status_code=409, detail="Lock release failed. Not the owner.")
    order_id = ttl_engine.release_shelf(shelf_id)
    if not order_id:
        raise HTTPException(status_code=404, detail="Shelf not found or already available")
    await set_shelf_status(shelf_id, {"status": "AVAILABLE", "order_id": None})
    return {"shelf_id": shelf_id, "order_id": order_id, "status": "AVAILABLE"}


@router.get("/{kitchen_id}/status")
async def get_kitchen_shelves(kitchen_id: str):
    """Get all shelf statuses for a kitchen."""
    shelves = []
    for shelf_id, shelf in ttl_engine.shelves.items():
        if shelf.kitchen_id != kitchen_id:
            continue
        redis_ttl = await get_shelf_ttl(shelf_id)
        shelves.append({"shelf_id": shelf.shelf_id, "zone": shelf.temperature_zone, "status": shelf.status, "order_id": shelf.current_order_id, "remaining_ttl": shelf.remaining_ttl, "redis_ttl": redis_ttl})
    return {"kitchen_id": kitchen_id, "shelves": shelves}


@router.get("/{kitchen_id}/warnings")
async def get_expiry_warnings(kitchen_id: str):
    """Get shelf TTL expiry warnings for a kitchen."""
    warnings = ttl_engine.check_expiry_warnings(kitchen_id)
    expired = ttl_engine.process_expired(kitchen_id)
    return {"warnings": warnings, "expired": expired, "total_warnings": len(warnings), "total_expired": len(expired)}


@router.post("/{kitchen_id}/init")
async def init_kitchen_shelves(kitchen_id: str, count: int = 10, zone: str = "HOT"):
    """Initialize shelf slots for a kitchen."""
    for i in range(1, count + 1):
        shelf_id = f"{kitchen_id}-A{i}"
        ttl_engine.register_shelf(shelf_id, kitchen_id, zone)
        await set_shelf_status(shelf_id, {"status": "AVAILABLE", "kitchen_id": kitchen_id, "zone": zone})
    return {"kitchen_id": kitchen_id, "shelves_created": count, "zone": zone}
