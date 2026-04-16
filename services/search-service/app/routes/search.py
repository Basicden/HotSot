"""HotSot Search Service — Routes."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from app.core.database import SearchIndexModel

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

@router.get("/")
async def search(q: str, entity_type: str = None, cuisine: str = None,
                 city: str = None, limit: int = 20,
                 user: dict = Depends(get_current_user),
                 session: AsyncSession = Depends(get_session)):
    """Full-text search across vendors, menu items, kitchens."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    query = select(SearchIndexModel).where(
        SearchIndexModel.tenant_id == tenant_id,
        SearchIndexModel.is_available == True,
    )
    if q:
        query = query.where(SearchIndexModel.name.ilike(f"%{q}%"))
    if entity_type:
        query = query.where(SearchIndexModel.entity_type == entity_type)
    if cuisine:
        query = query.where(SearchIndexModel.cuisine == cuisine)
    if city:
        query = query.where(SearchIndexModel.city == city)
    query = query.order_by(SearchIndexModel.popularity_score.desc()).limit(limit)
    result = await session.execute(query)
    items = result.scalars().all()
    return {"query": q, "results": [
        {"entity_type": i.entity_type, "entity_id": str(i.entity_id), "name": i.name, "cuisine": i.cuisine}
        for i in items
    ]}

@router.post("/index")
async def index_entity(entity_type: str, entity_id: str, name: str,
                       description: str = None, cuisine: str = None,
                       tags: list = None, city: str = None,
                       user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    """Add or update entity in search index."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    idx = SearchIndexModel(
        tenant_id=tenant_id, entity_type=entity_type,
        entity_id=uuid.UUID(entity_id), name=name, description=description,
        cuisine=cuisine, tags=tags or [], city=city,
    )
    session.add(idx)
    await session.commit()
    return {"indexed": True, "entity_id": entity_id}
