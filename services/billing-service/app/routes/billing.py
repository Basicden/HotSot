"""HotSot Billing Service — Routes."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from app.core.database import InvoiceModel, PayoutModel

router = APIRouter()
_session_factory = None

def set_dependencies(session_factory, redis_client=None, kafka_producer=None):
    global _session_factory
    _session_factory = session_factory

async def get_session():
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    async with _session_factory() as session:
        yield session

@router.post("/invoice/generate")
async def generate_invoice(vendor_id: str, period_start: str, period_end: str,
                           user: dict = Depends(require_role("admin")),
                           session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    invoice_number = f"INV-{uuid.uuid4().hex[:8].upper()}"
    invoice = InvoiceModel(tenant_id=uuid.UUID(tenant_id), vendor_id=uuid.UUID(vendor_id),
                           invoice_number=invoice_number,
                           period_start=period_start, period_end=period_end)
    session.add(invoice)
    await session.commit()
    return {"invoice_id": str(invoice.id), "invoice_number": invoice_number, "status": "DRAFT"}

@router.get("/vendor/{vendor_id}")
async def get_vendor_invoices(vendor_id: str, limit: int = 10,
                              user: dict = Depends(get_current_user),
                              session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(InvoiceModel).where(InvoiceModel.vendor_id == uuid.UUID(vendor_id),
                                   InvoiceModel.tenant_id == uuid.UUID(tenant_id))
        .order_by(InvoiceModel.created_at.desc()).limit(limit))
    invoices = result.scalars().all()
    return {"vendor_id": vendor_id, "invoices": [
        {"invoice_id": str(i.id), "number": i.invoice_number, "amount": i.payout_amount,
         "status": i.status} for i in invoices
    ]}

@router.post("/payout/process")
async def process_payout(vendor_id: str, amount: float, method: str = "BANK_TRANSFER",
                         user: dict = Depends(require_role("admin")),
                         session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    payout = PayoutModel(tenant_id=uuid.UUID(tenant_id), vendor_id=uuid.UUID(vendor_id),
                         amount=amount, method=method)
    session.add(payout)
    await session.commit()
    return {"payout_id": str(payout.id), "amount": amount, "status": "PENDING"}
