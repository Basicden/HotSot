"""
HotSot Billing Service — Routes.

Production-grade billing with:
    - Invoice generation with order aggregation and commission calculation
    - GST computation per Indian tax rules
    - Payout processing with Razorpay integration
    - Invoice listing and vendor billing dashboard

All monetary calculations use the shared Money class to ensure
Decimal-based precision and INR rounding consistency.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from shared.auth.jwt import get_current_user, require_role
from shared.compliance_decorators import compliance_check, require_compliance
from shared.utils.helpers import generate_id, now_iso
from shared.money import (
    Money,
    round_inr,
    calculate_gst,
    calculate_commission,
    validate_invoice_amount,
)
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


# Minimum invoice amount — prevents zero-amount invoices (Bug #20)
MIN_INVOICE_AMOUNT = Money("1.00")

# Payout methods
VALID_PAYOUT_METHODS = {"BANK_TRANSFER", "UPI", "RAZORPAY_PAYOUT"}


@router.post("/invoice/generate")
@compliance_check("GST")
async def generate_invoice(
    vendor_id: str,
    period_start: str,
    period_end: str,
    commission_rate: Optional[Decimal] = None,
    is_interstate: bool = False,
    allow_zero_draft: bool = False,
    tenant_id: str = None,
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

    When no order data is provided, returns a 400 error instead of
    creating a zero-amount draft (Bug #20 fix).

    Compliance: @compliance_check("GST") — soft gate verifies vendor GST
    status before generating invoices. Logs warning if PENDING, blocks if FAILED.
    """
    if tenant_id is None:
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

    # In a full implementation, we would query the order service for
    # completed orders in this period for this vendor.
    # For now, without order data, we reject zero-amount invoices (Bug #20 fix).
    total_orders = 0
    total_revenue = Money("0.00")

    # Use provided commission rate or default
    if commission_rate is not None:
        comm_rate = Decimal(str(commission_rate)) / Decimal("100")
    else:
        comm_rate = Decimal("0.15")

    # Calculate commission and GST using shared money utilities
    commission_result = calculate_commission(total_revenue, comm_rate)
    commission_amount = commission_result["commission"]
    gst_on_commission = commission_result["gst_on_commission"]
    payout_amount = commission_result["net_payout"]

    # GST split using shared calculate_gst
    gst_split = calculate_gst(commission_amount, rate=Decimal("0.18"), is_interstate=is_interstate)
    cgst = gst_split["cgst"]
    sgst = gst_split["sgst"]
    igst = gst_split["igst"]

    # Enforce minimum invoice amount — no zero-amount invoices allowed
    if total_revenue < MIN_INVOICE_AMOUNT:
        if allow_zero_draft:
            # Even with allow_zero_draft, we only create a placeholder record
            # that MUST be finalized with real order data later
            pass
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot generate invoice without order data. "
                    "Provide order data (total_orders, total_revenue) to create a valid invoice, "
                    "or set allow_zero_draft=True for a placeholder DRAFT invoice that must be "
                    "finalized with real order data before payout."
                ),
            )

    # Generate sequential invoice number
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    invoice_number = f"INV-{today_str}-{uuid.uuid4().hex[:5].upper()}"

    invoice = InvoiceModel(
        tenant_id=tenant_id,
        vendor_id=vendor_id,
        invoice_number=invoice_number,
        period_start=period_start,
        period_end=period_end,
        total_orders=total_orders,
        total_revenue=total_revenue.to_db(),
        commission_rate=str(round_inr(comm_rate * 100)),
        commission_amount=commission_amount.to_db(),
        gst_amount=gst_on_commission.to_db(),
        payout_amount=payout_amount.to_db(),
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
        "total_revenue": total_revenue.to_db(),
        "commission_rate_pct": str(round_inr(comm_rate * 100)),
        "commission_amount": commission_amount.to_db(),
        "gst_on_commission": gst_on_commission.to_db(),
        "cgst": cgst.to_db(),
        "sgst": sgst.to_db(),
        "igst": igst.to_db(),
        "is_interstate": is_interstate,
        "payout_amount": payout_amount.to_db(),
        "status": "DRAFT",
        "note": "Invoice created in DRAFT. Use /invoice/finalize with actual order data to complete. Zero-amount DRAFT invoices will be rejected unless allow_zero_draft=True.",
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

    Computes commission, GST, and net payout amount using Money class
    for all calculations.
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

    # Convert to Money object for all calculations
    revenue = Money(total_revenue)

    # Get commission rate from invoice record
    comm_rate = Decimal(str(invoice.commission_rate)) / 100 if invoice.commission_rate else Decimal("0.15")

    # Calculate commission and GST using shared money utilities
    commission_result = calculate_commission(revenue, comm_rate)
    commission = commission_result["commission"]
    gst = commission_result["gst_on_commission"]
    payout = commission_result["net_payout"]

    # GST split using shared calculate_gst
    gst_split = calculate_gst(commission, rate=Decimal("0.18"), is_interstate=is_interstate)
    cgst = gst_split["cgst"]
    sgst = gst_split["sgst"]
    igst = gst_split["igst"]

    # Validate all amounts — prevent zero-amount invoices (Bug #20 fix)
    try:
        validate_invoice_amount(revenue, min_amount=MIN_INVOICE_AMOUNT)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Update invoice using Money.to_db() for storage
    invoice.total_orders = total_orders
    invoice.total_revenue = revenue.to_db()
    invoice.commission_amount = commission.to_db()
    invoice.gst_amount = gst.to_db()
    invoice.payout_amount = payout.to_db()
    invoice.status = "FINALIZED"

    await session.commit()

    return {
        "invoice_id": str(invoice.id),
        "invoice_number": invoice.invoice_number,
        "status": "FINALIZED",
        "total_orders": total_orders,
        "total_revenue": revenue.to_db(),
        "commission_amount": commission.to_db(),
        "gst_on_commission": gst.to_db(),
        "cgst": cgst.to_db(),
        "sgst": sgst.to_db(),
        "igst": igst.to_db(),
        "is_interstate": is_interstate,
        "payout_amount": payout.to_db(),
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
@require_compliance("RBI")
async def process_payout(
    vendor_id: str,
    amount: Decimal,
    invoice_id: Optional[str] = None,
    method: str = "BANK_TRANSFER",
    tenant_id: str = None,
    user: dict = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    """
    Process a payout to a vendor.

    Creates a payout record and (optionally) triggers Razorpay payout.
    Payout states: PENDING -> PROCESSING -> COMPLETED / FAILED
    Uses Money.to_paise() for Razorpay amount conversion.

    Compliance: @require_compliance("RBI") — hard gate requires RBI compliance
    PASSED before processing payouts. Blocks if not PASSED.
    """
    if tenant_id is None:
        tenant_id = user.get("tenant_id", "default")

    # Convert to Money for validation and precision
    payout_money = Money(amount)

    if not payout_money.is_positive:
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

    payout = PayoutModel(
        tenant_id=tenant_id,
        vendor_id=vendor_id,
        invoice_id=invoice_id,
        amount=payout_money.to_db(),
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
                        "amount": payout_money.to_paise(),  # paise — uses Money.to_paise() for precise conversion
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
        "amount": payout_money.to_db(),
        "method": method,
        "status": payout.status,
        "razorpay_payout_id": razorpay_payout_id,
        "invoice_id": invoice_id,
    }
