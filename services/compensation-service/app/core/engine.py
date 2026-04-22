"""HotSot Compensation Service — Compensation Engine."""
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional, Union
from datetime import datetime, timezone
from shared.utils.helpers import generate_id, now_iso


logger = logging.getLogger("compensation-service.engine")

INR_QUANTIZE = Decimal("0.01")
MAX_COMPENSATION_AMOUNT = Decimal("5000.00")

def round_inr(amount: Decimal) -> Decimal:
    """Round to INR 2 decimal places."""
    return amount.quantize(INR_QUANTIZE, rounding=ROUND_HALF_UP)

# Default compensation rules for Indian food delivery
DEFAULT_RULES = {
    "SHELF_EXPIRED": {"type": "FULL_REFUND", "percentage": Decimal("100.00"), "auto_approve": True},
    "KITCHEN_FAILURE": {"type": "FULL_REFUND", "percentage": Decimal("100.00"), "auto_approve": True},
    "PAYMENT_CONFLICT": {"type": "FULL_REFUND", "percentage": Decimal("100.00"), "auto_approve": True},
    "EXPIRED_NOT_PICKED": {"type": "FULL_REFUND", "percentage": Decimal("100.00"), "auto_approve": True},
    "DELAY_15MIN": {"type": "PARTIAL_REFUND", "percentage": Decimal("20.00"), "auto_approve": True},
    "DELAY_30MIN": {"type": "PARTIAL_REFUND", "percentage": Decimal("50.00"), "auto_approve": True},
    "WRONG_ORDER": {"type": "FULL_REFUND", "percentage": Decimal("100.00"), "auto_approve": True},
    "QUALITY_ISSUE": {"type": "PARTIAL_REFUND", "percentage": Decimal("50.00"), "auto_approve": False},
}


class CompensationEngine:
    """Calculates compensation amounts and triggers refund flow.

    Integrates with Razorpay for actual refunds.
    All monetary calculations use Decimal to avoid IEEE 754 rounding errors.
    """

    def __init__(self, redis_client=None, kafka_producer=None):
        self._redis = redis_client
        self._kafka = kafka_producer

    def calculate_compensation(self, reason: str, order_amount: Union[Decimal, str, float],
                                tenant_id: str = None,
                                existing_compensation: Union[Decimal, str, float, None] = None) -> Dict[str, Any]:
        """Calculate compensation amount based on reason and order amount.

        Uses Decimal for all monetary arithmetic to prevent rounding errors.
        Float input is converted via string to preserve intent.

        FIX #8: Applied aggregate cap — total compensation never exceeds
        min(order_amount, MAX_COMPENSATION_AMOUNT). If existing_compensation
        is provided (from previous penalties for the same order), the new
        compensation is capped so that the total never exceeds the order amount.

        Args:
            reason: Compensation reason (e.g., SHELF_EXPIRED, DELAY_15MIN).
            order_amount: The original order amount in INR.
            tenant_id: Optional tenant for per-tenant rules.
            existing_compensation: Sum of already-applied compensation for this order.
        """
        # Convert to Decimal — reject raw float, accept str or Decimal
        if isinstance(order_amount, float):
            # Convert float via string to avoid IEEE 754 errors
            order_amount = Decimal(str(order_amount))
        elif isinstance(order_amount, str):
            order_amount = Decimal(order_amount)
        elif isinstance(order_amount, Decimal):
            pass
        else:
            raise TypeError(f"order_amount must be Decimal, str, or float, got {type(order_amount)}")

        rule = DEFAULT_RULES.get(reason, {"type": "PARTIAL_REFUND", "percentage": Decimal("10.00"), "auto_approve": False})

        amount = round_inr(order_amount * (rule["percentage"] / Decimal("100.00")))

        # FIX #8: Aggregate cap — compensation never exceeds order_amount or MAX_COMPENSATION_AMOUNT
        # Step 1: Absolute cap (₹5000 or order amount, whichever is less)
        absolute_cap = min(order_amount, MAX_COMPENSATION_AMOUNT)

        # Step 2: If there's existing compensation, reduce the cap further
        if existing_compensation is not None:
            if isinstance(existing_compensation, float):
                existing_compensation = Decimal(str(existing_compensation))
            elif isinstance(existing_compensation, str):
                existing_compensation = Decimal(existing_compensation)

            remaining_cap = max(Decimal("0.00"), absolute_cap - existing_compensation)
            amount = min(amount, remaining_cap)
        else:
            amount = min(amount, absolute_cap)

        # Step 3: Never allow negative compensation
        amount = max(amount, Decimal("0.00"))

        return {
            "reason": reason,
            "compensation_type": rule["type"],
            "percentage": str(rule["percentage"]),
            "order_amount": str(order_amount),
            "compensation_amount": str(amount),
            "currency": "INR",
            "auto_approve": rule["auto_approve"],
            "aggregate_cap_applied": amount < round_inr(order_amount * (rule["percentage"] / Decimal("100.00"))),
            "absolute_cap": str(absolute_cap),
        }

    async def trigger_refund(self, case_id: str, order_id: str,
                              payment_ref: str, amount: Union[Decimal, str, float],
                              reason: str, tenant_id: str = None) -> Dict[str, Any]:
        """Trigger refund via Razorpay payment gateway.

        Uses the RazorpayGateway for real refund processing with idempotency.
        All monetary amounts use Decimal.
        """
        # Convert to Decimal
        if isinstance(amount, float):
            amount = Decimal(str(amount))
        elif isinstance(amount, str):
            amount = Decimal(amount)
        elif not isinstance(amount, Decimal):
            raise TypeError(f"amount must be Decimal, str, or float, got {type(amount)}")

        # Use RazorpayGateway for real refund processing
        from shared.payment_gateway import RazorpayGateway
        gateway = RazorpayGateway()

        idempotency_key = f"refund_{case_id}_{order_id}"
        try:
            refund_result = await gateway.refund(
                payment_id=payment_ref,
                amount=amount,
                reason=reason,
                notes={"case_id": case_id, "tenant_id": tenant_id or ""},
                idempotency_key=idempotency_key,
            )
            refund_ref = refund_result.get("id", f"rfnd_{generate_id()[:12]}")
            refund_status = refund_result.get("status", "processed")
        except Exception as e:
            logger.error(f"Razorpay refund failed: {e}")
            refund_ref = f"rfnd_failed_{generate_id()[:12]}"
            refund_status = "failed"

        logger.info("refund_triggered",
                     case_id=case_id, order_id=order_id,
                     amount=str(amount), reason=reason,
                     refund_ref=refund_ref, refund_status=refund_status)

        # Publish compensation event
        if self._kafka:
            await self._kafka.publish_raw(
                topic="hotsot.compensation.events.v1",
                key=order_id,
                value={
                    "event_id": generate_id(),
                    "event_type": "COMPENSATION_COMPLETED",
                    "order_id": order_id,
                    "tenant_id": tenant_id,
                    "source": "compensation-service",
                    "timestamp": now_iso(),
                    "schema_version": 2,
                    "payload": {
                        "case_id": case_id,
                        "amount": str(amount),
                        "reason": reason,
                        "refund_ref": refund_ref,
                        "refund_status": refund_status,
                    },
                },
            )

        return {
            "status": "PROCESSING" if refund_status != "failed" else "FAILED",
            "refund_ref": refund_ref,
            "amount": str(amount),
        }
