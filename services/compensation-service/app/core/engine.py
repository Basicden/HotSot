"""HotSot Compensation Service — Compensation Engine."""
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from shared.utils.helpers import generate_id, now_iso

logger = logging.getLogger("compensation-service.engine")

# Default compensation rules for Indian food delivery
DEFAULT_RULES = {
    "SHELF_EXPIRED": {"type": "FULL_REFUND", "percentage": 100.0, "auto_approve": True},
    "KITCHEN_FAILURE": {"type": "FULL_REFUND", "percentage": 100.0, "auto_approve": True},
    "PAYMENT_CONFLICT": {"type": "FULL_REFUND", "percentage": 100.0, "auto_approve": True},
    "EXPIRED_NOT_PICKED": {"type": "FULL_REFUND", "percentage": 100.0, "auto_approve": True},
    "DELAY_15MIN": {"type": "PARTIAL_REFUND", "percentage": 20.0, "auto_approve": True},
    "DELAY_30MIN": {"type": "PARTIAL_REFUND", "percentage": 50.0, "auto_approve": True},
    "WRONG_ORDER": {"type": "FULL_REFUND", "percentage": 100.0, "auto_approve": True},
    "QUALITY_ISSUE": {"type": "PARTIAL_REFUND", "percentage": 50.0, "auto_approve": False},
}


class CompensationEngine:
    """Calculates compensation amounts and triggers refund flow.

    Integrates with Razorpay for actual refunds.
    """

    def __init__(self, redis_client=None, kafka_producer=None):
        self._redis = redis_client
        self._kafka = kafka_producer

    def calculate_compensation(self, reason: str, order_amount: float,
                                tenant_id: str = None) -> Dict[str, Any]:
        """Calculate compensation amount based on reason and order amount."""
        rule = DEFAULT_RULES.get(reason, {"type": "PARTIAL_REFUND", "percentage": 10.0, "auto_approve": False})

        amount = round(order_amount * (rule["percentage"] / 100.0), 2)
        # Cap at max (default 5000 INR)
        amount = min(amount, 5000.0)

        return {
            "reason": reason,
            "compensation_type": rule["type"],
            "percentage": rule["percentage"],
            "order_amount": order_amount,
            "compensation_amount": amount,
            "currency": "INR",
            "auto_approve": rule["auto_approve"],
        }

    async def trigger_refund(self, case_id: str, order_id: str,
                              payment_ref: str, amount: float,
                              reason: str, tenant_id: str = None) -> Dict[str, Any]:
        """Trigger refund via Razorpay.

        Production: calls Razorpay refunds API
        """
        # Razorpay refund API call (stub)
        refund_ref = f"rfnd_{generate_id()[:12]}"

        logger.info("refund_triggered",
                     case_id=case_id, order_id=order_id,
                     amount=amount, reason=reason)

        # Publish compensation event
        if self._kafka:
            await self._kafka.publish(
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
                        "amount": amount,
                        "reason": reason,
                        "refund_ref": refund_ref,
                    },
                },
            )

        return {
            "status": "PROCESSING",
            "refund_ref": refund_ref,
            "amount": amount,
        }
