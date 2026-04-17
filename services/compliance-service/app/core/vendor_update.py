"""
HotSot Compliance Service — _update_vendor_compliance helper.

This module was missing from the compliance service, causing
@compliance_check decorators to fail when trying to update
vendor compliance records after a check.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import VendorComplianceModel

logger = logging.getLogger(__name__)


# Mapping of check_type to the corresponding DB column
_CHECK_FIELD_MAP = {
    "FSSAI": "fssai_verified",
    "GST": "gst_verified",
    "DPDP": "dpdp_verified",
    "RBI": "rbi_verified",
}


async def _update_vendor_compliance(
    entity_id: str,
    tenant_id: str,
    check_type: str,
    status: str,
    session: AsyncSession,
) -> None:
    """
    Update vendor compliance record after a check is performed.

    Looks up the VendorComplianceModel record and updates the
    relevant compliance field (fssai_verified, gst_verified, etc.)
    based on the check result.

    Args:
        entity_id: Vendor UUID string.
        tenant_id: Tenant ID for isolation.
        check_type: One of FSSAI, GST, DPDP, RBI.
        status: Check result status (PASSED, FAILED, PENDING).
        session: Database session.

    Only sets verified=True if status is PASSED.
    Creates a new record if none exists.
    """
    field_name = _CHECK_FIELD_MAP.get(check_type)
    if not field_name:
        logger.warning(f"Unknown check_type for vendor update: {check_type}")
        return

    try:
        vendor_uuid = uuid.UUID(entity_id)
        tenant_uuid = uuid.UUID(tenant_id) if tenant_id != "default" else None
    except ValueError:
        logger.warning(f"Invalid UUID format: entity_id={entity_id} tenant_id={tenant_id}")
        return

    # Look up existing record
    query = select(VendorComplianceModel).where(
        VendorComplianceModel.vendor_id == vendor_uuid,
    )
    if tenant_uuid:
        query = query.where(VendorComplianceModel.tenant_id == tenant_uuid)

    result = await session.execute(query)
    vendor_record = result.scalar_one_or_none()

    if vendor_record:
        # Update existing record
        setattr(vendor_record, field_name, status == "PASSED")
        vendor_record.last_checked_at = datetime.now(timezone.utc)

        # Update onboarding status if all checks pass
        if status == "PASSED":
            all_verified = all([
                vendor_record.fssai_verified,
                vendor_record.gst_verified,
                vendor_record.dpdp_verified,
                vendor_record.rbi_verified,
            ])
            if all_verified:
                vendor_record.onboarding_status = "COMPLIANT"
                logger.info(f"Vendor {entity_id} is now fully COMPLIANT")

        logger.info(
            f"Vendor compliance updated: vendor={entity_id} "
            f"{check_type}={status} → {field_name}={status == 'PASSED'}"
        )
    else:
        # Create new record with this check result
        new_record = VendorComplianceModel(
            vendor_id=vendor_uuid,
            tenant_id=tenant_uuid or vendor_uuid,  # Fallback
            onboarding_status="PENDING",
            last_checked_at=datetime.now(timezone.utc),
        )
        setattr(new_record, field_name, status == "PASSED")
        session.add(new_record)
        logger.info(
            f"New vendor compliance record created: vendor={entity_id} "
            f"{check_type}={status}"
        )
