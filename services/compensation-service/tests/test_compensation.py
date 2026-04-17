"""HotSot Compensation Service — Unit Tests.

Tests cover:
1. Compensation engine: calculation by reason type
2. Compensation engine: percentage, cap, auto-approve logic
3. Refund trigger: Kafka event emission, reference generation
"""
from unittest.mock import AsyncMock

import pytest

from app.core.engine import CompensationEngine, DEFAULT_RULES


# ═══════════════════════════════════════════════════════════════
# COMPENSATION CALCULATION
# ═══════════════════════════════════════════════════════════════

class TestCompensationCalculation:
    """Test compensation amount calculation."""

    def test_shelf_expired_full_refund(self):
        """SHELF_EXPIRED should give 100% refund."""
        engine = CompensationEngine()
        result = engine.calculate_compensation("SHELF_EXPIRED", "500.00")
        assert result["compensation_type"] == "FULL_REFUND"
        assert result["percentage"] == 100.0
        assert result["compensation_amount"] == 500.0

    def test_kitchen_failure_full_refund(self):
        """KITCHEN_FAILURE should give 100% refund."""
        engine = CompensationEngine()
        result = engine.calculate_compensation("KITCHEN_FAILURE", "300.00")
        assert result["compensation_type"] == "FULL_REFUND"
        assert result["compensation_amount"] == 300.0

    def test_delay_15min_partial_refund(self):
        """DELAY_15MIN should give 20% refund."""
        engine = CompensationEngine()
        result = engine.calculate_compensation("DELAY_15MIN", "500.00")
        assert result["compensation_type"] == "PARTIAL_REFUND"
        assert result["percentage"] == 20.0
        assert result["compensation_amount"] == 100.0

    def test_delay_30min_partial_refund(self):
        """DELAY_30MIN should give 50% refund."""
        engine = CompensationEngine()
        result = engine.calculate_compensation("DELAY_30MIN", "400.00")
        assert result["compensation_type"] == "PARTIAL_REFUND"
        assert result["percentage"] == 50.0
        assert result["compensation_amount"] == 200.0

    def test_quality_issue_partial_not_auto_approved(self):
        """QUALITY_ISSUE should be 50% refund, NOT auto-approved."""
        engine = CompensationEngine()
        result = engine.calculate_compensation("QUALITY_ISSUE", "600.00")
        assert result["compensation_type"] == "PARTIAL_REFUND"
        assert result["percentage"] == 50.0
        assert result["auto_approve"] is False

    def test_unknown_reason_defaults_to_10_percent(self):
        """Unknown reason should default to 10% refund."""
        engine = CompensationEngine()
        result = engine.calculate_compensation("UNKNOWN_REASON", "500.00")
        assert result["compensation_amount"] == 50.0
        assert result["auto_approve"] is False

    def test_compensation_capped_at_5000(self):
        """Compensation should be capped at ₹5000."""
        engine = CompensationEngine()
        result = engine.calculate_compensation("SHELF_EXPIRED", "10000.00")
        assert result["compensation_amount"] == 5000.0

    def test_compensation_currency_is_inr(self):
        """Currency should always be INR."""
        engine = CompensationEngine()
        result = engine.calculate_compensation("SHELF_EXPIRED", "100.00")
        assert result["currency"] == "INR"

    def test_auto_approve_for_full_refund_reasons(self):
        """Full refund reasons should be auto-approved."""
        for reason in ["SHELF_EXPIRED", "KITCHEN_FAILURE", "PAYMENT_CONFLICT",
                       "EXPIRED_NOT_PICKED", "WRONG_ORDER"]:
            engine = CompensationEngine()
            result = engine.calculate_compensation(reason, "100.00")
            assert result["auto_approve"] is True, f"{reason} should be auto-approved"


# ═══════════════════════════════════════════════════════════════
# REFUND TRIGGER
# ═══════════════════════════════════════════════════════════════

class TestRefundTrigger:
    """Test refund triggering."""

    @pytest.mark.asyncio
    async def test_trigger_refund_returns_processing(self):
        """Trigger refund should return PROCESSING status."""
        engine = CompensationEngine(redis_client=None, kafka_producer=None)
        result = await engine.trigger_refund(
            case_id="case-1", order_id="order-1",
            payment_ref="pay-1", amount=500.0, reason="SHELF_EXPIRED",
        )
        assert result["status"] == "PROCESSING"
        assert "refund_ref" in result
        assert result["amount"] == 500.0

    @pytest.mark.asyncio
    async def test_trigger_refund_publishes_kafka_event(self):
        """Trigger refund should publish Kafka event if producer available."""
        mock_kafka = AsyncMock()
        engine = CompensationEngine(redis_client=None, kafka_producer=mock_kafka)
        await engine.trigger_refund(
            case_id="case-1", order_id="order-1",
            payment_ref="pay-1", amount=300.0, reason="DELAY_30MIN",
            tenant_id="t1",
        )
        mock_kafka.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_trigger_refund_without_kafka_no_error(self):
        """Trigger refund without Kafka should not raise."""
        engine = CompensationEngine(redis_client=None, kafka_producer=None)
        result = await engine.trigger_refund(
            case_id="case-1", order_id="order-1",
            payment_ref="pay-1", amount=200.0, reason="DELAY_15MIN",
        )
        assert result["status"] == "PROCESSING"


# ═══════════════════════════════════════════════════════════════
# DEFAULT RULES VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestDefaultRules:
    """Test that DEFAULT_RULES have expected structure."""

    def test_all_rules_have_required_keys(self):
        """Each rule should have type, percentage, auto_approve."""
        for reason, rule in DEFAULT_RULES.items():
            assert "type" in rule, f"Rule {reason} missing 'type'"
            assert "percentage" in rule, f"Rule {reason} missing 'percentage'"
            assert "auto_approve" in rule, f"Rule {reason} missing 'auto_approve'"

    def test_all_percentages_are_positive(self):
        """All rule percentages should be positive."""
        for reason, rule in DEFAULT_RULES.items():
            assert rule["percentage"] > 0, f"Rule {reason} has non-positive percentage"

    def test_all_percentages_at_most_100(self):
        """No rule should exceed 100% refund."""
        for reason, rule in DEFAULT_RULES.items():
            assert rule["percentage"] <= 100, f"Rule {reason} exceeds 100%"
