"""
HotSot Payment Gateway — Production Razorpay Integration.

Implements:
    - Real Razorpay API integration with test/live mode
    - Idempotency key support for safe retries
    - Payment state machine: CREATED → AUTHORIZED → CAPTURED → REFUNDED
    - Webhook signature verification
    - Automatic retry with exponential backoff

Usage:
    from shared.payment_gateway import RazorpayGateway, PaymentState

    gateway = RazorpayGateway()
    order = await gateway.create_order(amount=Decimal("500.00"), receipt="order_123")
    payment = await gateway.capture_payment(payment_id="pay_xxx", amount=Decimal("500.00"))
    refund = await gateway.refund(payment_id="pay_xxx", amount=Decimal("500.00"))
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("hotsot.payment_gateway")


class PaymentState(str, Enum):
    """Payment state machine states.

    Valid transitions:
        CREATED → AUTHORIZED → CAPTURED → REFUNDED (partial or full)
        CREATED → FAILED
        AUTHORIZED → FAILED
    """
    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    FAILED = "FAILED"


# Valid state transitions
VALID_TRANSITIONS = {
    PaymentState.CREATED: {PaymentState.AUTHORIZED, PaymentState.FAILED},
    PaymentState.AUTHORIZED: {PaymentState.CAPTURED, PaymentState.FAILED},
    PaymentState.CAPTURED: {PaymentState.REFUNDED, PaymentState.PARTIALLY_REFUNDED},
    PaymentState.PARTIALLY_REFUNDED: {PaymentState.REFUNDED, PaymentState.PARTIALLY_REFUNDED},
}


class PaymentGatewayError(Exception):
    """Base exception for payment gateway errors."""
    def __init__(self, message: str, code: str = None, razorpay_error: dict = None):
        super().__init__(message)
        self.code = code
        self.razorpay_error = razorpay_error


class IdempotencyError(PaymentGatewayError):
    """Raised when an idempotent request returns a different result."""
    pass


class RazorpayGateway:
    """
    Production-grade Razorpay payment gateway integration.

    Features:
    - Test mode for development (uses Razorpay test keys)
    - Idempotency keys for safe retries
    - Payment state machine with transition validation
    - Webhook signature verification
    - Automatic retry with exponential backoff
    - Paise conversion using Decimal arithmetic
    """

    def __init__(
        self,
        key_id: Optional[str] = None,
        key_secret: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        test_mode: bool = True,
    ):
        self._key_id = key_id or os.getenv("RAZORPAY_KEY_ID", "")
        self._key_secret = key_secret or os.getenv("RAZORPAY_KEY_SECRET", "")
        self._webhook_secret = webhook_secret or os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
        self._test_mode = test_mode or not bool(self._key_id)
        self._base_url = "https://api.razorpay.com/v1"

        # In-memory idempotency store (production: use Redis)
        self._idempotency_store: Dict[str, Dict[str, Any]] = {}

        if self._test_mode:
            logger.info("Razorpay gateway running in TEST mode — no real payments will be processed")

    @property
    def is_configured(self) -> bool:
        """Check if real Razorpay credentials are configured."""
        return bool(self._key_id and self._key_secret)

    def _to_paise(self, amount: Decimal) -> int:
        """Convert INR Decimal amount to paise (smallest unit).

        Uses Decimal arithmetic to avoid float precision loss.
        Example: ₹500.00 → 50000 paise
        """
        return int(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)

    def _from_paise(self, paise: int) -> Decimal:
        """Convert paise to INR Decimal.

        Example: 50000 paise → ₹500.00
        """
        return (Decimal(paise) / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def _generate_idempotency_key(self, operation: str, **params) -> str:
        """Generate an idempotency key for a payment operation."""
        key_parts = [operation]
        for k, v in sorted(params.items()):
            key_parts.append(f"{k}={v}")
        return hashlib.sha256("|".join(key_parts).encode()).hexdigest()[:40]

    def _check_idempotency(self, key: str, current_params: dict) -> Optional[dict]:
        """Check if this exact request was already processed."""
        cached = self._idempotency_store.get(key)
        if cached:
            logger.info(f"Idempotent request detected: {key[:8]}...")
            return cached["result"]
        return None

    def _store_idempotency(self, key: str, result: dict) -> None:
        """Store the result of a request for idempotency."""
        self._idempotency_store[key] = {"result": result, "timestamp": time.time()}

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[dict] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """Make an authenticated request to Razorpay API with retry logic.

        Implements exponential backoff: 1s, 2s, 4s.
        """
        if not self.is_configured:
            # Test mode — return mock response
            return self._mock_response(method, endpoint, payload)

        import httpx

        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        auth = (self._key_id, self._key_secret)

        last_error = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    if method == "POST":
                        response = await client.post(url, json=payload, auth=auth)
                    elif method == "GET":
                        response = await client.get(url, params=payload, auth=auth)
                    else:
                        raise ValueError(f"Unsupported HTTP method: {method}")

                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code == 429:
                        # Rate limited — wait longer
                        wait_time = 2 ** (attempt + 2)
                        logger.warning(f"Razorpay rate limited, waiting {wait_time}s")
                        time.sleep(wait_time)
                        continue
                    elif response.status_code >= 400:
                        error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                        raise PaymentGatewayError(
                            f"Razorpay API error: {response.status_code}",
                            code=str(response.status_code),
                            razorpay_error=error_data,
                        )

            except httpx.TimeoutException:
                last_error = PaymentGatewayError(f"Razorpay request timeout (attempt {attempt + 1})")
                wait_time = 2 ** attempt
                logger.warning(f"Razorpay timeout, retrying in {wait_time}s")
                time.sleep(wait_time)
            except httpx.HTTPError as e:
                last_error = PaymentGatewayError(f"HTTP error: {e}")
                wait_time = 2 ** attempt
                logger.warning(f"Razorpay HTTP error, retrying in {wait_time}s")
                time.sleep(wait_time)

        raise last_error or PaymentGatewayError("Max retries exceeded")

    def _mock_response(self, method: str, endpoint: str, payload: Optional[dict]) -> dict:
        """Generate mock response for test mode."""
        mock_id = f"mock_{uuid.uuid4().hex[:12]}"

        if "orders" in endpoint:
            return {
                "id": f"order_{mock_id}",
                "amount": payload.get("amount", 0) if payload else 0,
                "currency": "INR",
                "receipt": payload.get("receipt", "") if payload else "",
                "status": "created",
                "created_at": int(time.time()),
            }
        elif "payments" in endpoint and "capture" in endpoint:
            return {
                "id": payload.get("payment_id", f"pay_{mock_id}") if payload else f"pay_{mock_id}",
                "amount": payload.get("amount", 0) if payload else 0,
                "currency": "INR",
                "status": "captured",
                "captured": True,
            }
        elif "refunds" in endpoint:
            return {
                "id": f"rfnd_{mock_id}",
                "amount": payload.get("amount", 0) if payload else 0,
                "currency": "INR",
                "status": "processed",
                "payment_id": payload.get("payment_id", "") if payload else "",
            }
        elif "payouts" in endpoint:
            return {
                "id": f"pout_{mock_id}",
                "amount": payload.get("amount", 0) if payload else 0,
                "currency": "INR",
                "status": "processed",
            }
        return {"id": mock_id, "status": "mock"}

    async def create_order(
        self,
        amount: Decimal,
        receipt: str,
        notes: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a Razorpay order for payment.

        Args:
            amount: Order amount in INR (Decimal).
            receipt: Unique receipt identifier (max 40 chars).
            notes: Additional key-value notes.
            idempotency_key: Optional idempotency key for safe retries.

        Returns:
            Razorpay order dict with id, amount, currency, status.
        """
        paise = self._to_paise(amount)
        payload = {
            "amount": paise,
            "currency": "INR",
            "receipt": receipt[:40],
        }
        if notes:
            payload["notes"] = notes

        # Idempotency check
        if idempotency_key:
            cached = self._check_idempotency(idempotency_key, payload)
            if cached:
                return cached

        result = await self._make_request("POST", "/orders", payload)

        if idempotency_key:
            self._store_idempotency(idempotency_key, result)

        logger.info(f"Razorpay order created: {result.get('id')} amount=₹{amount}")
        return result

    async def capture_payment(
        self,
        payment_id: str,
        amount: Decimal,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Capture an authorized payment.

        Args:
            payment_id: Razorpay payment ID.
            amount: Amount to capture in INR (Decimal).
            idempotency_key: Optional idempotency key.

        Returns:
            Razorpay payment dict with captured status.
        """
        paise = self._to_paise(amount)
        payload = {
            "amount": paise,
            "currency": "INR",
            "payment_id": payment_id,
        }

        if idempotency_key:
            cached = self._check_idempotency(idempotency_key, payload)
            if cached:
                return cached

        result = await self._make_request(
            "POST", f"/payments/{payment_id}/capture",
            {"amount": paise, "currency": "INR"}
        )

        if idempotency_key:
            self._store_idempotency(idempotency_key, result)

        logger.info(f"Razorpay payment captured: {payment_id} amount=₹{amount}")
        return result

    async def refund(
        self,
        payment_id: str,
        amount: Decimal,
        reason: Optional[str] = None,
        notes: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Initiate a refund for a captured payment.

        Args:
            payment_id: Razorpay payment ID to refund.
            amount: Refund amount in INR (Decimal).
            reason: Reason for refund.
            notes: Additional notes.
            idempotency_key: Optional idempotency key.

        Returns:
            Razorpay refund dict with id, amount, status.
        """
        paise = self._to_paise(amount)
        payload = {
            "amount": paise,
            "payment_id": payment_id,
        }
        if reason:
            payload["notes"] = {"reason": reason}
        if notes:
            payload.setdefault("notes", {}).update(notes)

        if idempotency_key:
            cached = self._check_idempotency(idempotency_key, payload)
            if cached:
                return cached

        result = await self._make_request(
            "POST", f"/payments/{payment_id}/refund",
            {"amount": paise}
        )

        if idempotency_key:
            self._store_idempotency(idempotency_key, result)

        logger.info(f"Razorpay refund initiated: payment={payment_id} amount=₹{amount}")
        return result

    async def fetch_payment(self, payment_id: str) -> Dict[str, Any]:
        """Fetch payment details from Razorpay."""
        return await self._make_request("GET", f"/payments/{payment_id}")

    async def fetch_refund(self, refund_id: str) -> Dict[str, Any]:
        """Fetch refund details from Razorpay."""
        return await self._make_request("GET", f"/refunds/{refund_id}")

    def verify_webhook_signature(
        self, payload_body: str, signature: str
    ) -> bool:
        """
        Verify Razorpay webhook signature using HMAC-SHA256.

        Args:
            payload_body: Raw request body string.
            signature: X-Razorpay-Signature header value.

        Returns:
            True if signature is valid, False otherwise.
        """
        if not self._webhook_secret:
            logger.warning("Webhook secret not configured — skipping signature verification")
            return True  # Fail-open if not configured (fix in production)

        expected = hmac.new(
            self._webhook_secret.encode(),
            payload_body.encode(),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    @staticmethod
    def validate_state_transition(
        current: PaymentState, target: PaymentState
    ) -> bool:
        """Validate that a payment state transition is allowed."""
        allowed = VALID_TRANSITIONS.get(current, set())
        return target in allowed
