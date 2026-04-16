"""HotSot Menu Service — Routes."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from app.core.database import MenuItemModel, MenuCategoryModel

router = APIRouter()
_session_factory = None
_redis_client = None
_kafka_producer = None

def set_dependencies(session_factory, redis_client=None, kafka_producer=None):
    global _session_factory, _redis_client, _kafka_producer
    _session_factory = session_factory
    _redis_client = redis_client
    _kafka_producer = kafka_producer

async def get_session():
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session

@router.post("/item")
async def create_menu_item(name: str, vendor_id: str, price: Decimal, is_veg: bool = True,
                           category: str = None, batch_category: str = None, prep_time_seconds: int = 300,
                           user: dict = Depends(require_role("vendor_admin")),
                           session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    price = price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    item = MenuItemModel(tenant_id=tenant_id, vendor_id=uuid.UUID(vendor_id),
                         name=name, price=price, is_veg=is_veg, category=category,
                         batch_category=batch_category, prep_time_seconds=prep_time_seconds)
    session.add(item)
    await session.commit()
    return {"item_id": str(item.id), "name": name, "price": str(price)}

@router.get("/vendor/{vendor_id}")
async def get_vendor_menu(vendor_id: str, user: dict = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(MenuItemModel).where(MenuItemModel.vendor_id == uuid.UUID(vendor_id),
                                     MenuItemModel.tenant_id == tenant_id,
                                     MenuItemModel.is_available == True).order_by(MenuItemModel.sort_order))
    items = result.scalars().all()
    return {"vendor_id": vendor_id, "items": [
        {"item_id": str(i.id), "name": i.name, "price": str(Decimal(str(i.price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)), "is_veg": i.is_veg, "category": i.category}
        for i in items
    ]}

@router.put("/item/{item_id}/availability")
async def toggle_availability(item_id: str, is_available: bool,
                               user: dict = Depends(require_role("vendor_admin")),
                               session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(MenuItemModel).where(MenuItemModel.id == uuid.UUID(item_id)))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.is_available = is_available
    await session.commit()
    return {"item_id": item_id, "is_available": is_available}
