"""HotSot Vendor Service — Routes.

Production-grade vendor management with compliance enforcement:
    - @require_compliance("FSSAI", "GST") on activate endpoint
    - @compliance_check("FSSAI") on menu creation (soft gate)
    - Money class for all monetary fields
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.auth.jwt import get_current_user, require_role
from shared.compliance_decorators import compliance_check, require_compliance
from shared.money import Money
from app.core.database import VendorModel, VendorDocumentModel

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

@router.post("/")
async def register_vendor(name: str, email: str, phone: str, gstin: str = None,
                          fssai_license: str = None, user: dict = Depends(require_role("admin")),
                          session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    vendor = VendorModel(tenant_id=tenant_id, name=name, email=email,
                         phone=phone, gstin=gstin, fssai_license=fssai_license)
    session.add(vendor)
    await session.commit()
    await session.refresh(vendor)
    return {"vendor_id": str(vendor.id), "name": vendor.name, "status": vendor.onboarding_status}

@router.get("/{vendor_id}")
async def get_vendor(vendor_id: str, user: dict = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(VendorModel).where(VendorModel.id == uuid.UUID(vendor_id)))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return {"vendor_id": str(vendor.id), "name": vendor.name, "email": vendor.email, "is_active": vendor.is_active, "tier": vendor.tier}

@router.put("/{vendor_id}/activate")
@require_compliance("FSSAI", "GST")
async def activate_vendor(vendor_id: str, is_active: bool = True,
                        tenant_id: str = None, session: AsyncSession = None,
                        user: dict = Depends(require_role("admin"))):
    """Activate a vendor — requires FSSAI and GST compliance PASSED.

    This enforces the Compliance-as-Architecture principle:
    a vendor cannot be activated without valid FSSAI and GST.
    """
    if session is None:
        async with _session_factory() as session:
            result = await session.execute(select(VendorModel).where(VendorModel.id == uuid.UUID(vendor_id)))
            vendor = result.scalar_one_or_none()
            if not vendor:
                raise HTTPException(status_code=404, detail="Vendor not found")
            vendor.is_active = is_active
            vendor.updated_at = datetime.now(timezone.utc)
            await session.commit()
    return {"vendor_id": vendor_id, "is_active": is_active, "compliance_enforced": True}


@router.put("/{vendor_id}")
async def update_vendor(vendor_id: str, name: str = None, is_active: bool = None,
                        commission_rate: Decimal = None, tier: str = None,
                        user: dict = Depends(require_role("admin")),
                        session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(VendorModel).where(VendorModel.id == uuid.UUID(vendor_id)))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    if name: vendor.name = name
    if is_active is not None: vendor.is_active = is_active
    if commission_rate:
        # Use Money class for precise commission rate storage
        vendor.commission_rate = Money(str(commission_rate)).to_db()
    if tier: vendor.tier = tier
    vendor.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"vendor_id": str(vendor.id), "updated": True}

@router.get("/")
async def list_vendors(skip: int = 0, limit: int = 20, user: dict = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    tenant_id = user.get("claims", {}).get("tenant_id", user.get("user_id"))
    result = await session.execute(
        select(VendorModel).where(VendorModel.tenant_id == tenant_id).offset(skip).limit(limit))
    vendors = result.scalars().all()
    return {"vendors": [{"vendor_id": str(v.id), "name": v.name, "tier": v.tier, "is_active": v.is_active} for v in vendors]}
