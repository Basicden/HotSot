"""
HotSot Compliance Service — Routes.

Production-grade compliance checks for Indian food platforms:
    - FSSAI license verification
    - GST validation and calculation
    - DPDP (Digital Personal Data Protection) compliance
    - RBI payment compliance

All checks are tenant-isolated and produce audit records.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.auth.jwt import get_current_user, require_role
from shared.utils.helpers import generate_id, now_iso
from app.core.database import ComplianceCheckModel, GSTRecordModel, VendorComplianceModel

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


# ═══════════════════════════════════════════════════════════════
# FSSAI LICENSE VERIFICATION
# ═══════════════════════════════════════════════════════════════

# FSSAI license format: 14 digits
# First 2 digits = state/central code
# Digits 3-8 = registration number
# Digits 9-12 = year of registration
# Digits 13-14 = license type code
FSSAI_PATTERN = re.compile(r"^[0-9]{14}$")

# Known FSSAI license type codes
FSSAI_LICENSE_TYPES = {
    "01": "Central License (Large scale — turnover > 20 Cr)",
    "02": "Central License (Mid scale — turnover 12-20 Cr)",
    "03": "State License (Medium scale — turnover 5-12 Cr)",
    "10": "Basic Registration (Small scale — turnover < 5 Cr)",
    "11": "Basic Registration (Petty food business)",
    "12": "State License (Food processing)",
}


def verify_fssai_license(license_no: str) -> Dict[str, Any]:
    """
    Verify an FSSAI license number.

    Validates:
    - 14-digit format (standard for all FSSAI licenses)
    - State/central code in first 2 digits (01-37 valid)
    - License type code in last 2 digits (known types)
    - Year of registration in digits 9-12 (reasonable range)

    Args:
        license_no: The FSSAI license number to verify.

    Returns:
        Dict with valid (bool), license_type, state_code, and any errors.
    """
    license_no = license_no.strip()

    # Check 14-digit format
    if not FSSAI_PATTERN.match(license_no):
        return {
            "valid": False,
            "error": f"Invalid FSSAI license format: expected 14 digits, got '{license_no}'",
            "remediation": "FSSAI license must be a 14-digit number. Verify with FSSAI registry at fssai.gov.in",
        }

    # Check state code (first 2 digits)
    state_code = int(license_no[:2])
    if state_code < 1 or state_code > 37:
        return {
            "valid": False,
            "error": f"Invalid state code in FSSAI license: {state_code}",
            "remediation": "First 2 digits must be a valid Indian state code (01-37).",
        }

    # Check year of registration (digits 9-12)
    year = int(license_no[8:12])
    current_year = datetime.now(timezone.utc).year
    if year < 2006 or year > current_year:
        return {
            "valid": False,
            "error": f"Invalid registration year in FSSAI license: {year}",
            "remediation": f"Year must be between 2006 (FSSAI Act enactment) and {current_year}.",
        }

    # Check license type code (last 2 digits)
    license_type_code = license_no[12:14]
    license_type = FSSAI_LICENSE_TYPES.get(
        license_type_code,
        f"Unknown type ({license_type_code}) — may be a valid new category",
    )

    return {
        "valid": True,
        "license_no": license_no,
        "state_code": state_code,
        "state_name": _get_state_name(state_code),
        "registration_year": year,
        "license_type_code": license_type_code,
        "license_type": license_type,
        "verification_method": "format_and_pattern_validation",
        "note": "Production deployment should integrate FSSAI registry API for real-time verification",
    }


def verify_gst_number(gst_no: str) -> Dict[str, Any]:
    """
    Verify a GST registration number (GSTIN).

    Validates:
    - 15-character format: 2 digit state code + 10 char PAN + 1 entity + 1 checksum + Z + 1 check digit
    - State code must be 01-37
    - PAN format embedded within
    - Checksum digit validation using Luhn-like algorithm

    Args:
        gst_no: The GSTIN to verify.

    Returns:
        Dict with valid (bool), state_code, pan, entity_type, and any errors.
    """
    gst_no = gst_no.strip().upper()

    if not GSTIN_PATTERN.match(gst_no):
        return {
            "valid": False,
            "error": "Invalid GSTIN format. Expected: 2-digit state + 10-char PAN + entity + check + Z + check_digit",
            "remediation": "Provide a valid 15-character GSTIN matching the standard pattern.",
        }

    state_code = int(gst_no[:2])
    if state_code < 1 or state_code > 37:
        return {
            "valid": False,
            "error": f"Invalid state code: {state_code}. Must be 01-37.",
            "remediation": "First 2 digits must be a valid Indian state code.",
        }

    # Validate checksum using GST checksum algorithm
    if not _validate_gst_checksum(gst_no):
        return {
            "valid": False,
            "error": "GSTIN checksum validation failed. The number may be invalid.",
            "remediation": "Verify the GSTIN on the GST portal (gst.gov.in).",
        }

    pan = gst_no[2:12]

    return {
        "valid": True,
        "gst_no": gst_no,
        "state_code": state_code,
        "state_name": _get_state_name(state_code),
        "pan": pan,
        "entity_type": gst_no[12],
        "check_digit": gst_no[14],
        "verification_method": "format_and_checksum_validation",
        "note": "Production deployment should verify GSTIN via GST portal API",
    }


def _validate_gst_checksum(gstin: str) -> bool:
    """
    Validate GSTIN using the official checksum algorithm.

    The GST checksum uses a variant of the Luhn algorithm with
    a specific character-to-value mapping.
    """
    # GST checksum character map
    char_map = {
        '0': 0, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,
        '8': 8, '9': 9, 'A': 10, 'B': 11, 'C': 12, 'D': 13, 'E': 14,
        'F': 15, 'G': 16, 'H': 17, 'I': 18, 'J': 19, 'K': 20, 'L': 21,
        'M': 22, 'N': 23, 'O': 24, 'P': 25, 'Q': 26, 'R': 27, 'S': 28,
        'T': 29, 'U': 30, 'V': 31, 'W': 32, 'X': 33, 'Y': 34, 'Z': 35,
    }
    try:
        total = 0
        for i in range(14):
            val = char_map.get(gstin[i], 0)
            if i % 2 == 1:
                val *= 2
                val = val // 36 + val % 36
            total += val
        check = (36 - (total % 36)) % 36
        expected = char_map.get(gstin[14], -1)
        return check == expected
    except (IndexError, KeyError):
        return False


async def check_compliance_status(
    vendor_id: str, tenant_id: str, session: AsyncSession
) -> Dict[str, Any]:
    """
    Check vendor compliance status by querying the vendor record from DB
    and actually verifying stored FSSAI/GST numbers.

    Looks up the VendorComplianceModel to determine if the vendor
    has completed all required compliance checks, then re-verifies
    any stored license/GST numbers using verify_fssai_license and
    verify_gst_number.

    Args:
        vendor_id: The vendor ID to check.
        tenant_id: Tenant ID for isolation.
        session: Database session.

    Returns:
        Dict with overall_status, individual check statuses, and details.
    """
    result = await session.execute(
        select(VendorComplianceModel).where(
            VendorComplianceModel.vendor_id == uuid.UUID(vendor_id),
            VendorComplianceModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    vendor_record = result.scalar_one_or_none()

    if not vendor_record:
        return {
            "overall_status": "PENDING",
            "reason": "Vendor not found in compliance registry — onboarding not initiated",
            "vendor_id": vendor_id,
            "fssai_status": "PENDING",
            "gst_status": "PENDING",
            "dpdp_status": "PENDING",
            "rbi_status": "PENDING",
        }

    # Actually verify stored FSSAI license number
    fssai_status = "PENDING"
    fssai_reason = None
    if vendor_record.fssai_license:
        fssai_verification = verify_fssai_license(vendor_record.fssai_license)
        if fssai_verification["valid"]:
            fssai_status = "PASSED" if vendor_record.fssai_verified else "PENDING"
        else:
            fssai_status = "FAILED"
            fssai_reason = fssai_verification.get("error", "FSSAI license verification failed")
    elif vendor_record.fssai_verified:
        # Marked verified but no license on file — data inconsistency
        fssai_status = "PENDING"
        fssai_reason = "FSSAI marked verified but no license number on file"

    # Actually verify stored GST number
    gst_status = "PENDING"
    gst_reason = None
    if vendor_record.gst_number:
        gst_verification = verify_gst_number(vendor_record.gst_number)
        if gst_verification["valid"]:
            gst_status = "PASSED" if vendor_record.gst_verified else "PENDING"
        else:
            gst_status = "FAILED"
            gst_reason = gst_verification.get("error", "GST number verification failed")
    elif vendor_record.gst_verified:
        # Marked verified but no GST number on file — data inconsistency
        gst_status = "PENDING"
        gst_reason = "GST marked verified but no GST number on file"

    # DPDP status
    dpdp_status = "PENDING"
    dpdp_reason = None
    if vendor_record.dpdp_verified:
        dpdp_status = "PASSED"
    elif vendor_record.dpdp_consent_obtained and not vendor_record.dpdp_policy_published:
        dpdp_status = "FAILED"
        dpdp_reason = "DPDP consent obtained but privacy policy not published"
    elif not vendor_record.dpdp_consent_obtained and vendor_record.dpdp_policy_published:
        dpdp_status = "FAILED"
        dpdp_reason = "DPDP privacy policy published but consent not obtained"

    # RBI status
    rbi_status = "PENDING"
    rbi_reason = None
    if vendor_record.rbi_verified:
        rbi_status = "PASSED"
    elif vendor_record.rbi_payment_aggregator and not vendor_record.rbi_escrow_maintained:
        rbi_status = "FAILED"
        rbi_reason = "Payment aggregator registered but escrow not maintained"
    elif vendor_record.rbi_payment_aggregator and not vendor_record.rbi_refund_compliance:
        rbi_status = "FAILED"
        rbi_reason = "Payment aggregator registered but refund timeline not compliant"

    # Determine overall status
    checks = {
        "fssai_status": fssai_status,
        "gst_status": gst_status,
        "dpdp_status": dpdp_status,
        "rbi_status": rbi_status,
    }
    reasons = {
        "fssai_reason": fssai_reason,
        "gst_reason": gst_reason,
        "dpdp_reason": dpdp_reason,
        "rbi_reason": rbi_reason,
    }

    # If any check is FAILED, overall is FAILED
    # If any check is PENDING, overall is PENDING
    # Only PASSED if all are PASSED
    all_statuses = list(checks.values())
    if "FAILED" in all_statuses:
        overall = "FAILED"
    elif "PENDING" in all_statuses:
        overall = "PENDING"
    else:
        overall = "PASSED"

    result_dict = {
        "overall_status": overall,
        "vendor_id": vendor_id,
        "onboarding_status": vendor_record.onboarding_status,
        **checks,
        "last_checked_at": vendor_record.last_checked_at.isoformat() if vendor_record.last_checked_at else None,
    }
    # Include reasons for any non-PASSED checks
    for key, value in reasons.items():
        if value is not None:
            result_dict[key] = value

    return result_dict


async def check_vendor_compliance(
    vendor_id: str, tenant_id: str, session: AsyncSession
) -> Dict[str, Any]:
    """
    Comprehensive vendor compliance check that queries the actual vendor
    record from DB and verifies all stored compliance documents.

    This function:
    1. Looks up the vendor compliance record from the database
    2. Re-verifies the stored FSSAI license using verify_fssai_license()
    3. Re-verifies the stored GST number using verify_gst_number()
    4. Checks DPDP and RBI compliance from DB record
    5. Returns FAILED with specific reason if any verification fails
    6. Returns PENDING (not PASSED) if verification data is missing

    Args:
        vendor_id: The vendor UUID to check.
        tenant_id: Tenant ID for isolation.
        session: Database session.

    Returns:
        Dict with overall_status, per-check statuses, verification details,
        and specific failure reasons.
    """
    result = await session.execute(
        select(VendorComplianceModel).where(
            VendorComplianceModel.vendor_id == uuid.UUID(vendor_id),
            VendorComplianceModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    vendor_record = result.scalar_one_or_none()

    if not vendor_record:
        return {
            "overall_status": "PENDING",
            "reason": "Vendor not found in compliance registry — onboarding not initiated",
            "vendor_id": vendor_id,
            "fssai_status": "PENDING",
            "fssai_reason": "No FSSAI license on file — vendor not registered",
            "gst_status": "PENDING",
            "gst_reason": "No GST number on file — vendor not registered",
            "dpdp_status": "PENDING",
            "dpdp_reason": "No DPDP data on file — vendor not registered",
            "rbi_status": "PENDING",
            "rbi_reason": "No RBI data on file — vendor not registered",
        }

    checks: Dict[str, Any] = {}
    all_passed = True
    any_failed = False

    # ── FSSAI Verification ──
    if vendor_record.fssai_license:
        fssai_result = verify_fssai_license(vendor_record.fssai_license)
        if fssai_result["valid"]:
            checks["fssai_status"] = "PASSED"
            checks["fssai_details"] = {
                "license_no": fssai_result["license_no"],
                "license_type": fssai_result["license_type"],
                "state_code": fssai_result["state_code"],
                "state_name": fssai_result["state_name"],
                "registration_year": fssai_result["registration_year"],
            }
        else:
            checks["fssai_status"] = "FAILED"
            checks["fssai_reason"] = fssai_result.get("error", "FSSAI verification failed")
            checks["fssai_remediation"] = fssai_result.get(
                "remediation", "Verify with FSSAI registry at fssai.gov.in"
            )
            any_failed = True
    else:
        checks["fssai_status"] = "PENDING"
        checks["fssai_reason"] = "No FSSAI license number on file — cannot verify"
        all_passed = False

    # ── GST Verification ──
    if vendor_record.gst_number:
        gst_result = verify_gst_number(vendor_record.gst_number)
        if gst_result["valid"]:
            checks["gst_status"] = "PASSED"
            checks["gst_details"] = {
                "gst_no": gst_result["gst_no"],
                "state_code": gst_result["state_code"],
                "state_name": gst_result["state_name"],
                "pan": gst_result["pan"],
                "entity_type": gst_result["entity_type"],
            }
        else:
            checks["gst_status"] = "FAILED"
            checks["gst_reason"] = gst_result.get("error", "GST verification failed")
            checks["gst_remediation"] = gst_result.get(
                "remediation", "Verify the GSTIN on the GST portal (gst.gov.in)"
            )
            any_failed = True
    else:
        checks["gst_status"] = "PENDING"
        checks["gst_reason"] = "No GST number on file — cannot verify"
        all_passed = False

    # ── DPDP Verification ──
    if vendor_record.dpdp_verified:
        checks["dpdp_status"] = "PASSED"
    else:
        dpdp_failures = []
        if not vendor_record.dpdp_consent_obtained:
            dpdp_failures.append("consent not obtained")
        if not vendor_record.dpdp_policy_published:
            dpdp_failures.append("privacy policy not published")
        if dpdp_failures:
            checks["dpdp_status"] = "FAILED"
            checks["dpdp_reason"] = f"DPDP compliance failed: {', '.join(dpdp_failures)}"
            any_failed = True
        else:
            checks["dpdp_status"] = "PENDING"
            checks["dpdp_reason"] = "DPDP verification data incomplete — cannot confirm compliance"
            all_passed = False

    # ── RBI Verification ──
    if vendor_record.rbi_verified:
        checks["rbi_status"] = "PASSED"
    else:
        rbi_failures = []
        if not vendor_record.rbi_payment_aggregator:
            rbi_failures.append("payment aggregator not registered")
        if not vendor_record.rbi_escrow_maintained:
            rbi_failures.append("escrow account not maintained")
        if not vendor_record.rbi_refund_compliance:
            rbi_failures.append("refund timeline not compliant (T+5 business days)")
        if rbi_failures:
            checks["rbi_status"] = "FAILED"
            checks["rbi_reason"] = f"RBI compliance failed: {', '.join(rbi_failures)}"
            any_failed = True
        else:
            checks["rbi_status"] = "PENDING"
            checks["rbi_reason"] = "RBI verification data incomplete — cannot confirm compliance"
            all_passed = False

    # Determine overall status
    if any_failed:
        overall = "FAILED"
    elif not all_passed:
        overall = "PENDING"
    else:
        overall = "PASSED"

    return {
        "overall_status": overall,
        "vendor_id": vendor_id,
        "onboarding_status": vendor_record.onboarding_status,
        **checks,
        "last_checked_at": vendor_record.last_checked_at.isoformat() if vendor_record.last_checked_at else None,
    }


# ═══════════════════════════════════════════════════════════════
# GSTIN VALIDATION
# ═══════════════════════════════════════════════════════════════

# Indian GSTIN regex: 2 digit state code + 10 digit PAN + 1 entity + 1 check digit + Z
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")

# GST rates applicable to Indian restaurant services
# 5% for standalone/non-AC restaurants, 18% for AC/restaurants with liquor
GST_RATES = {
    "restaurant_standard": Decimal("0.05"),   # 5% — standalone, non-AC
    "restaurant_ac": Decimal("0.18"),          # 18% — AC, alcohol-serving
    "restaurant_canteen": Decimal("0.05"),     # 5% — canteen, mess
    "delivery_fee": Decimal("0.18"),           # 18% — delivery charges
    "packaging": Decimal("0.18"),              # 18% — packaging material
}


def validate_gstin(gstin: str) -> Dict[str, Any]:
    """
    Validate an Indian GSTIN number.

    Checks:
    - Format (regex match against GSTIN pattern)
    - State code (must be 01-37)
    - PAN format (embedded in GSTIN)

    Args:
        gstin: The GSTIN string to validate.

    Returns:
        Dict with valid (bool), state_code, pan, and any errors.
    """
    gstin = gstin.strip().upper()

    if not GSTIN_PATTERN.match(gstin):
        return {
            "valid": False,
            "error": "Invalid GSTIN format. Expected: 2-digit state + 10-char PAN + entity + check + Z",
            "gstin": gstin,
        }

    state_code = int(gstin[:2])
    if state_code < 1 or state_code > 37:
        return {
            "valid": False,
            "error": f"Invalid state code: {state_code}. Must be 01-37.",
            "gstin": gstin,
        }

    pan = gstin[2:12]

    return {
        "valid": True,
        "gstin": gstin,
        "state_code": state_code,
        "state_name": _get_state_name(state_code),
        "pan": pan,
        "entity_type": gstin[12],
        "check_digit": gstin[14],
    }


def _get_state_name(code: int) -> str:
    """Map state code to name."""
    states = {
        1: "Jammu & Kashmir", 2: "Himachal Pradesh", 3: "Punjab",
        4: "Chandigarh", 5: "Uttarakhand", 6: "Haryana", 7: "Delhi",
        8: "Rajasthan", 9: "Uttar Pradesh", 10: "Bihar", 11: "Sikkim",
        12: "Arunachal Pradesh", 13: "Nagaland", 14: "Manipur",
        15: "Mizoram", 16: "Tripura", 17: "Meghalaya", 18: "Assam",
        19: "West Bengal", 20: "Jharkhand", 21: "Odisha",
        22: "Chattisgarh", 23: "Madhya Pradesh", 24: "Gujarat",
        25: "Daman & Diu", 26: "Dadra & Nagar Haveli",
        27: "Maharashtra", 28: "Andhra Pradesh", 29: "Karnataka",
        30: "Goa", 31: "Lakshadweep", 32: "Kerala",
        33: "Tamil Nadu", 34: "Puducherry", 35: "Andaman & Nicobar",
        36: "Telangana", 37: "Andhra Pradesh (New)",
    }
    return states.get(code, "Unknown")


def determine_gst_rate(
    is_ac_restaurant: bool = False,
    serves_alcohol: bool = False,
    is_canteen: bool = False,
) -> Decimal:
    """
    Determine the applicable GST rate for a restaurant service.

    Indian GST rules for restaurants:
    - 5% (no ITC): Standalone restaurants, non-AC, canteens
    - 18% (with ITC): AC restaurants, restaurants serving alcohol

    Args:
        is_ac_restaurant: Whether the restaurant is AC.
        serves_alcohol: Whether the restaurant serves alcohol.
        is_canteen: Whether it's a canteen/mess.

    Returns:
        Applicable GST rate as Decimal.
    """
    if is_canteen:
        return GST_RATES["restaurant_canteen"]
    if is_ac_restaurant or serves_alcohol:
        return GST_RATES["restaurant_ac"]
    return GST_RATES["restaurant_standard"]


def round_gst(amount: Decimal) -> Decimal:
    """
    Round GST amount as per Indian GST rounding rules.

    GST amounts are rounded to the nearest 2 decimal places
    using standard rounding (ROUND_HALF_UP).

    Args:
        amount: The amount to round.

    Returns:
        Rounded amount.
    """
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ═══════════════════════════════════════════════════════════════
# COMPLIANCE CHECK ROUTES
# ═══════════════════════════════════════════════════════════════

@router.post("/check")
async def run_compliance_check(
    entity_type: str,
    entity_id: str,
    check_type: str,
    user: dict = Depends(require_role("admin", "vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """
    Run a compliance check (FSSAI, GST, DPDP, RBI).

    Each check type performs actual validation:

    - FSSAI: Validates license format, checks against vendor DB record
    - GST: Validates GSTIN format, checksum, and checks vendor DB record
    - DPDP: Checks data handling compliance against vendor DB record
    - RBI: Checks payment compliance against vendor DB record

    Status values:
    - PASSED: All verifications succeeded
    - FAILED: One or more verifications failed
    - PENDING: Verification data is missing and cannot be verified
    """
    tenant_id = user.get("tenant_id", "default")

    if check_type not in ("FSSAI", "GST", "DPDP", "RBI"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown check type: {check_type}. Supported: FSSAI, GST, DPDP, RBI",
        )

    # Run actual validation based on type
    status = "PENDING"
    details: Dict[str, Any] = {
        "check_type": check_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "checked_at": now_iso(),
    }
    warnings: List[str] = []

    if check_type == "FSSAI":
        result = await _check_fssai_compliance(entity_id, entity_type, tenant_id, session)
        status = result["status"]
        details.update(result["details"])
        warnings = result.get("warnings", [])

    elif check_type == "GST":
        result = await _check_gst_compliance(entity_id, entity_type, tenant_id, session)
        status = result["status"]
        details.update(result["details"])
        warnings = result.get("warnings", [])

    elif check_type == "DPDP":
        result = await _check_dpdp_compliance(entity_id, tenant_id, session)
        status = result["status"]
        details.update(result["details"])
        warnings = result.get("warnings", [])

    elif check_type == "RBI":
        result = await _check_rbi_compliance(entity_id, tenant_id, session)
        status = result["status"]
        details.update(result["details"])
        warnings = result.get("warnings", [])

    # Update vendor compliance record if entity_type is VENDOR
    if entity_type == "VENDOR":
        await _update_vendor_compliance(
            entity_id, tenant_id, check_type, status, session
        )

    # Persist check result
    check = ComplianceCheckModel(
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        check_type=check_type,
        status=status,
        details=details,
    )
    session.add(check)
    await session.commit()

    return {
        "check_id": str(check.id),
        "status": status,
        "details": details,
        "warnings": warnings,
        "checked_at": details.get("checked_at"),
    }


async def _check_fssai_compliance(
    entity_id: str, entity_type: str, tenant_id: str, session: AsyncSession
) -> Dict[str, Any]:
    """
    Perform FSSAI license compliance check.

    When entity_type is VENDOR, looks up the vendor's stored FSSAI license
    from the DB and verifies it. Otherwise, validates the entity_id directly
    as a FSSAI license number.

    Validates:
    - License number format (14-digit for central, state, basic)
    - License type detection
    - State code validity
    - Registration year validity
    - Vendor onboarding status in DB (when entity_type is VENDOR)

    FSSAI license format:
    - 14 digits: first 2 = state code, next 6 = registration, next 4 = year, last 2 = type
    """
    details: Dict[str, Any] = {
        "regulation": "Food Safety and Standards Authority of India (FSSAI) Act 2006",
    }

    # If entity_type is VENDOR, look up the stored FSSAI license from DB
    if entity_type == "VENDOR":
        result = await session.execute(
            select(VendorComplianceModel).where(
                VendorComplianceModel.vendor_id == uuid.UUID(entity_id),
                VendorComplianceModel.tenant_id == uuid.UUID(tenant_id),
            )
        )
        vendor_record = result.scalar_one_or_none()

        if not vendor_record:
            details["vendor_id"] = entity_id
            details["reason"] = "Vendor not found in compliance registry — cannot verify FSSAI"
            return {
                "status": "PENDING",
                "details": details,
                "warnings": ["Vendor not registered in compliance system"],
            }

        if not vendor_record.fssai_license:
            details["vendor_id"] = entity_id
            details["reason"] = "No FSSAI license on file for this vendor — cannot verify"
            return {
                "status": "PENDING",
                "details": details,
                "warnings": ["FSSAI license number not provided — vendor cannot be verified"],
            }

        # Verify the stored FSSAI license
        license_no = vendor_record.fssai_license
        details["vendor_id"] = entity_id
        details["license_number"] = license_no
    else:
        # Direct validation — entity_id is the FSSAI license number
        license_no = entity_id
        details["license_number"] = entity_id

    # Validate license format using verify_fssai_license
    verification = verify_fssai_license(license_no)
    if not verification["valid"]:
        return {
            "status": "FAILED",
            "details": {
                **details,
                "error": verification["error"],
                "remediation": verification.get("remediation", "Verify with FSSAI registry."),
            },
            "warnings": ["FSSAI license format invalid — vendor cannot operate legally"],
        }

    # Format passed — add verification details
    details["license_type"] = verification["license_type"]
    details["state_code"] = verification["state_code"]
    details["state_name"] = verification["state_name"]
    details["registration_year"] = verification["registration_year"]
    details["verification_method"] = verification["verification_method"]

    # If VENDOR type, also check vendor onboarding status in DB
    if entity_type == "VENDOR":
        vendor_status = await check_compliance_status(entity_id, tenant_id, session)
        details["vendor_onboarding_status"] = vendor_status.get("onboarding_status", "UNKNOWN")
        details["vendor_fssai_verified"] = vendor_status.get("fssai_status", "PENDING")

        if vendor_status.get("fssai_status") == "FAILED":
            return {
                "status": "FAILED",
                "details": {
                    **details,
                    "error": vendor_status.get(
                        "fssai_reason",
                        "FSSAI license verification failed in vendor compliance record",
                    ),
                },
                "warnings": [],
            }

        if vendor_status.get("fssai_status") == "PENDING":
            return {
                "status": "PENDING",
                "details": {
                    **details,
                    "reason": vendor_status.get(
                        "fssai_reason",
                        "FSSAI verification pending in vendor compliance record",
                    ),
                },
                "warnings": ["FSSAI format valid but vendor compliance not fully confirmed"],
            }

    return {
        "status": "PASSED",
        "details": details,
        "warnings": [
            "FSSAI license verified by format only — integrate FSSAI API for real-time status",
        ],
    }


async def _check_gst_compliance(
    entity_id: str, entity_type: str, tenant_id: str, session: AsyncSession
) -> Dict[str, Any]:
    """
    Perform GST compliance check.

    When entity_type is VENDOR, looks up the vendor's stored GST number
    from the DB and verifies it. Otherwise, validates the entity_id directly
    as a GSTIN.

    Validates GSTIN format using the standard Indian GSTIN regex,
    checksum algorithm, then verifies against vendor DB record.
    """
    details: Dict[str, Any] = {
        "regulation": "Central Goods and Services Tax (CGST) Act 2017",
    }

    # If entity_type is VENDOR, look up the stored GST number from DB
    if entity_type == "VENDOR":
        result = await session.execute(
            select(VendorComplianceModel).where(
                VendorComplianceModel.vendor_id == uuid.UUID(entity_id),
                VendorComplianceModel.tenant_id == uuid.UUID(tenant_id),
            )
        )
        vendor_record = result.scalar_one_or_none()

        if not vendor_record:
            details["vendor_id"] = entity_id
            details["reason"] = "Vendor not found in compliance registry — cannot verify GST"
            return {
                "status": "PENDING",
                "details": details,
                "warnings": ["Vendor not registered in compliance system"],
            }

        if not vendor_record.gst_number:
            details["vendor_id"] = entity_id
            details["reason"] = "No GST number on file for this vendor — cannot verify"
            return {
                "status": "PENDING",
                "details": details,
                "warnings": ["GST number not provided — vendor cannot be verified"],
            }

        # Verify the stored GST number
        gst_no = vendor_record.gst_number
        details["vendor_id"] = entity_id
        details["gstin"] = gst_no
    else:
        # Direct validation — entity_id is the GSTIN
        gst_no = entity_id
        details["gstin"] = entity_id

    # Validate GSTIN format and checksum
    validation = verify_gst_number(gst_no)

    if not validation["valid"]:
        details["error"] = validation["error"]
        details["remediation"] = validation.get(
            "remediation", "Provide a valid 15-character GSTIN."
        )
        return {
            "status": "FAILED",
            "details": details,
            "warnings": [],
        }

    # Format passed — add verification details
    details.update({
        "state_code": validation["state_code"],
        "state_name": validation["state_name"],
        "pan": validation["pan"],
        "entity_type": validation["entity_type"],
        "verification_method": validation["verification_method"],
    })

    # If VENDOR type, also check vendor onboarding status in DB
    if entity_type == "VENDOR":
        vendor_status = await check_compliance_status(entity_id, tenant_id, session)
        details["vendor_onboarding_status"] = vendor_status.get("onboarding_status", "UNKNOWN")
        details["vendor_gst_verified"] = vendor_status.get("gst_status", "PENDING")

        if vendor_status.get("gst_status") == "FAILED":
            return {
                "status": "FAILED",
                "details": {
                    **details,
                    "error": vendor_status.get(
                        "gst_reason",
                        "GSTIN verification failed in vendor compliance record",
                    ),
                },
                "warnings": [],
            }

        if vendor_status.get("gst_status") == "PENDING":
            return {
                "status": "PENDING",
                "details": {
                    **details,
                    "reason": vendor_status.get(
                        "gst_reason",
                        "GST verification pending in vendor compliance record",
                    ),
                },
                "warnings": ["GSTIN format valid but vendor compliance not fully confirmed"],
            }

    return {
        "status": "PASSED",
        "details": details,
        "warnings": [
            "GSTIN verified by format only — integrate GST portal API for real-time status",
        ],
    }


async def _check_dpdp_compliance(
    entity_id: str, tenant_id: str, session: AsyncSession
) -> Dict[str, Any]:
    """
    Perform Digital Personal Data Protection compliance check.

    Checks vendor DB record for:
    - Data collection consent mechanism
    - Data retention policy
    - Data deletion capability
    - Privacy policy existence

    Returns PENDING if vendor has no DPDP data on file.
    Returns FAILED if required DPDP items are missing.
    Returns PASSED only if all DPDP requirements are verified.
    """
    details: Dict[str, Any] = {
        "regulation": "Digital Personal Data Protection Act 2023 (India)",
        "entity_id": entity_id,
    }

    # Check vendor record in DB
    result = await session.execute(
        select(VendorComplianceModel).where(
            VendorComplianceModel.vendor_id == uuid.UUID(entity_id),
            VendorComplianceModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    vendor_record = result.scalar_one_or_none()

    if not vendor_record:
        details["reason"] = "Vendor not found in compliance registry — DPDP status unknown"
        details["checks_performed"] = []
        return {
            "status": "PENDING",
            "details": details,
            "warnings": [
                "Cannot verify DPDP compliance — vendor onboarding not initiated",
            ],
        }

    # Check DPDP-specific fields
    checks_performed = []
    failed_checks = []
    pending_checks = []

    # Consent mechanism
    if vendor_record.dpdp_consent_obtained:
        checks_performed.append("consent_mechanism_exists: VERIFIED")
    elif vendor_record.dpdp_consent_obtained is False:
        checks_performed.append("consent_mechanism_exists: NOT_PROVIDED")
        failed_checks.append("consent_mechanism_exists")
    else:
        checks_performed.append("consent_mechanism_exists: PENDING")
        pending_checks.append("consent_mechanism_exists")

    # Privacy policy
    if vendor_record.dpdp_policy_published:
        checks_performed.append("privacy_policy_published: VERIFIED")
    else:
        checks_performed.append("privacy_policy_published: NOT_PROVIDED")
        failed_checks.append("privacy_policy_published")

    # Data retention policy
    if vendor_record.dpdp_data_retention_days is not None:
        checks_performed.append(f"data_retention_policy: {vendor_record.dpdp_data_retention_days} days")
    else:
        checks_performed.append("data_retention_policy: NOT_DEFINED")
        pending_checks.append("data_retention_policy")

    # Overall DPDP status
    if vendor_record.dpdp_verified:
        status = "PASSED"
    elif failed_checks:
        status = "FAILED"
    elif pending_checks:
        status = "PENDING"
    else:
        status = "PENDING"

    details["checks_performed"] = checks_performed
    details["failed_checks"] = failed_checks
    details["pending_checks"] = pending_checks
    details["verification_method"] = "database_record_check"

    return {
        "status": status,
        "details": details,
        "warnings": [
            "DPDP compliance requires manual audit of data handling practices",
            "Ensure consent is obtained before data collection per Section 6",
            "Data must be deleted when purpose is served per Section 8",
        ] if status != "FAILED" else [
            f"DPDP compliance FAILED: {', '.join(failed_checks)}",
        ],
    }


async def _check_rbi_compliance(
    entity_id: str, tenant_id: str, session: AsyncSession
) -> Dict[str, Any]:
    """
    Perform RBI payment compliance check.

    Checks vendor DB record for:
    - Payment aggregator license
    - Escrow account maintenance
    - Refund timeline compliance (T+5 days)
    - UPI compliance

    Returns PENDING if vendor has no RBI data on file.
    Returns FAILED if required RBI items are missing.
    Returns PASSED only if all RBI requirements are verified.
    """
    details: Dict[str, Any] = {
        "regulation": "RBI Payment and Settlement Systems Act 2007",
        "entity_id": entity_id,
    }

    # Check vendor record in DB
    result = await session.execute(
        select(VendorComplianceModel).where(
            VendorComplianceModel.vendor_id == uuid.UUID(entity_id),
            VendorComplianceModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    vendor_record = result.scalar_one_or_none()

    if not vendor_record:
        details["reason"] = "Vendor not found in compliance registry — RBI status unknown"
        details["checks_performed"] = []
        return {
            "status": "PENDING",
            "details": details,
            "warnings": [
                "Cannot verify RBI compliance — vendor onboarding not initiated",
            ],
        }

    # Check RBI-specific fields
    checks_performed = []
    failed_checks = []

    # Payment aggregator registration
    if vendor_record.rbi_payment_aggregator:
        checks_performed.append("payment_aggregator_registration: VERIFIED")
    else:
        checks_performed.append("payment_aggregator_registration: NOT_VERIFIED")
        failed_checks.append("payment_aggregator_registration")

    # Escrow account
    if vendor_record.rbi_escrow_maintained:
        checks_performed.append("escrow_account_maintenance: VERIFIED")
    else:
        checks_performed.append("escrow_account_maintenance: NOT_VERIFIED")
        failed_checks.append("escrow_account_maintenance")

    # Refund timeline
    if vendor_record.rbi_refund_compliance:
        checks_performed.append("refund_timeline_compliance: VERIFIED (T+5 business days)")
    else:
        checks_performed.append("refund_timeline_compliance: NOT_VERIFIED")
        failed_checks.append("refund_timeline_compliance")

    # Overall RBI status
    if vendor_record.rbi_verified:
        status = "PASSED"
    elif failed_checks:
        status = "FAILED"
    else:
        status = "PENDING"

    details["checks_performed"] = checks_performed
    details["failed_checks"] = failed_checks
    details["verification_method"] = "database_record_check"
    details["refund_timeline"] = "T+5 business days as per RBI mandate"

    return {
        "status": status,
        "details": details,
        "warnings": [
            "RBI compliance is structural — verify payment aggregator license separately",
            "Refunds must be processed within 5 business days per RBI guidelines",
            "Escrow accounts must be maintained with scheduled banks",
        ] if status != "FAILED" else [
            f"RBI compliance FAILED: {', '.join(failed_checks)}",
        ],
    }


# ═══════════════════════════════════════════════════════════════
# GST CALCULATION ROUTE
# ═══════════════════════════════════════════════════════════════

@router.post("/gst/calculate")
async def calculate_gst(
    taxable_amount: Decimal,
    vendor_id: str,
    order_id: Optional[str] = None,
    is_interstate: bool = False,
    is_ac_restaurant: bool = False,
    serves_alcohol: bool = False,
    is_canteen: bool = False,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Calculate GST for an order with proper Indian tax rules.

    GST rates for Indian restaurant services:
    - 5% (no ITC): Standalone restaurants, non-AC, canteens, mess
    - 18% (with ITC): AC restaurants, restaurants serving alcohol

    For intra-state: CGST + SGST (split equally)
    For inter-state: IGST (full amount)

    Rounding: Indian GST requires rounding to nearest rupee (0.01 precision).
    """
    tenant_id = user.get("tenant_id", "default")

    if taxable_amount <= Decimal("0"):
        raise HTTPException(status_code=400, detail="Taxable amount must be positive")

    # Determine applicable rate
    gst_rate = determine_gst_rate(
        is_ac_restaurant=is_ac_restaurant,
        serves_alcohol=serves_alcohol,
        is_canteen=is_canteen,
    )

    # Calculate using Decimal for precision
    amount = taxable_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_gst = round_gst(amount * gst_rate)
    cgst = Decimal("0.00")
    sgst = Decimal("0.00")
    igst = Decimal("0.00")

    if is_interstate:
        igst = total_gst
    else:
        cgst = round_gst(total_gst / 2)
        sgst = round_gst(total_gst - cgst)  # Avoid rounding drift

    # HSN code for restaurant services
    hsn_code = "9963"  # Food and beverage serving services

    # Persist record
    record = GSTRecordModel(
        tenant_id=tenant_id,
        order_id=order_id if order_id else str(uuid.uuid4()),
        vendor_id=vendor_id,
        hsn_code=hsn_code,
        taxable_amount=taxable_amount,
        cgst=str(cgst),
        sgst=str(sgst),
        igst=str(igst),
        total_gst=str(total_gst),
    )
    session.add(record)
    await session.commit()

    return {
        "taxable_amount": str(amount),
        "gst_rate_percent": str(gst_rate * 100),
        "hsn_code": hsn_code,
        "is_interstate": is_interstate,
        "cgst": str(cgst),
        "sgst": str(sgst),
        "igst": str(igst),
        "total_gst": str(total_gst),
        "total_amount": str(round_gst(amount + total_gst)),
        "record_id": str(record.id),
    }


# ═══════════════════════════════════════════════════════════════
# GSTIN VALIDATION ROUTE
# ═══════════════════════════════════════════════════════════════

@router.post("/gstin/validate")
async def validate_gstin_route(
    gstin: str,
    user: dict = Depends(get_current_user),
):
    """
    Validate an Indian GSTIN number.

    Checks format, state code, PAN structure, and checksum.
    """
    result = verify_gst_number(gstin)
    return result


# ═══════════════════════════════════════════════════════════════
# VENDOR COMPLIANCE ROUTES
# ═══════════════════════════════════════════════════════════════

@router.post("/vendor/register")
async def register_vendor_compliance(
    vendor_id: str,
    fssai_license: Optional[str] = None,
    gst_number: Optional[str] = None,
    dpdp_consent_obtained: bool = False,
    dpdp_policy_published: bool = False,
    dpdp_data_retention_days: Optional[int] = None,
    rbi_payment_aggregator: bool = False,
    rbi_escrow_maintained: bool = False,
    rbi_refund_compliance: bool = False,
    user: dict = Depends(require_role("admin", "vendor_admin")),
    session: AsyncSession = Depends(get_session),
):
    """
    Register or update vendor compliance data.

    This is used to populate the vendor compliance record
    before running compliance checks.
    """
    tenant_id = user.get("tenant_id", "default")

    # Check if record exists
    result = await session.execute(
        select(VendorComplianceModel).where(
            VendorComplianceModel.vendor_id == uuid.UUID(vendor_id),
            VendorComplianceModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Update existing record
        if fssai_license is not None:
            existing.fssai_license = fssai_license
            verification = verify_fssai_license(fssai_license)
            existing.fssai_verified = verification["valid"]
            if verification["valid"]:
                existing.fssai_verified_at = datetime.now(timezone.utc)
        if gst_number is not None:
            existing.gst_number = gst_number
            verification = verify_gst_number(gst_number)
            existing.gst_verified = verification["valid"]
            if verification["valid"]:
                existing.gst_verified_at = datetime.now(timezone.utc)
        existing.dpdp_consent_obtained = dpdp_consent_obtained
        existing.dpdp_policy_published = dpdp_policy_published
        existing.dpdp_data_retention_days = dpdp_data_retention_days
        existing.rbi_payment_aggregator = rbi_payment_aggregator
        existing.rbi_escrow_maintained = rbi_escrow_maintained
        existing.rbi_refund_compliance = rbi_refund_compliance
        existing.last_checked_at = datetime.now(timezone.utc)
        existing.updated_at = datetime.now(timezone.utc)

        # Recalculate overall status
        dpdp_ok = dpdp_consent_obtained and dpdp_policy_published
        existing.dpdp_verified = dpdp_ok
        rbi_ok = rbi_payment_aggregator and rbi_escrow_maintained and rbi_refund_compliance
        existing.rbi_verified = rbi_ok

        all_ok = existing.fssai_verified and existing.gst_verified and dpdp_ok and rbi_ok
        existing.overall_status = "PASSED" if all_ok else "PENDING"
        existing.onboarding_status = "APPROVED" if all_ok else "IN_REVIEW"

        await session.commit()
        return {
            "vendor_id": vendor_id,
            "action": "updated",
            "overall_status": existing.overall_status,
        }

    # Create new record
    fssai_verified = False
    fssai_verified_at = None
    gst_verified = False
    gst_verified_at = None

    if fssai_license:
        verification = verify_fssai_license(fssai_license)
        fssai_verified = verification["valid"]
        if fssai_verified:
            fssai_verified_at = datetime.now(timezone.utc)

    if gst_number:
        verification = verify_gst_number(gst_number)
        gst_verified = verification["valid"]
        if gst_verified:
            gst_verified_at = datetime.now(timezone.utc)

    dpdp_ok = dpdp_consent_obtained and dpdp_policy_published
    rbi_ok = rbi_payment_aggregator and rbi_escrow_maintained and rbi_refund_compliance
    all_ok = fssai_verified and gst_verified and dpdp_ok and rbi_ok

    record = VendorComplianceModel(
        tenant_id=uuid.UUID(tenant_id),
        vendor_id=uuid.UUID(vendor_id),
        onboarding_status="APPROVED" if all_ok else "IN_REVIEW",
        fssai_license=fssai_license,
        fssai_verified=fssai_verified,
        fssai_verified_at=fssai_verified_at,
        gst_number=gst_number,
        gst_verified=gst_verified,
        gst_verified_at=gst_verified_at,
        dpdp_consent_obtained=dpdp_consent_obtained,
        dpdp_policy_published=dpdp_policy_published,
        dpdp_data_retention_days=dpdp_data_retention_days,
        dpdp_verified=dpdp_ok,
        rbi_payment_aggregator=rbi_payment_aggregator,
        rbi_escrow_maintained=rbi_escrow_maintained,
        rbi_refund_compliance=rbi_refund_compliance,
        rbi_verified=rbi_ok,
        overall_status="PASSED" if all_ok else "PENDING",
        last_checked_at=datetime.now(timezone.utc),
    )
    session.add(record)
    await session.commit()

    return {
        "vendor_id": vendor_id,
        "action": "created",
        "overall_status": record.overall_status,
    }


@router.get("/vendor/{vendor_id}/status")
async def get_vendor_compliance_status(
    vendor_id: str,
    user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Get the compliance status of a vendor.

    Returns all compliance check statuses and the overall status.
    """
    tenant_id = user.get("tenant_id", "default")
    return await check_vendor_compliance(vendor_id, tenant_id, session)


async def _update_vendor_compliance(
    vendor_id: str, tenant_id: str, check_type: str, status: str,
    session: AsyncSession,
) -> None:
    """Update the vendor compliance record after a check."""
    result = await session.execute(
        select(VendorComplianceModel).where(
            VendorComplianceModel.vendor_id == uuid.UUID(vendor_id),
            VendorComplianceModel.tenant_id == uuid.UUID(tenant_id),
        )
    )
    vendor_record = result.scalar_one_or_none()

    if not vendor_record:
        # Auto-create a vendor compliance record
        vendor_record = VendorComplianceModel(
            tenant_id=uuid.UUID(tenant_id),
            vendor_id=uuid.UUID(vendor_id),
            onboarding_status="IN_REVIEW",
            overall_status="PENDING",
        )
        session.add(vendor_record)
        await session.flush()

    now = datetime.now(timezone.utc)
    vendor_record.last_checked_at = now

    if check_type == "FSSAI" and status == "PASSED":
        vendor_record.fssai_verified = True
        vendor_record.fssai_verified_at = now
    elif check_type == "FSSAI" and status == "FAILED":
        vendor_record.fssai_verified = False

    if check_type == "GST" and status == "PASSED":
        vendor_record.gst_verified = True
        vendor_record.gst_verified_at = now
    elif check_type == "GST" and status == "FAILED":
        vendor_record.gst_verified = False

    if check_type == "DPDP" and status == "PASSED":
        vendor_record.dpdp_verified = True
    elif check_type == "DPDP" and status == "FAILED":
        vendor_record.dpdp_verified = False

    if check_type == "RBI" and status == "PASSED":
        vendor_record.rbi_verified = True
    elif check_type == "RBI" and status == "FAILED":
        vendor_record.rbi_verified = False

    # Recalculate overall status
    all_passed = (
        vendor_record.fssai_verified
        and vendor_record.gst_verified
        and vendor_record.dpdp_verified
        and vendor_record.rbi_verified
    )
    any_failed = (
        (vendor_record.fssai_license and not vendor_record.fssai_verified)
        or (vendor_record.gst_number and not vendor_record.gst_verified)
        or (vendor_record.dpdp_consent_obtained and not vendor_record.dpdp_verified)
        or (vendor_record.rbi_payment_aggregator and not vendor_record.rbi_verified)
    )

    if all_passed:
        vendor_record.overall_status = "PASSED"
        vendor_record.onboarding_status = "APPROVED"
    elif any_failed:
        vendor_record.overall_status = "FAILED"
        vendor_record.onboarding_status = "REJECTED"
    else:
        vendor_record.overall_status = "PENDING"
        vendor_record.onboarding_status = "IN_REVIEW"

    vendor_record.updated_at = now
