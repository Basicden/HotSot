"""
Compensation Routes
===================
REST API layer for the Compensation Service.

  POST /evaluate              — Evaluate a compensation request
  GET  /{order_id}            — Lookup compensation status
  GET  /kitchen/{kitchen_id}/penalty — Kitchen penalty status
  POST /bulk-evaluate         — Batch evaluate multiple requests
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, status

from app.core.engine import (
    CompensationEngine,
    CompensationReason,
    CompensationRequest,
    CompensationStatus,
    OrderPaymentStatus,
    RefundType,
    compensation_engine,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/compensation", tags=["compensation"])


# ── Pydantic-style request / response models (inline for simplicity) ─────

from pydantic import BaseModel, Field


class EvaluateRequest(BaseModel):
    """Inbound compensation evaluation request."""
    order_id: str = Field(..., description="Unique order identifier")
    reason: CompensationReason = Field(..., description="Compensation trigger reason")
    kitchen_id: str = Field(..., description="Kitchen that handled the order")
    payment_status: OrderPaymentStatus = Field(
        OrderPaymentStatus.NOT_PAID, description="Payment state of the order"
    )
    order_amount: float = Field(0.0, ge=0, description="Original order amount in INR")
    delay_minutes: Optional[int] = Field(None, ge=0, description="Delay in minutes (for DELAY_EXCEEDED)")
    payment_ref: Optional[str] = Field(None, description="Original payment reference")
    metadata: Optional[dict] = Field(None, description="Additional context")


class CompensationResponse(BaseModel):
    """Outcome of a compensation evaluation."""
    compensation_id: str
    order_id: str
    reason: str
    refund_type: str
    amount: float
    status: str
    payment_ref: Optional[str] = None
    upi_refund_ref: Optional[str] = None
    is_duplicate: bool = False
    created_at: str


class PenaltyResponse(BaseModel):
    """Kitchen penalty status."""
    kitchen_id: str
    date: str
    shelf_expiration_count: int
    threshold: int
    flagged: bool


class BulkEvaluateRequest(BaseModel):
    """Batch evaluate multiple compensation requests."""
    requests: List[EvaluateRequest] = Field(..., max_length=50)


class BulkEvaluateResponse(BaseModel):
    """Batch evaluation results."""
    results: List[CompensationResponse]
    total: int
    skipped_duplicates: int


# ── Endpoints ────────────────────────────────────────────────────────────

@router.post("/evaluate", response_model=CompensationResponse, status_code=status.HTTP_200_OK)
async def evaluate_compensation(request: EvaluateRequest):
    """
    Evaluate a single compensation request.

    The engine enforces idempotency: calling this endpoint twice with the
    same (order_id, reason) will return the original result with
    `is_duplicate=True` on the second call.
    """
    comp_request = CompensationRequest(
        order_id=request.order_id,
        reason=request.reason,
        kitchen_id=request.kitchen_id,
        payment_status=request.payment_status,
        order_amount=request.order_amount,
        delay_minutes=request.delay_minutes,
        payment_ref=request.payment_ref,
        metadata=request.metadata,
    )

    try:
        result = compensation_engine.evaluate(comp_request)
    except Exception as exc:
        logger.error("Compensation evaluation failed for order %s: %s", request.order_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Evaluation engine error: {exc}",
        )

    return CompensationResponse(
        compensation_id=result.compensation_id,
        order_id=result.order_id,
        reason=result.reason.value,
        refund_type=result.refund_type.value,
        amount=result.amount,
        status=result.status.value,
        payment_ref=result.payment_ref,
        upi_refund_ref=result.upi_refund_ref,
        is_duplicate=result.is_duplicate,
        created_at=result.created_at,
    )


@router.get("/{order_id}", response_model=CompensationResponse)
async def get_compensation_status(order_id: str):
    """
    Look up the stored compensation record for an order.

    Returns 404 if no compensation has been recorded for this order.
    """
    record = compensation_engine.get_compensation_status(order_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No compensation record found for order {order_id}",
        )
    return CompensationResponse(
        compensation_id=record["compensation_id"],
        order_id=record["order_id"],
        reason=record["reason"],
        refund_type=record["refund_type"],
        amount=record["amount"],
        status=record["status"],
        payment_ref=record.get("payment_ref"),
        upi_refund_ref=record.get("upi_refund_ref"),
        is_duplicate=record.get("is_duplicate", False),
        created_at=record.get("created_at", ""),
    )


@router.get("/kitchen/{kitchen_id}/penalty", response_model=PenaltyResponse)
async def get_kitchen_penalty(kitchen_id: str):
    """
    Get the current penalty status for a kitchen.

    Tracks daily shelf-expiration count.  If count exceeds threshold,
    the kitchen is flagged for review.
    """
    penalty = compensation_engine.get_kitchen_penalty(kitchen_id)
    return PenaltyResponse(**penalty)


@router.post("/bulk-evaluate", response_model=BulkEvaluateResponse)
async def bulk_evaluate(request: BulkEvaluateRequest):
    """
    Batch evaluate up to 50 compensation requests in a single call.

    Idempotency is enforced per-request, so duplicate entries within
    the batch or from prior evaluations will be skipped.
    """
    results = []
    skipped = 0

    for req in request.requests:
        comp_request = CompensationRequest(
            order_id=req.order_id,
            reason=req.reason,
            kitchen_id=req.kitchen_id,
            payment_status=req.payment_status,
            order_amount=req.order_amount,
            delay_minutes=req.delay_minutes,
            payment_ref=req.payment_ref,
            metadata=req.metadata,
        )
        try:
            result = compensation_engine.evaluate(comp_request)
            results.append(
                CompensationResponse(
                    compensation_id=result.compensation_id,
                    order_id=result.order_id,
                    reason=result.reason.value,
                    refund_type=result.refund_type.value,
                    amount=result.amount,
                    status=result.status.value,
                    payment_ref=result.payment_ref,
                    upi_refund_ref=result.upi_refund_ref,
                    is_duplicate=result.is_duplicate,
                    created_at=result.created_at,
                )
            )
            if result.is_duplicate:
                skipped += 1
        except Exception as exc:
            logger.error("Bulk eval failed for order %s: %s", req.order_id, exc)
            skipped += 1

    return BulkEvaluateResponse(
        results=results,
        total=len(results),
        skipped_duplicates=skipped,
    )
