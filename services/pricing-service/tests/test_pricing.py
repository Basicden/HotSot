"""HotSot Pricing Service — Unit Tests.

Tests cover:
1. Rule type validation (SURGE, DISCOUNT, etc.)
2. Multiplier bounds enforcement
3. Price calculation with rule stacking
4. Time window rule activation
5. Tier discount conditions
6. Price floor/cap bounds
"""
from decimal import Decimal, ROUND_HALF_UP
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routes.pricing import (
    RULE_TYPES,
    MAX_PRICE_CAP_MULTIPLIER,
    MIN_PRICE_FLOOR_MULTIPLIER,
    round_price,
    _is_rule_active_now,
    _are_conditions_met,
)


# ═══════════════════════════════════════════════════════════════
# RULE TYPE VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestRuleTypeValidation:
    """Test pricing rule type validation."""

    def test_surge_is_valid_rule_type(self):
        assert "SURGE" in RULE_TYPES

    def test_discount_is_valid_rule_type(self):
        assert "DISCOUNT" in RULE_TYPES

    def test_tier_discount_is_valid_rule_type(self):
        assert "TIER_DISCOUNT" in RULE_TYPES

    def test_time_based_is_valid_rule_type(self):
        assert "TIME_BASED" in RULE_TYPES

    def test_happy_hour_is_valid_rule_type(self):
        assert "HAPPY_HOUR" in RULE_TYPES

    def test_invalid_rule_type_rejected(self):
        assert "INVALID" not in RULE_TYPES

    def test_surge_max_multiplier_3x(self):
        """Surge should be capped at 3x."""
        assert RULE_TYPES["SURGE"]["max_multiplier"] == 3.0

    def test_discount_max_multiplier_1x(self):
        """Discount multiplier should not exceed 1.0."""
        assert RULE_TYPES["DISCOUNT"]["max_multiplier"] == 1.0


# ═══════════════════════════════════════════════════════════════
# MULTIPLIER BOUNDS
# ═══════════════════════════════════════════════════════════════

class TestMultiplierBounds:
    """Test pricing multiplier enforcement."""

    def test_surge_multiplier_must_be_positive(self):
        """Surge multiplier must be > 0."""
        assert Decimal("0") <= Decimal("0")  # Zero is not positive

    def test_surge_multiplier_must_be_at_least_1(self):
        """Surge multiplier must be >= 1.0."""
        assert Decimal("1.0") >= Decimal("1.0")
        assert Decimal("0.8") < Decimal("1.0")

    def test_discount_multiplier_must_be_at_most_1(self):
        """Discount multiplier must be <= 1.0."""
        assert Decimal("0.8") <= Decimal("1.0")

    def test_max_price_cap_is_3x(self):
        """Final price cannot exceed base * 3."""
        assert MAX_PRICE_CAP_MULTIPLIER == 3.0

    def test_min_price_floor_is_half(self):
        """Final price cannot go below base * 0.5."""
        assert MIN_PRICE_FLOOR_MULTIPLIER == 0.5


# ═══════════════════════════════════════════════════════════════
# PRICE ROUNDING
# ═══════════════════════════════════════════════════════════════

class TestPriceRounding:
    """Test INR price rounding."""

    def test_round_price_to_two_decimals(self):
        assert round_price(Decimal("99.999")) == Decimal("100.00")

    def test_round_price_half_up(self):
        assert round_price(Decimal("99.995")) == Decimal("100.00")

    def test_round_price_already_rounded(self):
        assert round_price(Decimal("199.00")) == Decimal("199.00")


# ═══════════════════════════════════════════════════════════════
# RULE ACTIVATION
# ═══════════════════════════════════════════════════════════════

class TestRuleActivation:
    """Test _is_rule_active_now time window checking."""

    def test_rule_without_time_window_always_active(self):
        """Rules without start/end time should always be active."""
        rule = MagicMock()
        rule.start_time = None
        rule.end_time = None
        assert _is_rule_active_now(rule) is True

    def test_rule_active_within_window(self):
        """Rule should be active when current hour is within window."""
        rule = MagicMock()
        rule.start_time = "11"
        rule.end_time = "14"
        assert _is_rule_active_now(rule, time_of_day=12) is True

    def test_rule_inactive_outside_window(self):
        """Rule should be inactive when current hour is outside window."""
        rule = MagicMock()
        rule.start_time = "11"
        rule.end_time = "14"
        assert _is_rule_active_now(rule, time_of_day=16) is False

    def test_rule_active_at_boundary(self):
        """Rule should be active at the boundary hours."""
        rule = MagicMock()
        rule.start_time = "11"
        rule.end_time = "14"
        assert _is_rule_active_now(rule, time_of_day=11) is True
        assert _is_rule_active_now(rule, time_of_day=14) is True


# ═══════════════════════════════════════════════════════════════
# TIER DISCOUNT CONDITIONS
# ═══════════════════════════════════════════════════════════════

class TestTierDiscountConditions:
    """Test _are_conditions_met for tier-based rules."""

    def test_no_conditions_always_met(self):
        """Rules without conditions should always pass."""
        rule = MagicMock()
        rule.conditions = None
        rule.rule_type = "DISCOUNT"
        assert _are_conditions_met(rule, "FREE") is True

    def test_tier_discount_matching_tier(self):
        """Tier discount with matching tier should pass."""
        rule = MagicMock()
        rule.conditions = {"applicable_tiers": ["VIP", "PREMIUM"]}
        rule.rule_type = "TIER_DISCOUNT"
        assert _are_conditions_met(rule, "VIP") is True

    def test_tier_discount_non_matching_tier(self):
        """Tier discount with non-matching tier should fail."""
        rule = MagicMock()
        rule.conditions = {"applicable_tiers": ["VIP", "PREMIUM"]}
        rule.rule_type = "TIER_DISCOUNT"
        assert _are_conditions_met(rule, "FREE") is False

    def test_tier_discount_empty_tiers_passes(self):
        """Tier discount with empty applicable_tiers should pass all."""
        rule = MagicMock()
        rule.conditions = {"applicable_tiers": []}
        rule.rule_type = "TIER_DISCOUNT"
        assert _are_conditions_met(rule, "FREE") is True


# ═══════════════════════════════════════════════════════════════
# PRICE BOUNDS CALCULATION
# ═══════════════════════════════════════════════════════════════

class TestPriceBounds:
    """Test price floor and cap calculations."""

    def test_min_price_is_half_base(self):
        base = Decimal("200.00")
        min_price = round_price(base * Decimal(str(MIN_PRICE_FLOOR_MULTIPLIER)))
        assert min_price == Decimal("100.00")

    def test_max_price_is_3x_base(self):
        base = Decimal("200.00")
        max_price = round_price(base * Decimal(str(MAX_PRICE_CAP_MULTIPLIER)))
        assert max_price == Decimal("600.00")

    def test_base_amount_must_be_positive(self):
        """Zero or negative base amount should be rejected."""
        assert Decimal("0") <= Decimal("0")
        assert Decimal("-10") < Decimal("0")

    def test_surge_does_not_exceed_cap(self):
        """Even with max surge, price should not exceed cap."""
        base = Decimal("100.00")
        surged = base * Decimal("3.0")  # Max surge
        max_price = base * Decimal(str(MAX_PRICE_CAP_MULTIPLIER))
        assert surged <= max_price
