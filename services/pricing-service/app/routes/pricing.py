"""HotSot Pricing Service — Routes."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from app.core.database import PricingRuleModel

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

@router.post("/rule")
async def create_pricing_rule(vendor_id: str, rule_type: str, name: str,
                              multiplier: float = 1.0, flat_amount: float = 0.0,
                              conditions: dict = None,
                              user: dict = Depends(require_role("vendor_admin")),
                              session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    rule = PricingRuleModel(tenant_id=uuid.UUID(tenant_id), vendor_id=uuid.UUID(vendor_id),
                            rule_type=rule_type, name=name, multiplier=multiplier,
                            flat_amount=flat_amount, conditions=conditions or {})
    session.add(rule)
    await session.commit()
    return {"rule_id": str(rule.id), "name": name, "rule_type": rule_type}

@router.post("/calculate")
async def calculate_price(vendor_id: str, base_amount: float, user_tier: str = "FREE",
                          time_of_day: int = None, user: dict = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)):
    """Calculate final price after applying pricing rules."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(PricingRuleModel).where(
            PricingRuleModel.tenant_id == uuid.UUID(tenant_id),
            PricingRuleModel.vendor_id == uuid.UUID(vendor_id),
            PricingRuleModel.is_active == True,
        ).order_by(PricingRuleModel.priority.desc()))
    rules = result.scalars().all()

    final_amount = base_amount
    applied_rules = []
    for rule in rules:
        prev = final_amount
        final_amount = final_amount * rule.multiplier + rule.flat_amount
        applied_rules.append({"rule": rule.name, "type": rule.rule_type,
                              "adjustment": round(final_amount - prev, 2)})

    return {"base_amount": base_amount, "final_amount": round(final_amount, 2),
            "applied_rules": applied_rules}
