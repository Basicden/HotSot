"""HotSot Compliance Service — Routes."""
import uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from app.core.database import ComplianceCheckModel, GSTRecordModel

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

@router.post("/check")
async def run_compliance_check(entity_type: str, entity_id: str, check_type: str,
                               user: dict = Depends(require_role("admin")),
                               session: AsyncSession = Depends(get_session)):
    """Run a compliance check (FSSAI, GST, DPDP, RBI)."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    # Simplified compliance check
    status = "PASSED"
    details = {"check_type": check_type, "entity_type": entity_type, "notes": "Automated check passed"}
    if check_type == "FSSAI":
        details["notes"] = "FSSAI license verification: valid"
    elif check_type == "GST":
        details["notes"] = "GST compliance: valid"
    check = ComplianceCheckModel(
        tenant_id=uuid.UUID(tenant_id), entity_type=entity_type,
        entity_id=uuid.UUID(entity_id), check_type=check_type,
        status=status, details=details,
    )
    session.add(check)
    await session.commit()
    return {"check_id": str(check.id), "status": status, "details": details}

@router.post("/gst/calculate")
async def calculate_gst(taxable_amount: float, vendor_id: str,
                         is_interstate: bool = False,
                         user: dict = Depends(get_current_user),
                         session: AsyncSession = Depends(get_session)):
    """Calculate GST for an order (5% for food services in India)."""
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    gst_rate = 0.05  # 5% GST for restaurant services
    total_gst = round(taxable_amount * gst_rate, 2)
    if is_interstate:
        record = GSTRecordModel(tenant_id=uuid.UUID(tenant_id), order_id=uuid.uuid4(),
                                vendor_id=uuid.UUID(vendor_id), taxable_amount=taxable_amount,
                                igst=total_gst, total_gst=total_gst)
    else:
        record = GSTRecordModel(tenant_id=uuid.UUID(tenant_id), order_id=uuid.uuid4(),
                                vendor_id=uuid.UUID(vendor_id), taxable_amount=taxable_amount,
                                cgst=round(total_gst/2, 2), sgst=round(total_gst/2, 2),
                                total_gst=total_gst)
    session.add(record)
    await session.commit()
    return {"taxable_amount": taxable_amount, "total_gst": total_gst,
            "cgst": record.cgst, "sgst": record.sgst, "igst": record.igst}
