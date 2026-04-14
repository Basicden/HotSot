"""HotSot Compensation Service — Unit Tests."""
from app.core.engine import CompensationEngine

def test_evaluate_shelf_expired():
    ce = CompensationEngine()
    result = ce.evaluate(
        order_id="o_001", kitchen_id="k_001",
        reason="SHELF_EXPIRED", order_amount=298.00,
        delay_minutes=0, is_duplicate=False,
    )
    assert result["refund_type"] == "FULL_REFUND"
    assert result["amount"] == 298.00

def test_evaluate_delay_exceeded():
    ce = CompensationEngine()
    result = ce.evaluate(
        order_id="o_002", kitchen_id="k_001",
        reason="DELAY_EXCEEDED", order_amount=200.00,
        delay_minutes=22, is_duplicate=False,
    )
    assert result["refund_type"] == "PARTIAL_REFUND"
    assert result["amount"] == 100.00  # 50% of 200

def test_evaluate_kitchen_failure():
    ce = CompensationEngine()
    result = ce.evaluate(
        order_id="o_003", kitchen_id="k_001",
        reason="KITCHEN_FAILURE", order_amount=349.00,
        delay_minutes=0, is_duplicate=False,
    )
    assert result["refund_type"] == "FULL_REFUND"
    assert result["kitchen_penalty"] is True

def test_duplicate_skipped():
    ce = CompensationEngine()
    result = ce.evaluate(
        order_id="o_004", kitchen_id="k_001",
        reason="SHELF_EXPIRED", order_amount=100.00,
        delay_minutes=0, is_duplicate=True,
    )
    assert result["status"] == "DUPLICATE_SKIPPED"

def test_payment_conflict():
    ce = CompensationEngine()
    result = ce.evaluate(
        order_id="o_005", kitchen_id="k_001",
        reason="PAYMENT_CONFLICT", order_amount=250.00,
        delay_minutes=0, is_duplicate=False,
    )
    assert result["refund_type"] == "FULL_REFUND"
    assert result["kitchen_penalty"] is False

def test_customer_complaint():
    ce = CompensationEngine()
    result = ce.evaluate(
        order_id="o_006", kitchen_id="k_001",
        reason="CUSTOMER_COMPLAINT", order_amount=300.00,
        delay_minutes=0, is_duplicate=False,
    )
    assert result["refund_type"] == "CREDIT_NOTE"
