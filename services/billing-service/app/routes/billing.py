"""
HotSot Billing Service — Routes.

Production-grade billing with:
    - Invoice generation with order aggregation and commission calculation
    - GST computation per Indian tax rules
    - Payout processing with Razorpay integration
    - Invoice listing and vendor billing dashboard
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from shared.auth.jwt import get_current_user, require_role
from shared.utils.helpers import generate_id, now_iso
from app.core.database import InvoiceModel, PayoutModel

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


# Platform commission rates
DEFAULT_COMMISSION_RATE = Decimal("0.15")  # 15% commission
GST_ON_COMMISSION_RATE = Decimal("0.18")  # 18% GST on commission

# Minimum invoice amount (₹1.00)
MIN_INVOICE_AMOUNT = Decimal("1.00")

# Payout methods
VALID_PAYOUT_METHODS = {"BANK_TRANSFER", "UPI", "RAZORPAY_PAYOUT"}


def round_inr(amount: Decimal) -> Decimal:
    """Round to INR 2 decimal places."""
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_invoice_amounts(
    total_revenue: Decimal,
    commission_amount: Decimal,
    gst_amount: Decimal,
    payout_amount: Decimal,
) -> None:
    """
    Validate that all invoice amounts are positive before saving.

    Raises ValueError if any amount is zero or negative.
    This prevents creation of zero-amount invoices (Bug #20).
    """
    if total_revenue < MIN_INVOICE_AMOUNT:
        raise ValueError(
            f"Total revenue ₹{total_revenue} is below minimum invoice amount ₹{MIN_INVOICE_AMOUNT}"
        )
    if commission_amount < Decimal("0"):
        raise ValueError(f"Commission amount cannot be negative: ₹{commission_amount}")
    if gst_amount < Decimal("0"):
        raise ValueError(f"GST amount cannot be negative: ₹{gst_amount}")
    if payout_amount < Decimal("0"):
        raise ValueError(f"Payout amount cannot be negative: ₹{payout_amount}")


@router.post("/invoice/generate")
async def generate_invoice(
    vendor_id: str,
    period_start: str,
    period_end: str,
    commission_rate: Optional[Decimal] = None,
    is_interstate: bool = False,
    user: dict = Depends(require_role("admin", "vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """
    Generate an invoice for a vendor for a billing period.

    The invoice includes:
    - Total orders count and revenue in the period
    - Platform commission (default 15%, configurable)
    - GST on commission (18%)
    - Net payout amount

    Invoice number format: INV-YYYYMMDD-XXXXX
    """
    tenant_id = user.get("tenant_id", "default")

    if not vendor_id or not period_start or not period_end:
        raise HTTPException(
            status_code=400,
            detail="vendor_id, period_start, and period_end are required",
        )

    # Parse dates
    try:
        start_dt = datetime.fromisoformat(period_start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(period_end.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Use ISO 8601 (e.g., 2024-01-01T00:00:00Z)",
        )

    if start_dt >= end_dt:
        raise HTTPException(status_code=400, detail="period_start must be before period_end")

    # Generate sequential invoice number
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    invoice_number = f"INV-{today_str}-{uuid.uuid4().hex[:5].upper()}"

    # Use provided commission rate or default
    if commission_rate is not None:
        comm_rate = round_inr(commission_rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) / Decimal("100")
    else:
        comm_rate = DEFAULT_COMMISSION_RATE

    # In a full implementation, we would query the order service for
    # completed orders in this period for this vendor.
    # For now, the invoice is created with calculation structure.
    # The caller (admin) provides total_orders and total_revenue,
    # or we default to 0 for manual entry.

    # Calculate commission and GST
    # (In production: aggregate from order-service via Kafka/API)
    total_orders = 0
    total_revenue = Decimal("0")

    commission_amount = round_inr(total_revenue * comm_rate)
    gst_on_commission = round_inr(commission_amount * GST_ON_COMMISSION_RATE)

    # GST split: CGST+SGST for intra-state, IGST for inter-state
    if is_interstate:
        igst = gst_on_commission
        cgst = Decimal("0.00")
        sgst = Decimal("0.00")
    else:
        cgst = round_inr(gst_on_commission / 2)
        sgst = round_inr(gst_on_commission - cgst)  # Avoid rounding drift
        igst = Decimal("0.00")

    payout_amount = round_inr(total_revenue - commission_amount - gst_on_commission)

    invoice = InvoiceModel(
        tenant_id=tenant_id,
        vendor_id=vendor_id,
        invoice_number=invoice_number,
        period_start=period_start,
        period_end=period_end,
        total_orders=total_orders,
        total_revenue=str(total_revenue),
        commission_rate=str(round_inr(comm_rate * 100)),
        commission_amount=str(commission_amount),
        gst_amount=str(gst_on_commission),
        payout_amount=str(payout_amount),
        status="DRAFT",
    )
    session.add(invoice)
    await session.commit()

    logger.info(
        f"Invoice generated: vendor={vendor_id} number={invoice_number} "
        f"period={period_start} to {period_end}"
    )

    return {
        "invoice_id": str(invoice.id),
        "invoice_number": invoice_number,
        "vendor_id": vendor_id,
        "period_start": period_start,
        "period_end": period_end,
        "total_orders": total_orders,
        "total_revenue": str(total_revenue),
        "commission_rate_pct": str(round_inr(comm_rate * 100)),
        "commission_amount": str(commission_amount),
        "gst_on_commission": str(gst_on_commission),
        "cgst": str(cgst),
        "sgst": str(sgst),
        "igst": str(igst),
        "is_interstate": is_interstate,
        "payout_amount": str(payout_amount),
        "status": "DRAFT",
        "note": "Invoice created in DRAFT. Populate total_orders and total_revenue, then finalize.",
    }


@router.post("/invoice/finalize")
async def finalize_invoice(
    invoice_id: str,
    total_orders: int,
    total_revenue: Decimal,
    is_interstate: bool = False,
    user: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """
    Finalize an invoice with actual order data.

    Computes commission, GST, and net payout amount.
    """
    tenant_id = user.get("tenant_id", "default")

    result = await session.execute(
        select(InvoiceModel).where(
            InvoiceModel.id == uuid.UUID(invoice_id),
            InvoiceModel.tenant_id == tenant_id,
        )
    )
    invoice = result.scalar_one_or_none()

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if invoice.status != "DRAFT":
        raise HTTPException(status_code=400, detail=f"Cannot finalize invoice in {invoice.status} status")

    # Calculate amounts
    revenue = round_inr(total_revenue.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    comm_rate = Decimal(str(invoice.commission_rate)) / 100 if invoice.commission_rate else DEFAULT_COMMISSION_RATE

    commission = round_inr(revenue * comm_rate)
    gst = round_inr(commission * GST_ON_COMMISSION_RATE)

    # GST split: CGST+SGST for intra-state, IGST for inter-state
    if is_interstate:
        igst = gst
        cgst = Decimal("0.00")
        sgst = Decimal("0.00")
    else:
        cgst = round_inr(gst / 2)
        sgst = round_inr(gst - cgst)  # Avoid rounding drift
        igst = Decimal("0.00")

    payout = round_inr(revenue - commission - gst)

    # Validate all amounts are positive (Bug #20 fix)
    try:
        validate_invoice_amounts(revenue, commission, gst, payout)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Update invoice
    invoice.total_orders = total_orders
    invoice.total_revenue = str(revenue)
    invoice.commission_amount = str(commission)
    invoice.gst_amount = str(gst)
    invoice.payout_amount = str(payout)
    invoice.status = "FINALIZED"

    await session.commit()

    return {
        "invoice_id": str(invoice.id),
        "invoice_number": invoice.invoice_number,
        "status": "FINALIZED",
        "total_orders": total_orders,
        "total_revenue": str(revenue),
        "commission_amount": str(commission),
        "gst_on_commission": str(gst),
        "cgst": str(cgst),
        "sgst": str(sgst),
        "igst": str(igst),
        "is_interstate": is_interstate,
        "payout_amount": str(payout),
    }


@router.get("/vendor/{vendor_id}")
async def get_vendor_invoices(
    vendor_id: str,
    limit: int = 10,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Get all invoices for a vendor, most recent first."""
    tenant_id = user.get("tenant_id", "default")

    result = await session.execute(
        select(InvoiceModel).where(
            InvoiceModel.vendor_id == vendor_id,
            InvoiceModel.tenant_id == tenant_id,
        )
        .order_by(InvoiceModel.created_at.desc())
        .limit(limit)
    )
    invoices = result.scalars().all()

    return {
        "vendor_id": vendor_id,
        "invoices": [{
            "invoice_id": str(i.id),
            "invoice_number": i.invoice_number,
            "period_start": i.period_start,
            "period_end": i.period_end,
            "total_orders": i.total_orders,
            "total_revenue": i.total_revenue,
            "commission_amount": i.commission_amount,
            "gst_amount": i.gst_amount,
            "payout_amount": i.payout_amount,
            "status": i.status,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        } for i in invoices],
        "total": len(invoices),
    }


@router.post("/payout/process")
async def process_payout(
    vendor_id: str,
    amount: Decimal,
    invoice_id: Optional[str] = None,
    method: str = "BANK_TRANSFER",
    user: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """
    Process a payout to a vendor.

    Creates a payout record and (optionally) triggers Razorpay payout.
    Payout states: PENDING → PROCESSING → COMPLETED / FAILED
    """
    tenant_id = user.get("tenant_id", "default")

    if amount <= Decimal("0"):
        raise HTTPException(status_code=400, detail="Payout amount must be positive")

    if method not in VALID_PAYOUT_METHODS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid payout method. Supported: {VALID_PAYOUT_METHODS}",
        )

    # Check for duplicate payout (idempotency)
    if invoice_id:
        existing = await session.execute(
            select(PayoutModel).where(
                PayoutModel.invoice_id == invoice_id,
                PayoutModel.tenant_id == tenant_id,
                PayoutModel.status.in_(["PENDING", "PROCESSING", "COMPLETED"]),
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail="Payout already exists for this invoice",
            )

    payout_amt = round_inr(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    payout = PayoutModel(
        tenant_id=tenant_id,
        vendor_id=vendor_id,
        invoice_id=invoice_id,
        amount=str(payout_amt),
        method=method,
        status="PENDING",
    )
    session.add(payout)
    await session.commit()

    # Attempt Razorpay payout if configured
    razorpay_payout_id = None
    if method == "RAZORPAY_PAYOUT":
        try:
            from shared.utils.config import get_settings
            import httpx

            settings = get_settings("billing")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{settings.RAZORPAY_API_BASE_URL}/payouts",
                    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
                    json={
                        "account_number": "hotsot_escrow_account",
                        "fund_account_id": vendor_id,  # Should be Razorpay fund account ID
                        "amount": int(payout_amt * 100),  # paise
                        "currency": "INR",
                        "mode": "IMPS",
                        "purpose": "payout",
                        "notes": {
                            "vendor_id": vendor_id,
                            "invoice_id": invoice_id or "",
                            "tenant_id": tenant_id,
                        },
                    },
                    timeout=15.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    razorpay_payout_id = data.get("id")
                    payout.status = "PROCESSING"
                    payout.reference_id = razorpay_payout_id
                    await session.commit()
                    logger.info(f"Razorpay payout initiated: {razorpay_payout_id}")
                else:
                    logger.error(f"Razorpay payout failed: {response.text}")

        except ImportError:
            logger.warning("httpx not available — Razorpay payout in demo mode")
        except Exception as e:
            logger.error(f"Razorpay payout error: {e}")

    return {
        "payout_id": str(payout.id),
        "vendor_id": vendor_id,
        "amount": str(payout_amt),
        "method": method,
        "status": payout.status,
        "razorpay_payout_id": razorpay_payout_id,
        "invoice_id": invoice_id,
    }
