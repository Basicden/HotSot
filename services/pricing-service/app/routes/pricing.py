"""
HotSot Pricing Service — Routes.

Production-grade pricing with:
    - Rule creation with type, priority, time windows, conditions
    - Price calculation with conflict detection and capping
    - Support for surge, discount, tier-based, and time-based rules
    - Minimum and maximum price bounds

Uses shared Money class for all monetary calculations (Decimal-based, INR rounding).

Bug fixes (Idea #17):
    1. Flat discounts now tracked as equivalent % toward 50% cap
    2. Floor check after discounts — price never goes below min_price
    3. Discounts calculated against BASE, not surged price
    4. Replaced local round_price() with shared Money / round_inr
    5. Discounts are additive against base (not multiplicative stacking)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.auth.jwt import get_current_user, require_role
from shared.money import Money, round_inr
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

# Maximum total discount percentage (including flat-amount equivalent)
MAX_TOTAL_DISCOUNT_PCT = Decimal("50")


# ───────────────────────────────────────────────────────────────
# Endpoints
# ───────────────────────────────────────────────────────────────

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
    if rule_type in ("DISCOUNT", "TIER_DISCOUNT", "HAPPY_HOUR") and multiplier > Decimal("1.0"):
        raise HTTPException(status_code=400, detail=f"{rule_type} multiplier must be <= 1.0")

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
    2. Multiple DISCOUNT rules stack additively against BASE (up to 50% cap)
    3. TIER_DISCOUNT applies if user_tier matches
    4. TIME_BASED rules only apply if within their time window
    5. Final price is bounded by MIN_PRICE_FLOOR and MAX_PRICE_CAP

    Calculation order (two-phase):
    Phase 1 — Price increases:
      1. Start with base_amount
      2. Apply SURGE (multiplicative, highest priority only)
      3. Apply TIME_BASED (multiplicative, if active)
    Phase 2 — Price decreases (additive against BASE):
      4. Calculate each discount against the original BASE amount
      5. Sum all discount amounts (percentage + flat) additively
      6. Cap total discount at 50% of BASE (percentage + flat equivalent)
      7. Subtract total discount from surged amount
      8. Floor at min_price — price never goes below base * 0.5
    """
    tenant_id = user.get("tenant_id", "default")

    if base_amount <= Decimal("0"):
        raise HTTPException(status_code=400, detail="Base amount must be positive")

    # Fetch all active rules for this vendor, ordered by priority desc
    result = await session.execute(
        select(PricingRuleModel).where(
            PricingRuleModel.tenant_id == tenant_id,
            PricingRuleModel.vendor_id == vendor_id,
            PricingRuleModel.is_active == True,
        ).order_by(PricingRuleModel.priority.desc())
    )
    rules = result.scalars().all()

    # ── Money initialization ──────────────────────────────────
    base = Money(base_amount)
    current_amount = Money(base)
    min_price = Money(base.amount * Decimal(str(MIN_PRICE_FLOOR_MULTIPLIER)))
    max_price = Money(base.amount * Decimal(str(MAX_PRICE_CAP_MULTIPLIER)))

    applied_rules: List[Dict[str, Any]] = []
    surge_applied = False

    # ══════════════════════════════════════════════════════════
    # Phase 1: Apply SURGE and TIME_BASED rules (multiplicative)
    # ══════════════════════════════════════════════════════════
    for rule in rules:
        if not _is_rule_active_now(rule, time_of_day):
            continue
        if not _are_conditions_met(rule, user_tier):
            continue

        multiplier = Decimal(str(rule.multiplier))

        if rule.rule_type == "SURGE":
            # Only one surge applies (highest priority wins)
            if surge_applied:
                continue
            prev = Money(current_amount)
            current_amount = current_amount * multiplier
            surge_applied = True
            applied_rules.append({
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "type": "SURGE",
                "multiplier": str(multiplier),
                "adjustment": str((current_amount - prev).amount),
            })

        elif rule.rule_type == "TIME_BASED":
            prev = Money(current_amount)
            current_amount = current_amount * multiplier
            applied_rules.append({
                "rule_id": str(rule.id),
                "rule_name": rule.name,
                "type": "TIME_BASED",
                "multiplier": str(multiplier),
                "adjustment": str((current_amount - prev).amount),
            })

    # ══════════════════════════════════════════════════════════
    # Phase 2: Collect discounts against BASE (additive, capped)
    #
    # Fix #5: Discounts are additive against BASE, not
    #   multiplicative against current_amount.
    #   Two 10% discounts = 20% off base, NOT 0.9 * 0.9 = 19%.
    #
    # Fix #1: Flat discounts are converted to equivalent
    #   percentage of base and tracked in total_discount_pct.
    #
    # Fix #3: Each discount is calculated against the original
    #   base, so a 10% discount always means 10% of base,
    #   regardless of surge.
    # ══════════════════════════════════════════════════════════
    total_discount_pct = Decimal("0")
    total_discount_amount = Money("0.00")

    for rule in rules:
        if not _is_rule_active_now(rule, time_of_day):
            continue
        if not _are_conditions_met(rule, user_tier):
            continue

        if rule.rule_type not in ("DISCOUNT", "TIER_DISCOUNT", "HAPPY_HOUR"):
            continue

        multiplier = Decimal(str(rule.multiplier))
        flat = Decimal(str(rule.flat_amount))

        # -- Percentage discount against BASE --
        pct_discount = (Decimal("1") - multiplier) * Decimal("100")

        # -- Flat discount equivalent percentage against BASE --
        # Fix #1: flat_amount is converted to equivalent % of base
        # so the 50% cap properly accounts for both types.
        if flat != Decimal("0") and base.is_positive:
            flat_abs = abs(flat)
            flat_pct = (flat_abs / base.amount) * Decimal("100")
        else:
            flat_pct = Decimal("0")

        # Check if adding this discount exceeds the 50% cap
        proposed_total_pct = total_discount_pct + pct_discount + flat_pct
        if proposed_total_pct > MAX_TOTAL_DISCOUNT_PCT:
            logger.info(
                f"Discount cap reached: proposed={proposed_total_pct}% "
                f"(existing={total_discount_pct}% + "
                f"pct={pct_discount}% + flat_eq={flat_pct}%) "
                f"— skipping rule '{rule.name}'"
            )
            continue

        # Approved — update tracking
        total_discount_pct = proposed_total_pct

        # Calculate discount amounts against BASE (additive)
        pct_discount_amount = base * (Decimal("1") - multiplier)
        flat_discount_amount = (
            Money(str(abs(flat))) if flat != Decimal("0") else Money("0.00")
        )
        total_discount_amount = total_discount_amount + pct_discount_amount + flat_discount_amount

        # Build applied rule entry
        rule_entry: Dict[str, Any] = {
            "rule_id": str(rule.id),
            "rule_name": rule.name,
            "type": rule.rule_type,
            "multiplier": str(multiplier),
            "flat_adjustment": str(flat),
            "adjustment": str(-(pct_discount_amount + flat_discount_amount).amount),
        }
        if rule.rule_type == "TIER_DISCOUNT":
            rule_entry["tier"] = user_tier
        applied_rules.append(rule_entry)

    # ── Subtract total discount from surged amount ────────────
    # Money.__sub__ allows negative intermediate results
    current_amount = current_amount - total_discount_amount

    # ══════════════════════════════════════════════════════════
    # Phase 3: Clamp to min/max bounds
    #
    # Fix #2: Floor at min_price — price never goes negative.
    # Even if total_discount_amount > current_amount, the
    # result is clamped to min_price (base * 0.5).
    # ══════════════════════════════════════════════════════════
    bounded = False
    if current_amount.amount < min_price.amount:
        logger.info(
            f"Price floor applied: {current_amount} < min {min_price}"
        )
        current_amount = min_price
        bounded = True
    elif current_amount.amount > max_price.amount:
        logger.info(
            f"Price cap applied: {current_amount} > max {max_price}"
        )
        current_amount = max_price
        bounded = True

    return {
        "base_amount": base.to_db(),
        "final_amount": current_amount.to_db(),
        "applied_rules": applied_rules,
        "rules_count": len(applied_rules),
        "surge_applied": surge_applied,
        "total_discount_pct": str(round_inr(total_discount_pct)),
        "price_bounded": bounded,
        "min_price": min_price.to_db(),
        "max_price": max_price.to_db(),
    }


# ───────────────────────────────────────────────────────────────
# Helper functions
# ───────────────────────────────────────────────────────────────

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
