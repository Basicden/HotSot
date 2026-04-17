"""
HotSot Compliance Registry — Auto-registers compliance check functions.

This module wires the compliance service's real check functions into the
compliance decorator registry, enabling @compliance_check and @require_compliance
decorators to actually invoke domain-specific verification logic.

Registered domains:
    - FSSAI: Food Safety and Standards Authority of India license verification
    - GST:  Goods and Services Tax registration number verification
    - DPDP: Digital Personal Data Protection Act compliance
    - RBI:  Reserve Bank of India payment compliance

Usage:
    # Importing this module triggers auto-registration
    import shared.compliance_registry  # noqa: F401

    # Now @compliance_check("FSSAI") will use the registered FSSAI check
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from shared.compliance_decorators import register_check

logger = logging.getLogger("hotsot.compliance.registry")


@register_check("FSSAI")
async def check_fssai(entity_id: str, tenant_id: str, session: AsyncSession) -> Dict[str, Any]:
    """
    FSSAI compliance check — verifies food safety license.

    Delegates to the compliance service's verify_fssai_license and
    check_compliance_status functions for actual verification.

    Args:
        entity_id: Vendor ID or FSSAI license number.
        tenant_id: Tenant ID for isolation.
        session: Database session.

    Returns:
        Dict with overall_status and optional reason.
    """
    try:
        # Import from compliance service routes — only available when running
        # within the compliance service context. Other services will hit the
        # ImportError fallback and return PENDING status.
        from app.routes.compliance import (
            verify_fssai_license,
            check_compliance_status,
        )

        # First try to check against vendor record in DB
        try:
            vendor_status = await check_compliance_status(entity_id, tenant_id, session)
            fssai_status = vendor_status.get("fssai_status", "PENDING")
            result = {"overall_status": fssai_status}
            reason = vendor_status.get("fssai_reason")
            if reason:
                result["reason"] = reason
            return result
        except Exception:
            # Fall back to direct license verification if vendor lookup fails
            pass

        # Direct verification of license number
        verification = verify_fssai_license(entity_id)
        if verification["valid"]:
            return {"overall_status": "PASSED"}
        else:
            return {
                "overall_status": "FAILED",
                "reason": verification.get("error", "FSSAI license verification failed"),
            }
    except ImportError:
        logger.warning("Compliance service not available for FSSAI check")
        return {"overall_status": "PENDING", "reason": "FSSAI compliance service unavailable"}


@register_check("GST")
async def check_gst(entity_id: str, tenant_id: str, session: AsyncSession) -> Dict[str, Any]:
    """
    GST compliance check — verifies GST registration number.

    Delegates to the compliance service's verify_gst_number and
    check_compliance_status functions for actual verification.

    Args:
        entity_id: Vendor ID or GSTIN.
        tenant_id: Tenant ID for isolation.
        session: Database session.

    Returns:
        Dict with overall_status and optional reason.
    """
    try:
        # Import from compliance service routes — only available when running
        # within the compliance service context.
        from app.routes.compliance import (
            verify_gst_number,
            check_compliance_status,
        )

        # First try to check against vendor record in DB
        try:
            vendor_status = await check_compliance_status(entity_id, tenant_id, session)
            gst_status = vendor_status.get("gst_status", "PENDING")
            result = {"overall_status": gst_status}
            reason = vendor_status.get("gst_reason")
            if reason:
                result["reason"] = reason
            return result
        except Exception:
            # Fall back to direct GSTIN verification if vendor lookup fails
            pass

        # Direct verification of GST number
        verification = verify_gst_number(entity_id)
        if verification["valid"]:
            return {"overall_status": "PASSED"}
        else:
            return {
                "overall_status": "FAILED",
                "reason": verification.get("error", "GST number verification failed"),
            }
    except ImportError:
        logger.warning("Compliance service not available for GST check")
        return {"overall_status": "PENDING", "reason": "GST compliance service unavailable"}


@register_check("DPDP")
async def check_dpdp(entity_id: str, tenant_id: str, session: AsyncSession) -> Dict[str, Any]:
    """
    DPDP compliance check — verifies Digital Personal Data Protection compliance.

    Delegates to the compliance service's check_compliance_status function
    to verify data protection compliance for the entity.

    Args:
        entity_id: Vendor ID.
        tenant_id: Tenant ID for isolation.
        session: Database session.

    Returns:
        Dict with overall_status and optional reason.
    """
    try:
        # Import from compliance service routes — only available when running
        # within the compliance service context.
        from app.routes.compliance import check_compliance_status

        vendor_status = await check_compliance_status(entity_id, tenant_id, session)
        dpdp_status = vendor_status.get("dpdp_status", "PENDING")
        result = {"overall_status": dpdp_status}
        reason = vendor_status.get("dpdp_reason")
        if reason:
            result["reason"] = reason
        return result
    except ImportError:
        logger.warning("Compliance service not available for DPDP check")
        return {"overall_status": "PENDING", "reason": "DPDP compliance service unavailable"}


@register_check("RBI")
async def check_rbi(entity_id: str, tenant_id: str, session: AsyncSession) -> Dict[str, Any]:
    """
    RBI compliance check — verifies Reserve Bank of India payment compliance.

    Delegates to the compliance service's check_compliance_status function
    to verify payment aggregator, escrow, and refund compliance.

    Args:
        entity_id: Vendor ID.
        tenant_id: Tenant ID for isolation.
        session: Database session.

    Returns:
        Dict with overall_status and optional reason.
    """
    try:
        from services.compliance_service.app.routes.compliance import (
            check_compliance_status,
        )

        vendor_status = await check_compliance_status(entity_id, tenant_id, session)
        rbi_status = vendor_status.get("rbi_status", "PENDING")
        result = {"overall_status": rbi_status}
        reason = vendor_status.get("rbi_reason")
        if reason:
            result["reason"] = reason
        return result
    except ImportError:
        logger.warning("Compliance service not available for RBI check")
        return {"overall_status": "PENDING", "reason": "RBI compliance service unavailable"}
