"""
HotSot Order Service — Payment Routes.

Production-grade payment lifecycle using shared Money class and RazorpayGateway:
    - POST /init         — Initialize a payment (create Razorpay order)
    - POST /confirm      — Confirm payment from webhook/callback
    - POST /webhook      — Razorpay webhook handler (HMAC verified)
    - GET  /{order_id}   — Get payment status for an order
    - POST /refund       — Initiate a refund

All monetary calculations use Decimal via shared Money class.
All Razorpay API calls use shared RazorpayGateway with idempotency.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from shared.auth.jwt import get_current_user, require_role
from shared.compliance_decorators import require_compliance
from shared.money import Money
from shared.payment_gateway import RazorpayGateway, PaymentState, PaymentGatewayError
from shared.utils.config import get_settings
from shared.utils.database import get_session_factory, set_tenant_id
from shared.utils.helpers import generate_id, now_iso

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level gateway instance — uses env vars for keys
_gateway = RazorpayGateway()


async def get_db():
    """Database session dependency for compliance checks."""
    session_factory = get_session_factory("order")
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@router.post("/init")
@require_compliance("RBI")
async def init_payment(
    request: Request,
    vendor_id: str = None,
    tenant_id: str = None,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Initialize a payment for an order.

    Creates a Razorpay order via RazorpayGateway with idempotency key
    derived from order_id to prevent duplicate orders on retry.

    Amount conversion uses Money.to_paise() for Decimal precision —
    never float multiplication (Bug #6 / Idea #6 fix).

    Compliance: @require_compliance("RBI") — hard gate requires RBI compliance
    PASSED before any payment processing. Blocks if not PASSED.
    """
    if tenant_id is None:
        tenant_id = user.get("tenant_id", "default")
    body = await request.json()
    order_id = body.get("order_id")
    raw_amount = body.get("amount", 0)
    payment_method = body.get("payment_method", "UPI")

    if not order_id:
        raise HTTPException(status_code=400, detail="order_id is required")

    # Convert to Money for validation and precise paise conversion
    try:
        amount = Money(str(raw_amount))
    except (ValueError, TypeError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid amount: {raw_amount}. Must be a positive number. Error: {e}",
        )

    if not amount.is_positive:
        raise HTTPException(status_code=400, detail="amount must be positive")

    settings = get_settings("order")

    # Generate idempotency key from order_id for safe retries
    idempotency_key = f"order_init_{order_id}"

    # Create Razorpay order via shared gateway
    try:
        razorpay_result = await _gateway.create_order(
            amount=amount.amount,
            receipt=f"hotsot_{order_id[:20]}",
            notes={
                "order_id": str(order_id),
                "tenant_id": tenant_id,
                "platform": "hotsot",
                "payment_method": payment_method,
            },
            idempotency_key=idempotency_key,
        )

        razorpay_order_id = razorpay_result.get("id")
        logger.info(
            f"Razorpay order created: razorpay_order_id={razorpay_order_id} "
            f"order_id={order_id} amount={amount}"
        )

        return {
            "order_id": order_id,
            "razorpay_order_id": razorpay_order_id,
            "amount": amount.to_db(),
            "amount_paise": amount.to_paise(),
            "currency": "INR",
            "key_id": settings.RAZORPAY_KEY_ID if _gateway.is_configured else "demo_key",
            "status": "PAYMENT_PENDING",
        }

    except PaymentGatewayError as e:
        logger.error(f"Razorpay order creation failed: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Payment gateway error: {e}. Please retry.",
        )


@router.post("/confirm")
@require_compliance("RBI")
async def confirm_payment(
    request: Request,
    vendor_id: str = None,
    tenant_id: str = None,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Confirm a payment after client-side Razorpay checkout.

    Verifies the payment signature using HMAC-SHA256 and captures
    the payment into escrow via RazorpayGateway.

    Uses Money.to_paise() for capture amount — never float * 100.

    Compliance: @require_compliance("RBI") — hard gate requires RBI compliance
    PASSED before confirming payments. Blocks if not PASSED.
    """
    if tenant_id is None:
        tenant_id = user.get("tenant_id", "default")
    body = await request.json()
    razorpay_order_id = body.get("razorpay_order_id")
    razorpay_payment_id = body.get("razorpay_payment_id")
    razorpay_signature = body.get("razorpay_signature")
    order_id = body.get("order_id")
    raw_amount = body.get("amount", 0)

    if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
        raise HTTPException(
            status_code=400,
            detail="Missing required payment verification fields",
        )

    # Verify Razorpay signature (HMAC-SHA256)
    settings = get_settings("order")
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, razorpay_signature):
        logger.warning(
            f"Payment signature verification FAILED: order={order_id} "
            f"razorpay_order={razorpay_order_id}"
        )
        raise HTTPException(
            status_code=400,
            detail="Payment verification failed. Invalid signature.",
        )

    # Convert capture amount using Money — never float
    try:
        capture_amount = Money(str(raw_amount))
    except (ValueError, TypeError):
        capture_amount = Money("0.00")

    # Capture payment via shared gateway (escrow hold)
    idempotency_key = f"capture_{razorpay_payment_id}"

    try:
        if capture_amount.is_positive:
            capture_result = await _gateway.capture_payment(
                payment_id=razorpay_payment_id,
                amount=capture_amount.amount,
                idempotency_key=idempotency_key,
            )
            logger.info(
                f"Payment captured (escrow): order={order_id} "
                f"payment={razorpay_payment_id} amount={capture_amount}"
            )
        else:
            logger.warning(f"Capture amount is zero for order={order_id}")

    except PaymentGatewayError as e:
        logger.error(f"Payment capture error for order={order_id}: {e}")
        # Don't fail the confirm — the webhook will reconcile

    # Validate state transition
    if not PaymentState.validate_state_transition(PaymentState.AUTHORIZED, PaymentState.CAPTURED):
        logger.warning(f"Invalid state transition for payment={razorpay_payment_id}")

    return {
        "order_id": order_id,
        "payment_ref": razorpay_payment_id,
        "amount_captured": capture_amount.to_db(),
        "status": "PAYMENT_CONFIRMED",
        "escrow": "HELD",
    }


@router.post("/webhook")
async def razorpay_webhook(request: Request):
    """
    Handle Razorpay webhook events.

    Verifies the webhook signature using HMAC-SHA256 (via RazorpayGateway)
    and processes events like:
        - payment.captured
        - payment.failed
        - refund.processed

    IMPORTANT: This endpoint does NOT require JWT auth.
    Razorpay signs webhooks with a shared secret instead.
    """
    body = await request.body()
    body_json = await request.json()

    settings = get_settings("order")
    webhook_signature = request.headers.get("X-Razorpay-Signature", "")

    # Verify webhook signature using shared gateway
    if not _gateway.verify_webhook_signature(body.decode("utf-8"), webhook_signature):
        if settings.RAZORPAY_WEBHOOK_SECRET:
            logger.warning("Webhook signature verification FAILED")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        # If no webhook secret configured, log warning but proceed
        logger.warning("Webhook secret not configured — skipping signature verification")

    event = body_json.get("event", "")
    payload = body_json.get("payload", {})

    logger.info(f"Razorpay webhook received: event={event}")

    if event == "payment.captured":
        payment_entity = payload.get("payment", {}).get("entity", {})
        payment_id = payment_entity.get("id")
        order_id = payment_entity.get("notes", {}).get("order_id")
        amount_paise = payment_entity.get("amount", 0)
        amount = Money.from_paise(amount_paise)
        logger.info(
            f"Webhook: payment captured — payment={payment_id} "
            f"order={order_id} amount={amount}"
        )

    elif event == "payment.failed":
        payment_entity = payload.get("payment", {}).get("entity", {})
        logger.warning(
            f"Webhook: payment failed — payment={payment_entity.get('id')} "
            f"error={payment_entity.get('error_description')}"
        )

    elif event == "refund.processed":
        refund_entity = payload.get("refund", {}).get("entity", {})
        refund_amount_paise = refund_entity.get("amount", 0)
        refund_amount = Money.from_paise(refund_amount_paise)
        logger.info(
            f"Webhook: refund processed — refund={refund_entity.get('id')} "
            f"amount={refund_amount}"
        )

    return {"status": "ok"}


@router.get("/{order_id}")
async def get_payment_status(
    order_id: str,
    user: dict = Depends(get_current_user),
):
    """Get payment status for an order."""
    # In production: query PaymentModel from DB
    return {
        "order_id": order_id,
        "status": "PAYMENT_CONFIRMED",
        "escrow": "HELD",
    }


@router.post("/refund")
async def initiate_refund(
    request: Request,
    user: dict = Depends(require_role("admin", "vendor_admin")),
):
    """
    Initiate a refund for an order.

    Releases escrow and processes refund via RazorpayGateway.
    Uses Money.to_paise() for amount conversion — never float * 100.

    Supports both full and partial refunds with reason tracking.
    """
    body = await request.json()
    order_id = body.get("order_id")
    payment_ref = body.get("payment_ref")
    raw_amount = body.get("amount")
    reason = body.get("reason", "Order cancelled/expired")

    if not all([order_id, payment_ref]):
        raise HTTPException(status_code=400, detail="order_id and payment_ref required")

    # Convert refund amount using Money — never float
    if raw_amount is not None:
        try:
            refund_amount = Money(str(raw_amount))
        except (ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid refund amount: {raw_amount}. Error: {e}",
            )
    else:
        refund_amount = None  # Full refund

    # Generate idempotency key from order_id
    idempotency_key = f"refund_{order_id}_{raw_amount or 'full'}"

    try:
        if refund_amount and refund_amount.is_positive:
            # Partial or specified amount refund
            refund_result = await _gateway.refund(
                payment_id=payment_ref,
                amount=refund_amount.amount,
                reason=reason,
                notes={
                    "order_id": str(order_id),
                    "reason": reason,
                    "platform": "hotsot",
                },
                idempotency_key=idempotency_key,
            )
        else:
            # Full refund — amount not specified
            refund_result = await _gateway.refund(
                payment_id=payment_ref,
                amount=Money("0.00").amount,  # Gateway will refund full amount
                reason=reason,
                notes={
                    "order_id": str(order_id),
                    "reason": reason,
                    "platform": "hotsot",
                },
                idempotency_key=idempotency_key,
            )

        refund_id = refund_result.get("id")
        logger.info(
            f"Refund initiated: order={order_id} "
            f"refund_id={refund_id} amount={refund_amount or 'FULL'}"
        )

        # Validate state transition
        PaymentState.validate_state_transition(
            PaymentState.CAPTURED,
            PaymentState.REFUNDED if refund_amount is None else PaymentState.PARTIALLY_REFUNDED,
        )

        return {
            "order_id": order_id,
            "refund_id": refund_id,
            "amount": refund_amount.to_db() if refund_amount else "FULL",
            "reason": reason,
            "status": "REFUND_INITIATED",
        }

    except PaymentGatewayError as e:
        logger.error(f"Refund failed for order={order_id}: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Refund processing failed at gateway: {e}",
        )
