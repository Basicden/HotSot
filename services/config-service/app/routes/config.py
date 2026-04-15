"""HotSot Config Service — Routes."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from app.core.database import ConfigModel

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

@router.get("/{service_name}/{config_key}")
async def get_config(service_name: str, config_key: str,
                     user: dict = Depends(get_current_user),
                     session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(ConfigModel).where(
            ConfigModel.tenant_id == uuid.UUID(tenant_id),
            ConfigModel.service_name == service_name,
            ConfigModel.config_key == config_key,
        ))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return {"key": config.config_key, "value": config.config_value}

@router.put("/{service_name}/{config_key}")
async def set_config(service_name: str, config_key: str, config_value: dict,
                     user: dict = Depends(require_role("admin")),
                     session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(ConfigModel).where(
            ConfigModel.tenant_id == uuid.UUID(tenant_id),
            ConfigModel.service_name == service_name,
            ConfigModel.config_key == config_key,
        ))
    config = result.scalar_one_or_none()
    if config:
        config.config_value = config_value
        config.updated_at = datetime.now(timezone.utc)
    else:
        config = ConfigModel(
            tenant_id=uuid.UUID(tenant_id), service_name=service_name,
            config_key=config_key, config_value=config_value,
        )
        session.add(config)
    await session.commit()
    return {"key": config_key, "updated": True}
