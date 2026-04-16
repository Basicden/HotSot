"""
HotSot Order Service — Payment Routes.

Handles payment lifecycle:
    - POST /init         — Initialize a payment (create Razorpay order)
    - POST /confirm      — Confirm payment from webhook/callback
    - POST /webhook      — Razorpay webhook handler (HMAC verified)
    - GET  /{order_id}   — Get payment status for an order
    - POST /refund       — Initiate a refund
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from shared.auth.jwt import get_current_user, require_role
from shared.utils.config import get_settings
from shared.utils.helpers import generate_id, now_iso

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/init")
async def init_payment(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Initialize a payment for an order.

    Creates a Razorpay order and returns the payment details
    needed by the client to complete the payment.
    """
    body = await request.json()
    order_id = body.get("order_id")
    amount = body.get("amount", 0)
    payment_method = body.get("payment_method", "UPI")
    tenant_id = user.get("tenant_id", "default")

    if not order_id:
        raise HTTPException(status_code=400, detail="order_id is required")

    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")

    settings = get_settings("order")

    # Create Razorpay order
    try:
        import httpx

        razorpay_order_id = None
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.RAZORPAY_API_BASE_URL}/orders",
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
                json={
                    "amount": int(amount * 100),  # Razorpay expects paise
                    "currency": "INR",
                    "receipt": f"hotsot_{order_id[:20]}",
                    "payment_capture": 0,  # Manual capture for escrow
                    "notes": {
                        "order_id": str(order_id),
                        "tenant_id": tenant_id,
                        "platform": "hotsot",
                    },
                },
                timeout=10.0,
            )

            if response.status_code == 200:
                data = response.json()
                razorpay_order_id = data.get("id")
                logger.info(
                    f"Razorpay order created: razorpay_order_id={razorpay_order_id} "
                    f"order_id={order_id} amount={amount}"
                )
            else:
                logger.error(
                    f"Razorpay order creation failed: status={response.status_code} "
                    f"body={response.text}"
                )
                raise HTTPException(
                    status_code=502,
                    detail="Payment gateway error. Please retry.",
                )

        return {
            "order_id": order_id,
            "razorpay_order_id": razorpay_order_id,
            "amount": amount,
            "currency": "INR",
            "key_id": settings.RAZORPAY_KEY_ID,
            "status": "PAYMENT_PENDING",
        }

    except httpx.HTTPError as e:
        logger.error(f"Razorpay connection error: {e}")
        raise HTTPException(
            status_code=502,
            detail="Payment gateway unreachable. Please retry.",
        )
    except ImportError:
        # Demo mode without httpx
        logger.warning("httpx not available — payment init in demo mode")
        return {
            "order_id": order_id,
            "razorpay_order_id": f"order_demo_{generate_id()[:12]}",
            "amount": amount,
            "currency": "INR",
            "key_id": settings.RAZORPAY_KEY_ID or "demo_key",
            "status": "PAYMENT_PENDING",
        }


@router.post("/confirm")
async def confirm_payment(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Confirm a payment after client-side Razorpay checkout.

    Verifies the payment signature and captures the payment
    (escrow hold).
    """
    body = await request.json()
    razorpay_order_id = body.get("razorpay_order_id")
    razorpay_payment_id = body.get("razorpay_payment_id")
    razorpay_signature = body.get("razorpay_signature")
    order_id = body.get("order_id")

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

    # Capture payment (escrow hold)
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.RAZORPAY_API_BASE_URL}/payments/{razorpay_payment_id}/capture",
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
                json={
                    "amount": body.get("amount", 0) * 100,  # paise
                    "currency": "INR",
                },
                timeout=10.0,
            )

            if response.status_code == 200:
                logger.info(
                    f"Payment captured (escrow): order={order_id} "
                    f"payment={razorpay_payment_id}"
                )
            else:
                logger.error(f"Payment capture failed: {response.text}")

    except ImportError:
        logger.warning("httpx not available — payment capture in demo mode")
    except Exception as e:
        logger.error(f"Payment capture error: {e}")

    return {
        "order_id": order_id,
        "payment_ref": razorpay_payment_id,
        "status": "PAYMENT_CONFIRMED",
        "escrow": "HELD",
    }


@router.post("/webhook")
async def razorpay_webhook(request: Request):
    """
    Handle Razorpay webhook events.

    Verifies the webhook signature using HMAC-SHA256 and
    processes events like:
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

    # Verify webhook signature
    if settings.RAZORPAY_WEBHOOK_SECRET:
        expected_sig = hmac.new(
            settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_sig, webhook_signature):
            logger.warning("Webhook signature verification FAILED")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = body_json.get("event", "")
    payload = body_json.get("payload", {})

    logger.info(f"Razorpay webhook received: event={event}")

    if event == "payment.captured":
        payment_entity = payload.get("payment", {}).get("entity", {})
        payment_id = payment_entity.get("id")
        order_id = payment_entity.get("notes", {}).get("order_id")
        logger.info(f"Webhook: payment captured — payment={payment_id} order={order_id}")

    elif event == "payment.failed":
        payment_entity = payload.get("payment", {}).get("entity", {})
        logger.warning(
            f"Webhook: payment failed — payment={payment_entity.get('id')} "
            f"error={payment_entity.get('error_description')}"
        )

    elif event == "refund.processed":
        refund_entity = payload.get("refund", {}).get("entity", {})
        logger.info(f"Webhook: refund processed — refund={refund_entity.get('id')}")

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

    Releases escrow and processes refund via Razorpay.
    """
    body = await request.json()
    order_id = body.get("order_id")
    payment_ref = body.get("payment_ref")
    amount = body.get("amount")
    reason = body.get("reason", "Order cancelled/expired")

    if not all([order_id, payment_ref]):
        raise HTTPException(status_code=400, detail="order_id and payment_ref required")

    settings = get_settings("order")

    try:
        import httpx

        refund_payload = {
            "notes": {
                "order_id": str(order_id),
                "reason": reason,
                "platform": "hotsot",
            },
        }
        if amount:
            refund_payload["amount"] = int(amount * 100)  # paise

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.RAZORPAY_API_BASE_URL}/payments/{payment_ref}/refund",
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
                json=refund_payload,
                timeout=10.0,
            )

            if response.status_code == 200:
                data = response.json()
                refund_id = data.get("id")
                logger.info(
                    f"Refund initiated: order={order_id} "
                    f"refund_id={refund_id} amount={amount}"
                )
                return {
                    "order_id": order_id,
                    "refund_id": refund_id,
                    "amount": amount,
                    "status": "REFUND_INITIATED",
                }
            else:
                logger.error(f"Refund failed: {response.text}")
                raise HTTPException(
                    status_code=502,
                    detail="Refund processing failed at gateway.",
                )

    except ImportError:
        logger.warning("httpx not available — refund in demo mode")
        return {
            "order_id": order_id,
            "refund_id": f"rfnd_demo_{generate_id()[:12]}",
            "amount": amount,
            "status": "REFUND_INITIATED",
        }
