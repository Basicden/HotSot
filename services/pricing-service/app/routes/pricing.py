"""
HotSot Pricing Service — Routes.

Production-grade pricing with:
    - Rule creation with type, priority, time windows, conditions
    - Price calculation with conflict detection and capping
    - Support for surge, discount, tier-based, and time-based rules
    - Minimum and maximum price bounds
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.auth.jwt import get_current_user, require_role
from shared.utils.helpers import now_iso
from app.core.database import PricingRuleModel

logger = logging.getLogger(__name__)

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


# Valid rule types with their behavior
RULE_TYPES = {
    "SURGE": {
        "description": "Price multiplier during high demand",
        "direction": "increase",
        "default_priority": 10,
        "max_multiplier": 3.0,  # Cap at 3x
    },
    "DISCOUNT": {
        "description": "Percentage or flat discount",
        "direction": "decrease",
        "default_priority": 20,
        "max_multiplier": 1.0,
    },
    "TIER_DISCOUNT": {
        "description": "Discount based on user tier",
        "direction": "decrease",
        "default_priority": 30,
        "max_multiplier": 1.0,
    },
    "TIME_BASED": {
        "description": "Surge or discount based on time of day",
        "direction": "variable",
        "default_priority": 15,
        "max_multiplier": 2.0,
    },
    "HAPPY_HOUR": {
        "description": "Fixed discount during happy hours",
        "direction": "decrease",
        "default_priority": 25,
        "max_multiplier": 1.0,
    },
}

# Maximum final price cannot exceed base * MAX_PRICE_CAP
MAX_PRICE_CAP_MULTIPLIER = 3.0
# Minimum final price cannot go below base * MIN_PRICE_FLOOR
MIN_PRICE_FLOOR_MULTIPLIER = 0.5


def round_price(amount: Decimal) -> Decimal:
    """Round price to 2 decimal places (INR)."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@router.post("/rule")
async def create_pricing_rule(
    vendor_id: str,
    rule_type: str,
    name: str,
    multiplier: Decimal = Decimal("1.0"),
    flat_amount: Decimal = Decimal("0.0"),
    conditions: Optional[dict] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    priority: int = 0,
    user: dict = Depends(require_role("vendor_admin", "admin")),
    session: AsyncSession = Depends(get_session),
):
    """
    Create a pricing rule for a vendor.

    Rule types:
    - SURGE: Multiplier > 1.0 during high demand (max 3x)
    - DISCOUNT: Multiplier < 1.0 or flat_amount < 0
    - TIER_DISCOUNT: Conditions must specify applicable tiers
    - TIME_BASED: Active only during start_time-end_time window
    - HAPPY_HOUR: Fixed discount during specific hours
    """
    tenant_id = user.get("tenant_id", "default")

    # Validate rule type
    if rule_type not in RULE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid rule type: {rule_type}. Supported: {list(RULE_TYPES.keys())}",
        )

    rule_config = RULE_TYPES[rule_type]

    # Validate multiplier bounds
    if multiplier <= Decimal("0"):
        raise HTTPException(status_code=400, detail="Multiplier must be positive")

    if multiplier > Decimal(str(rule_config["max_multiplier"])):
        raise HTTPException(
            status_code=400,
            detail=f"Multiplier {multiplier} exceeds max {rule_config['max_multiplier']} for {rule_type}",
        )

    # Validate surge rules: multiplier must be >= 1.0
    if rule_type == "SURGE" and multiplier < Decimal("1.0"):
        raise HTTPException(status_code=400, detail="Surge multiplier must be >= 1.0")

    # Validate discount rules: multiplier must be <= 1.0
    if rule_type == "DISCOUNT" and multiplier > Decimal("1.0"):
        raise HTTPException(status_code=400, detail="Discount multiplier must be <= 1.0")

    # Set priority if not provided
    if priority == 0:
        priority = rule_config["default_priority"]

    rule = PricingRuleModel(
        tenant_id=tenant_id,
        vendor_id=vendor_id,
        rule_type=rule_type,
        name=name,
        multiplier=multiplier,
        flat_amount=flat_amount,
        conditions=conditions or {},
        start_time=start_time,
        end_time=end_time,
        priority=priority,
        is_active=True,
    )
    session.add(rule)
    await session.commit()

    logger.info(
        f"Pricing rule created: vendor={vendor_id} type={rule_type} "
        f"name={name} multiplier={multiplier}"
    )

    return {
        "rule_id": str(rule.id),
        "name": name,
        "rule_type": rule_type,
        "multiplier": multiplier,
        "flat_amount": flat_amount,
        "priority": priority,
    }


@router.post("/calculate")
async def calculate_price(
    vendor_id: str,
    base_amount: Decimal,
    user_tier: str = "FREE",
    time_of_day: Optional[int] = None,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Calculate final price after applying pricing rules.

    Rules are applied with conflict detection:
    1. Only ONE SURGE rule applies (highest priority)
    2. Multiple DISCOUNT rules can stack (up to a cap)
    3. TIER_DISCOUNT applies if user_tier matches
    4. TIME_BASED rules only apply if within their time window
    5. Final price is bounded by MIN_PRICE_FLOOR and MAX_PRICE_CAP

    Calculation order:
    1. Start with base_amount
    2. Apply SURGE (multiplicative)
    3. Apply TIME_BASED (multiplicative, if active)
    4. Apply DISCOUNT rules (additive, capped)
    5. Apply TIER_DISCOUNT (if applicable)
    6. Add flat_amount adjustments
    7. Clamp to min/max bounds
    """
    tenant_id = user.get("tenant_id", "default")

    if base_amount <= Decimal("0"):
        raise HTTPException(status_code=400, detail="Base amount must be positive")

    # Fetch all active rules for this vendor
    result = await session.execute(
        select(PricingRuleModel).where(
            PricingRuleModel.tenant_id == tenant_id,
            PricingRuleModel.vendor_id == vendor_id,
            PricingRuleModel.is_active == True,
        ).order_by(PricingRuleModel.priority.desc())
    )
    rules = result.scalars().all()

    base = base_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    current_amount = base
    applied_rules: List[Dict[str, Any]] = []
    surge_applied = False
    total_discount_pct = Decimal("0")

    for rule in rules:
        # Check time window
        if not _is_rule_active_now(rule, time_of_day):
            continue

        # Check conditions
        if not _are_conditions_met(rule, user_tier):
            continue

        multiplier = Decimal(str(rule.multiplier))
        flat = Decimal(str(rule.flat_amount))

        if rule.rule_type == "SURGE":
            # Only one surge applies (highest priority wins)
            if surge_applied:
                continue
            prev = current_amount
            current_amount = round_price(current_amount * multiplier)
            surge_applied = True
            applied_rules.append({
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "type": "SURGE",
                "multiplier": str(multiplier),
                "adjustment": str(current_amount - prev),
            })

        elif rule.rule_type == "TIME_BASED":
            prev = current_amount
            current_amount = round_price(current_amount * multiplier)
            applied_rules.append({
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "type": "TIME_BASED",
                "multiplier": str(multiplier),
                "adjustment": str(current_amount - prev),
            })

        elif rule.rule_type == "DISCOUNT":
            # Track discount percentage for capping
            discount_pct = (Decimal("1") - multiplier) * Decimal("100")
            total_discount_pct += discount_pct
            # Cap total discount at 50%
            if total_discount_pct > 50:
                logger.info(f"Discount cap reached: total={total_discount_pct}% — skipping {rule.name}")
                continue
            prev = current_amount
            current_amount = round_price(current_amount * multiplier)
            if flat != 0:
                current_amount = round_price(current_amount + flat)
            applied_rules.append({
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "type": "DISCOUNT",
                "multiplier": str(multiplier),
                "flat_adjustment": str(flat),
                "adjustment": str(current_amount - prev),
            })

        elif rule.rule_type == "TIER_DISCOUNT":
            prev = current_amount
            current_amount = round_price(current_amount * multiplier)
            if flat != 0:
                current_amount = round_price(current_amount + flat)
            applied_rules.append({
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "type": "TIER_DISCOUNT",
                "tier": user_tier,
                "multiplier": str(multiplier),
                "flat_adjustment": str(flat),
                "adjustment": str(current_amount - prev),
            })

        elif rule.rule_type == "HAPPY_HOUR":
            prev = current_amount
            current_amount = round_price(current_amount * multiplier)
            if flat != 0:
                current_amount = round_price(current_amount + flat)
            applied_rules.append({
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "type": "HAPPY_HOUR",
                "multiplier": str(multiplier),
                "flat_adjustment": str(flat),
                "adjustment": str(current_amount - prev),
            })

    # Apply price bounds
    min_price = round_price(base * Decimal(str(MIN_PRICE_FLOOR_MULTIPLIER)))
    max_price = round_price(base * Decimal(str(MAX_PRICE_CAP_MULTIPLIER)))
    bounded = False

    if current_amount < min_price:
        current_amount = min_price
        bounded = True
    elif current_amount > max_price:
        current_amount = max_price
        bounded = True

    return {
        "base_amount": str(base),
        "final_amount": str(current_amount),
        "applied_rules": applied_rules,
        "rules_count": len(applied_rules),
        "surge_applied": surge_applied,
        "total_discount_pct": str(total_discount_pct),
        "price_bounded": bounded,
        "min_price": str(min_price),
        "max_price": str(max_price),
    }


def _is_rule_active_now(rule: PricingRuleModel, time_of_day: Optional[int] = None) -> bool:
    """
    Check if a rule is currently active based on its time window.

    If the rule has no start_time/end_time, it's always active.
    If time_of_day is provided (0-23), check against the rule's window.
    """
    if not rule.start_time and not rule.end_time:
        return True

    if time_of_day is None:
        # If no time provided, use current hour
        time_of_day = datetime.now(timezone.utc).hour

    # Parse rule times (expected format: "HH:MM" or just hour int as string)
    try:
        start_h = int(str(rule.start_time).split(":")[0]) if rule.start_time else 0
        end_h = int(str(rule.end_time).split(":")[0]) if rule.end_time else 23
    except (ValueError, IndexError):
        return True  # If can't parse, assume active

    return start_h <= time_of_day <= end_h


def _are_conditions_met(rule: PricingRuleModel, user_tier: str) -> bool:
    """
    Check if a rule's conditions are met.

    For TIER_DISCOUNT: conditions must include the user's tier.
    For other types: conditions are optional.
    """
    if not rule.conditions:
        return True

    if rule.rule_type == "TIER_DISCOUNT":
        applicable_tiers = rule.conditions.get("applicable_tiers", [])
        if applicable_tiers and user_tier not in applicable_tiers:
            return False

    return True


@router.get("/rules/{vendor_id}")
async def list_pricing_rules(
    vendor_id: str,
    active_only: bool = True,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """List all pricing rules for a vendor."""
    tenant_id = user.get("tenant_id", "default")

    query = select(PricingRuleModel).where(
        PricingRuleModel.tenant_id == tenant_id,
        PricingRuleModel.vendor_id == vendor_id,
    )
    if active_only:
        query = query.where(PricingRuleModel.is_active == True)

    query = query.order_by(PricingRuleModel.priority.desc())
    result = await session.execute(query)
    rules = result.scalars().all()

    return {
        "vendor_id": vendor_id,
        "rules": [{
            "rule_id": str(r.id),
            "name": r.name,
            "rule_type": r.rule_type,
            "multiplier": str(r.multiplier) if r.multiplier else None,
            "flat_amount": str(r.flat_amount) if r.flat_amount else None,
            "priority": r.priority,
            "is_active": r.is_active,
            "start_time": r.start_time,
            "end_time": r.end_time,
        } for r in rules],
        "total": len(rules),
    }
